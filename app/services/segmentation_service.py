from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

import cv2
import numpy as np

from app.config import Settings
from app.exceptions import SegmentationError
from app.models.vision import SegmentationResult


_inference_lock = Lock()


@dataclass(frozen=True)
class CatDetection:
    bbox: tuple[int, int, int, int]
    confidence: float


class CatDetector(Protocol):
    def detect(self, image: np.ndarray) -> list[CatDetection]: ...


class MaskRefiner(Protocol):
    def refine(
        self,
        image: np.ndarray,
        bbox: tuple[int, int, int, int],
    ) -> np.ndarray: ...


class TorchvisionCatDetector:
    """Encontra gatos com Faster R-CNN treinado no COCO."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def detect(self, image: np.ndarray) -> list[CatDetection]:
        model, categories = _load_detector(
            self.settings.model_cache_dir,
            self.settings.detector_confidence,
            self.settings.vision_device,
        )
        try:
            import torch

            image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            tensor = torch.from_numpy(
                np.ascontiguousarray(image_rgb)
            ).permute(2, 0, 1).float() / 255.0
            tensor = tensor.to(self.settings.vision_device)

            with _inference_lock, torch.inference_mode():
                prediction = model([tensor])[0]

            boxes = prediction["boxes"].detach().cpu().numpy()
            labels = prediction["labels"].detach().cpu().numpy()
            scores = prediction["scores"].detach().cpu().numpy()

            detections: list[CatDetection] = []
            for box, label, score in zip(boxes, labels, scores, strict=True):
                if categories[int(label)] != "cat":
                    continue
                detections.append(
                    CatDetection(
                        bbox=_clamp_bbox(box, image.shape[:2]),
                        confidence=float(score),
                    )
                )

            return detections
        except SegmentationError:
            raise
        except Exception as error:
            raise SegmentationError(
                "O detector local não pôde analisar a imagem."
            ) from error


class Sam2MaskRefiner:
    """Refina a caixa do detector em uma máscara precisa com SAM 2."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def refine(
        self,
        image: np.ndarray,
        bbox: tuple[int, int, int, int],
    ) -> np.ndarray:
        predictor = _load_sam2_predictor(
            self.settings.sam2_config,
            self.settings.sam2_checkpoint,
            self.settings.vision_device,
        )
        try:
            import torch

            image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            with _inference_lock, torch.inference_mode():
                predictor.set_image(image_rgb)
                masks, scores, _ = predictor.predict(
                    box=np.asarray(bbox, dtype=np.float32),
                    multimask_output=True,
                )

            return masks[int(np.argmax(scores))] > 0
        except SegmentationError:
            raise
        except Exception as error:
            raise SegmentationError(
                "O SAM 2 não pôde refinar a máscara do gato."
            ) from error


