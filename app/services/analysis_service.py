import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from functools import lru_cache
from time import perf_counter, sleep
from typing import Any

import cv2
import numpy as np
from google import genai
from google.genai import types
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.exceptions import (
    GeminiAnalysisError,
    MissingApiKeyError,
    RejectedImageError,
)
from app.models.analysis import (
    CatAnalysisResponse,
    GeminiCoatAnalysis,
    PatternMap,
)
from app.prompts.coat_analysis import COAT_ANALYSIS_PROMPT
from app.services.color_service import ColorService
from app.services.analysis_cache import AnalysisCache
from app.services.fusion_service import FusionService
from app.services.preprocessing_service import PreprocessingService
from app.services.quality_service import QualityService
from app.services.segmentation_service import SegmentationService
from app.models.vision import VisionColor


logger = logging.getLogger(__name__)


@lru_cache
def _get_gemini_client(api_key: str) -> genai.Client:
    """Reutiliza a conexão HTTP do Gemini entre análises."""
    return genai.Client(api_key=api_key)


class AnalysisService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.preprocessing = PreprocessingService(self.settings)
        self.quality = QualityService(self.settings)
        self.segmentation = SegmentationService(self.settings)
        self.colors = ColorService(self.settings)
        self.fusion = FusionService()
        self.semantic_cache = AnalysisCache(self.settings.analysis_cache_dir)

    async def analyze(
        self,
        *,
        cat_id: str,
        image_bytes: bytes,
    ) -> CatAnalysisResponse:
        started_at = perf_counter()
        timings: dict[str, float] = {}
        prepared = await asyncio.to_thread(
            self.preprocessing.prepare,
            image_bytes,
        )
        timings["preprocessamento"] = perf_counter() - started_at
        quality = await asyncio.to_thread(
            self.quality.analyze,
            prepared.vision_image,
        )
        timings["qualidade"] = perf_counter() - started_at

        if not quality.usable:
            raise RejectedImageError(
                reason="LOW_QUALITY",
                message=(
                    "A imagem está excessivamente escura ou clara e sem "
                    "detalhes suficientes para análise."
                ),
            )

        segmentation = await asyncio.to_thread(
            self.segmentation.segment_cat,
            prepared.vision_image,
        )
        timings["segmentacao"] = perf_counter() - started_at
        if segmentation.cat_count == 0:
            raise RejectedImageError(
                reason="NOT_A_CAT",
                message="Nenhum gato foi identificado na imagem.",
            )

        if segmentation.ambiguous_multiple_cats:
            raise RejectedImageError(
                reason="MULTIPLE_CATS",
                message=(
                    "Há mais de um gato principal na imagem. Envie uma "
                    "foto com apenas um gato em destaque."
                ),
            )

        measured_colors = await asyncio.to_thread(
            self.colors.extract,
            prepared.vision_image,
            segmentation.mask,
        )
        timings["cores"] = perf_counter() - started_at

        semantic_context = self._build_semantic_context(measured_colors)
        local_result = self._try_local_semantic_shortcut(
            measured_colors,
            segmentation.detector_confidence,
            segmentation.cat_count,
            segmentation.warnings,
        )
        if local_result is not None:
            response_warnings = list(segmentation.warnings)
            response_warnings.append(
                "Classificação sem chamada externa: pelagem sólida de alta "
                "confiança."
            )
            result = local_result
            timings["semantica"] = perf_counter() - started_at
        else:
            encoded_image = self._encode_gemini_image(segmentation.cat_crop)
            cache_key = self._semantic_cache_key(encoded_image, semantic_context)
            cached_payload = await asyncio.to_thread(
                self.semantic_cache.get,
                cache_key,
            )
            if cached_payload:
                try:
                    result = GeminiCoatAnalysis.model_validate_json(cached_payload)
                    response_warnings = list(segmentation.warnings)
                    response_warnings.append(
                        "Resultado semântico reutilizado do cache local; "
                        "nenhuma chamada externa foi feita."
                    )
                    logger.info("Cache semântico local usado para %s", cache_key[:12])
                    timings["semantica"] = perf_counter() - started_at
                except ValidationError:
                    logger.warning("Entrada inválida no cache semântico; ignorando")
                    cached_payload = None

            if not cached_payload:
                if not self.settings.gemini_api_key:
                    raise MissingApiKeyError(
                        "GEMINI_API_KEY não foi configurada no arquivo .env."
                    )

                gemini_started_at = perf_counter()
                result = await asyncio.to_thread(
                    self._analyze_with_gemini,
                    encoded_image,
                    "image/jpeg",
                    semantic_context,
                )
                timings["gemini"] = perf_counter() - gemini_started_at
                timings["semantica"] = perf_counter() - started_at
                await asyncio.to_thread(
                    self.semantic_cache.set,
                    cache_key,
                    result.model_dump_json(),
                )
                response_warnings = list(segmentation.warnings)

        validation = result.validation

        if not validation.accepted:
            if validation.reason == "NOT_A_CAT":
                logger.warning(
                    "Gemini discordou da detecção local de gato; "
                    "classificando como imagem não identificável"
                )
                raise RejectedImageError(
                    reason="CAT_NOT_IDENTIFIABLE",
                    message=(
                        "Um gato foi localizado, mas está pequeno ou sem "
                        "detalhes suficientes para classificar a pelagem."
                    ),
                )
            raise RejectedImageError(
                reason=validation.reason,
                message=validation.message,
            )

        response = self.fusion.combine(
            cat_id=cat_id,
            measured_colors=measured_colors,
            semantic=result,
            warnings=response_warnings,
        )

        logger.info(
            "Análise concluída em %.2fs (original=%dx%d, visão=%dx%d, "
            "gatos=%d, cores_medidas=%d, confiança_detector=%.2f, etapas=%s)",
            perf_counter() - started_at,
            prepared.original_width,
            prepared.original_height,
            quality.width,
            quality.height,
            segmentation.cat_count,
            len(measured_colors),
            segmentation.detector_confidence or 0,
            ", ".join(
                f"{name}={elapsed:.2f}s" for name, elapsed in timings.items()
            ),
        )
        return response

    def _try_local_semantic_shortcut(
        self,
        measured_colors: list[VisionColor],
        detector_confidence: float | None,
        cat_count: int,
        segmentation_warnings: tuple[str, ...],
    ) -> GeminiCoatAnalysis | None:
        """Resolve apenas o caso muito seguro de pelagem sólida."""
        if not self.settings.local_semantic_gate_enabled:
            return None
        if cat_count != 1 or segmentation_warnings:
            return None
        if (
            detector_confidence is None
            or detector_confidence
            < self.settings.local_solid_min_detector_confidence
            or not measured_colors
        ):
            return None

        dominant = measured_colors[0]
        if dominant.percentage / 100 < self.settings.local_solid_dominance_threshold:
            return None

        color_name = self._color_name_from_hex(dominant.hex)
        if color_name == "Outro":
            return None

        return GeminiCoatAnalysis.model_validate(
            {
                "validation": {
                    "accepted": True,
                    "reason": "ACCEPTED",
                    "cat_count": 1,
                    "quality": "GOOD",
                    "message": "Classificação local de pelagem sólida.",
                },
                "coat_type": "Sólido",
                "color_assignments": [
                    {"cluster_index": index, "name": color_name}
                    for index in range(len(measured_colors))
                ],
                "confidence": 0.78,
                "pattern_map": PatternMap(
                    base_color=color_name,
                    pattern="Sólido",
                    accent_colors=[],
                    regions=[],
                ),
            }
        )

    @staticmethod
    def _color_name_from_hex(value: str) -> str:
        red = int(value[1:3], 16)
        green = int(value[3:5], 16)
        blue = int(value[5:7], 16)
        maximum = max(red, green, blue)
        minimum = min(red, green, blue)

        if maximum < 70:
            return "Preto"
        if minimum > 190 and maximum - minimum < 55:
            return "Branco"
        if maximum - minimum < 28:
            return "Cinza"
        if red > green * 1.25 and green > blue * 1.15:
            return "Laranja"
        if red > green * 1.08 and green > blue * 1.12 and red < 190:
            return "Marrom"
        if red > 170 and green > 135 and blue < 150:
            return "Creme"
        return "Outro"

    def _semantic_cache_key(self, image_bytes: bytes, semantic_context: str) -> str:
        digest = hashlib.sha256()
        digest.update(b"semantic-v2\\0")
        digest.update(self.settings.gemini_model.encode("utf-8"))
        digest.update(b"\\0")
        digest.update(COAT_ANALYSIS_PROMPT.encode("utf-8"))
        digest.update(b"\\0")
        digest.update(image_bytes)
        digest.update(b"\\0")
        digest.update(semantic_context.encode("utf-8"))
        return digest.hexdigest()

    def _encode_gemini_image(self, image: np.ndarray | None) -> bytes:
        if image is None:
            raise GeminiAnalysisError(
                "Não foi possível preparar o recorte do gato para análise."
            )

        height, width = image.shape[:2]
        max_side = max(height, width)
        if max_side > self.settings.gemini_max_side:
            scale = self.settings.gemini_max_side / max_side
            image = cv2.resize(
                image,
                (
                    round(width * scale),
                    round(height * scale),
                ),
                interpolation=cv2.INTER_AREA,
            )

        success, encoded = cv2.imencode(
            ".jpg",
            image,
            [
                int(cv2.IMWRITE_JPEG_QUALITY),
                self.settings.jpeg_quality,
            ],
        )
        if not success:
            raise GeminiAnalysisError(
                "Não foi possível preparar o recorte do gato para análise."
            )
        return encoded.tobytes()

    def _analyze_with_gemini(
        self,
        image_bytes: bytes,
        mime_type: str,
        semantic_context: str,
    ) -> GeminiCoatAnalysis:
        try:
            api_key = self.settings.gemini_api_key
            if not api_key:
                raise MissingApiKeyError(
                    "GEMINI_API_KEY não foi configurada no arquivo .env."
                )
            client = _get_gemini_client(api_key)

            response = self._generate_content_with_retry(
                client,
                contents=[
                    COAT_ANALYSIS_PROMPT,
                    semantic_context,
                    types.Part.from_bytes(
                        data=image_bytes,
                        mime_type=mime_type,
                    ),
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=GeminiCoatAnalysis,
                    max_output_tokens=self.settings.gemini_max_output_tokens,
                    automatic_function_calling=
                        types.AutomaticFunctionCallingConfig(
                            disable=True,
                        ),
                ),
            )

            if getattr(response, "parsed", None) is not None:
                parsed = response.parsed

                if isinstance(parsed, GeminiCoatAnalysis):
                    return parsed

                if isinstance(parsed, dict):
                    return GeminiCoatAnalysis.model_validate(parsed)

            text = getattr(response, "text", None)
            if text:
                return GeminiCoatAnalysis.model_validate_json(text)

            raise GeminiAnalysisError(
                "O Gemini não retornou conteúdo estruturado."
            )

        except MissingApiKeyError:
            raise
        except RejectedImageError:
            raise
        except GeminiAnalysisError:
            raise
        except ValidationError as error:
            raise GeminiAnalysisError(
                "A resposta do Gemini não corresponde ao schema esperado."
            ) from error
        except Exception as error:
            logger.exception("Resposta do Gemini inválida após a chamada")
            raise GeminiAnalysisError(
                "A resposta do Gemini não pôde ser interpretada "
                f"({type(error).__name__}: {self._safe_error_detail(error)})."
            ) from error

    def _generate_content_with_retry(
        self,
        client: Any,
        *,
        contents: list[Any],
        config: Any,
    ) -> Any:
        attempts = max(0, self.settings.gemini_max_retries) + 1
        for attempt in range(attempts):
            try:
                return client.models.generate_content(
                    model=self.settings.gemini_model,
                    contents=contents,
                    config=config,
                )
            except Exception as error:
                retrying = (
                    attempt + 1 < attempts
                    and self._is_retryable_gemini_error(error)
                )
                if retrying:
                    delay = 2 ** attempt
                    logger.warning(
                        "Gemini falhou (%s); nova tentativa em %ss "
                        "(%d/%d)",
                        self._safe_error_detail(error),
                        delay,
                        attempt + 1,
                        attempts - 1,
                    )
                    sleep(delay)
                    continue

                logger.exception(
                    "Falha na chamada ao Gemini (tentativas=%d)",
                    attempt + 1,
                )
                raise GeminiAnalysisError(
                    "Falha ao analisar a imagem com o Gemini "
                    f"({type(error).__name__}: "
                    f"{self._safe_error_detail(error)})."
                ) from error

        raise GeminiAnalysisError("Falha ao analisar a imagem com o Gemini.")

    @staticmethod
    def _is_retryable_gemini_error(error: Exception) -> bool:
        status = getattr(error, "status_code", None)
        if status is None:
            status = getattr(error, "code", None)
        try:
            if int(status) in {408, 409, 429, 500, 502, 503, 504}:
                return True
        except (TypeError, ValueError):
            pass

        detail = str(error).lower()
        return any(
            marker in detail
            for marker in (
                "timeout",
                "temporarily",
                "rate limit",
                "unavailable",
                "connecterror",
                "getaddrinfo",
            )
        )

    def _safe_error_detail(self, error: Exception) -> str:
        detail = str(error).strip().replace("\n", " ")
        if self.settings.gemini_api_key:
            detail = detail.replace(self.settings.gemini_api_key, "[redacted]")
        return (detail or "sem detalhes")[:300]

    @staticmethod
    def _build_semantic_context(measured_colors: list[VisionColor]) -> str:
        palette = "\n".join(
            f"- cluster_index={index}; hex={color.hex}; "
            f"coverage={color.percentage:.1f}%; shade_group={color.shade_group}"
            for index, color in enumerate(measured_colors)
        )
        return (
            "Medições locais da área mascarada do gato. Elas são a fonte "
            "de verdade para os percentuais; não os estime novamente. "
            "Clusters do mesmo shade_group têm cromia semelhante e variam "
            "principalmente por iluminação/sombra; não conte essa variação "
            "como uma cor adicional:\n"
            f"{palette}"
        )