class SegmentationService:
    """Combina detector aberto + SAM 2 para obter a máscara do gato."""

    def __init__(
        self,
        settings: Settings,
        *,
        detector: CatDetector | None = None,
        refiner: MaskRefiner | None = None,
        fallback_detector: CatDetector | None = None,
    ) -> None:
        self.settings = settings
        detector_was_created = detector is None
        self.detector = detector or TorchvisionCatDetector(settings)
        self.refiner = refiner or Sam2MaskRefiner(settings)
        if fallback_detector is not None:
            self.fallback_detector = fallback_detector
        elif detector_was_created and (
            0 < settings.detector_fallback_confidence
            < settings.detector_confidence
        ):
            fallback_settings = settings.model_copy(
                update={
                    "detector_confidence": settings.detector_fallback_confidence,
                }
            )
            self.fallback_detector = TorchvisionCatDetector(fallback_settings)
        else:
            self.fallback_detector = None

    def segment_cat(self, image: np.ndarray) -> SegmentationResult:
        detections = self.detector.detect(image)
        detector_fallback_used = False
        if not detections:
            if self.fallback_detector is not None:
                detections = self.fallback_detector.detect(image)
                detector_fallback_used = bool(detections)
            if not detections:
                return self._empty_result()

        warnings: list[str] = []
        if detector_fallback_used:
            warnings.append(
                "O gato foi encontrado em uma segunda tentativa com limiar "
                "menor; revise a máscara e os percentuais de cor."
            )

        detections.sort(key=lambda item: _area(item.bbox), reverse=True)
        primary = detections[0]
        if primary.confidence < self.settings.detector_confidence:
            warnings.append(
                "A detecção do gato tem confiança baixa; a imagem deve ser "
                "revisada manualmente."
            )

        ambiguous_multiple_cats = self._has_ambiguous_primary(detections)
        if ambiguous_multiple_cats:
            return SegmentationResult(
                cat_count=len(detections),
                detector_confidence=round(primary.confidence, 4),
                mask=None,
                bbox=primary.bbox,
                cat_crop=None,
                ambiguous_multiple_cats=True,
                detector_fallback_used=detector_fallback_used,
                warnings=tuple(warnings),
            )

        if len(detections) > 1:
            warnings.append(
                "Foi encontrada uma segunda detecção menor; verifique se há "
                "outro gato ou uma oclusão na imagem."
            )

        mask = self.refiner.refine(image, primary.bbox)
        mask = self._normalize_mask(mask, image.shape[:2])
        if not mask.any():
            raise SegmentationError(
                "O SAM 2 retornou uma máscara vazia para o gato detectado."
            )

        bbox = self._bbox(mask)
        return SegmentationResult(
            cat_count=len(detections),
            detector_confidence=round(primary.confidence, 4),
            mask=(mask.astype(np.uint8) * 255),
            bbox=bbox,
            cat_crop=self._crop(image, bbox),
            detector_fallback_used=detector_fallback_used,
            warnings=tuple(warnings),
        )

    def _has_ambiguous_primary(
        self,
        detections: list[CatDetection],
    ) -> bool:
        return len(detections) > 1 and (
            _area(detections[1].bbox) / _area(detections[0].bbox)
            >= self.settings.secondary_cat_max_area_ratio
        )

    def _crop(
        self,
        image: np.ndarray,
        bbox: tuple[int, int, int, int],
    ) -> np.ndarray:
        x1, y1, x2, y2 = bbox
        padding = round(
            max(x2 - x1, y2 - y1)
            * self.settings.cat_crop_padding_ratio
        )
        height, width = image.shape[:2]
        return image[
            max(0, y1 - padding):min(height, y2 + padding),
            max(0, x1 - padding):min(width, x2 + padding),
        ].copy()

    @staticmethod
    def _empty_result() -> SegmentationResult:
        return SegmentationResult(
            cat_count=0,
            detector_confidence=None,
            mask=None,
            bbox=None,
            cat_crop=None,
        )

    @staticmethod
    def _bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
        y_coordinates, x_coordinates = np.where(mask)
        return (
            int(x_coordinates.min()),
            int(y_coordinates.min()),
            int(x_coordinates.max()) + 1,
            int(y_coordinates.max()) + 1,
        )

    @staticmethod
    def _normalize_mask(
        mask: np.ndarray,
        image_shape: tuple[int, int],
    ) -> np.ndarray:
        if mask.shape != image_shape:
            mask = cv2.resize(
                mask.astype(np.uint8),
                (image_shape[1], image_shape[0]),
                interpolation=cv2.INTER_NEAREST,
            )
        return mask > 0


@lru_cache
def _load_detector(
    cache_dir: str,
    confidence: float,
    device: str,
) -> tuple[Any, list[str]]:
    try:
        import torch
        from torchvision.models.detection import (
            FasterRCNN_MobileNet_V3_Large_320_FPN_Weights,
            fasterrcnn_mobilenet_v3_large_320_fpn,
        )

        cache_path = Path(cache_dir).resolve()
        cache_path.mkdir(parents=True, exist_ok=True)
        torch.hub.set_dir(str(cache_path))
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise SegmentationError(
                "VISION_DEVICE está configurado para CUDA, mas CUDA não "
                "está disponível."
            )

        weights = FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.DEFAULT
        model = fasterrcnn_mobilenet_v3_large_320_fpn(
            weights=weights,
            box_score_thresh=confidence,
        )
        model.to(device).eval()
        return model, list(weights.meta["categories"])
    except SegmentationError:
        raise
    except Exception as error:
        raise SegmentationError(
            "Não foi possível carregar o detector aberto de gatos."
        ) from error


@lru_cache
def _load_sam2_predictor(
    config: str,
    checkpoint: str,
    device: str,
) -> Any:
    checkpoint_path = Path(checkpoint)
    if not checkpoint_path.is_file():
        raise SegmentationError(
            "O checkpoint do SAM 2 não foi encontrado em "
            f"{checkpoint_path}. Configure SAM2_CHECKPOINT."
        )

    try:
        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor

        return SAM2ImagePredictor(
            build_sam2(config, str(checkpoint_path), device=device)
        )
    except SegmentationError:
        raise
    except Exception as error:
        raise SegmentationError(
            "Não foi possível carregar o checkpoint do SAM 2."
        ) from error


def _clamp_bbox(
    box: np.ndarray,
    image_shape: tuple[int, int],
) -> tuple[int, int, int, int]:
    height, width = image_shape
    x1, y1, x2, y2 = box.astype(int)
    x1, x2 = sorted((max(0, x1), min(width, x2)))
    y1, y2 = sorted((max(0, y1), min(height, y2)))
    if x1 >= x2 or y1 >= y2:
        raise SegmentationError("O detector retornou uma caixa de gato inválida.")
    return x1, y1, x2, y2


def _area(bbox: tuple[int, int, int, int]) -> int:
    x1, y1, x2, y2 = bbox
    return (x2 - x1) * (y2 - y1)
