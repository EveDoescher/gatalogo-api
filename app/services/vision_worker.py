"""Consumidor em CPU para SAM 2 + DINOv2.

O processo web não importa este módulo. O worker é intencionalmente separado
porque a segmentação e os embeddings em CPU são demorados; a API continua
disponível enquanto os trabalhos persistidos são processados em segundo plano.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib

import numpy as np
from PIL import Image, ImageOps
from sqlalchemy import case, delete, or_, select, update

from app.config import Settings, get_settings
from app.database import get_session_factory
from app.persistence_models import CatPhoto, CatVisualEmbedding, RemoteCat, VisionJob
from app.services.recognition_service import refresh_cat_matches, is_solid_analysis
from app.services.realtime_events import publish_committed_events
from app.services.recognition_features import foreground_quality, local_features, model_version, perceptual_hash, prepare_embedding_image
from app.services.preprocessing_service import PreprocessingService
from app.services.dense_features import encode_dense_features
from app.services.segmentation_service import SegmentationService
from app.services.storage_service import photo_storage


class Sam2DinoWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._torch = None
        self._dino = None
        self._segmenter = None
        self._device = "cpu"

    def _load_models(self) -> None:
        if self._dino is not None:
            return
        import torch

        self._torch = torch
        # O worker não usa CUDA: a GT 610 não é suportada por CUDA moderno.
        worker_settings = self.settings.model_copy(update={"vision_device": self._device})
        self._segmenter = SegmentationService(worker_settings)
        torch.hub.set_dir(worker_settings.model_cache_dir)
        self._dino = (
            torch.hub.load("facebookresearch/dinov2", self.settings.dinov2_model_name)
            .eval()
            .to(self._device)
        )

    def _primary_cat_crop(self, image: Image.Image) -> Image.Image:
        crop, _, _, _ = self._segment(image)
        return crop

    def _segment(self, image: Image.Image):
        self._load_models()
        # Benchmark and queued uploads follow the same bounded, EXIF-aware path.
        if image.width * image.height > self.settings.max_image_pixels:
            raise ValueError("A imagem possui pixels demais para reconhecimento.")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((self.settings.vision_max_side, self.settings.vision_max_side), Image.Resampling.LANCZOS)
        image_bgr = np.asarray(image)[:, :, ::-1].copy()
        segmented = self._segmenter.segment_cat(image_bgr)
        if segmented.ambiguous_multiple_cats:
            raise ValueError("Há mais de um gato em destaque na imagem.")
        if segmented.cat_crop is None or segmented.mask is None or segmented.bbox is None:
            raise RuntimeError("SAM 2 não encontrou um gato na imagem.")

        left, top, right, bottom = segmented.bbox
        masked = image_bgr[top:bottom, left:right].copy()
        mask = segmented.mask[top:bottom, left:right] > 0
        # Fundo neutro impede que cenário, chão ou vegetação dominem o DINOv2.
        masked[~mask] = (255, 255, 255)
        crop_bgr = image_bgr[top:bottom, left:right].copy()
        metrics = foreground_quality(crop_bgr, mask, self.settings)
        if not metrics.usable or not mask.any():
            raise ValueError("Foto sem informação visual suficiente para reconhecimento.")
        # Technical usability, not a probability of correct recognition. Low
        # texture alone must not reject a healthy smooth-coated cat.
        quality = min(1.0, min(crop_bgr.shape[:2]) / 256)
        quality *= min(1.0, max(0.0, float(segmented.detector_confidence or 0)))
        if segmented.detector_fallback_used:
            quality *= 0.75
        if metrics.blur_score < self.settings.min_blur_score and metrics.contrast < self.settings.min_contrast:
            quality *= 0.5
        features = {"quality": metrics.model_dump(), "detector_fallback": segmented.detector_fallback_used,
                    "local": local_features(crop_bgr, mask)}
        return Image.fromarray(masked[:, :, ::-1]), quality, features, mask

    def embedding(self, image: Image.Image) -> list[float]:
        vector, _ = self._dino_features(image)
        return vector

    def _dino_features(self, image: Image.Image):
        self._load_models()
        torch = self._torch
        prepared = prepare_embedding_image(image)
        data = np.asarray(prepared, dtype=np.float32) / 255.0
        data = (data - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array([0.229, 0.224, 0.225], dtype=np.float32)
        tensor = torch.from_numpy(data).permute(2, 0, 1).unsqueeze(0).to(self._device)
        with torch.inference_mode():
            output = self._dino.forward_features(tensor)
            vector = output["x_norm_clstoken"].squeeze(0).float().cpu().numpy()
            patches = output["x_norm_patchtokens"].squeeze(0).float().cpu().numpy()
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(vector).all() or norm <= 1e-12:
            raise ValueError("Modelo retornou vetor inválido.")
        vector = vector / norm
        if vector.size != 384:
            raise RuntimeError(f"DINOv2 retornou dimensão inesperada: {vector.size}.")
        return vector.tolist(), patches

    def extract_embedding(self, image: Image.Image) -> list[float]:
        """Entrada pública do benchmark: SAM 2 recorta, DINOv2 vetoriza."""
        return self.embedding(self._primary_cat_crop(image))

    def extract_evidence(self, image: Image.Image) -> dict:
        crop, quality, features, mask = self._segment(image)
        features["perceptual_hash"] = perceptual_hash(crop)
        vector, patches = self._dino_features(crop)
        features["dense"] = encode_dense_features(patches, mask)
        return {"embedding": vector, "quality_score": quality,
                "features": features, "model_version": model_version(self.settings)}

    def extract_bytes(self, data: bytes) -> dict:
        prepared = PreprocessingService(self.settings).prepare(data)
        image = Image.fromarray(prepared.vision_image[:, :, ::-1])
        return self.extract_evidence(image)

    async def process(self, job_id) -> None:
        factory = get_session_factory()
        async with factory() as session:
            job = await session.get(VisionJob, job_id)
            if job is None or job.status not in {"pending", "running"}:
                return
            claim_started_at = job.started_at
            cat = await session.get(RemoteCat, job.cat_id)
            photo = await session.get(CatPhoto, job.photo_id) if job.photo_id else None
            if cat is None or cat.deleted_at is not None or photo is None or photo.deleted_at is not None:
                job.status, job.error_message, job.completed_at = "failed", "Foto não disponível para reconhecimento.", datetime.now(UTC)
                await session.commit()
                return
            try:
                expected_hash = job.photo_hash or photo.content_hash
                if expected_hash != photo.content_hash:
                    job.status, job.completed_at = "superseded", datetime.now(UTC)
                    await session.commit()
                    return
                with photo_storage.open(photo.storage_key).open("rb") as source:
                    data = source.read(self.settings.max_image_bytes + 1)
                if hashlib.sha256(data).hexdigest() != expected_hash:
                    raise ValueError("O conteúdo da foto mudou durante o processamento.")
                evidence = await asyncio.to_thread(self.extract_bytes, data)
                await session.refresh(job, with_for_update=True)
                if job.started_at != claim_started_at or job.status not in {"pending", "running"}:
                    return
                # Lock and reread after CPU work: an upload may have replaced or
                # deleted this photo while inference was running.
                await session.refresh(cat, with_for_update=True)
                await session.refresh(photo, with_for_update=True)
                if cat.deleted_at is not None or photo.deleted_at is not None or photo.content_hash != expected_hash:
                    job.status, job.completed_at = "superseded", datetime.now(UTC)
                    await session.commit()
                    return
                async with session.begin_nested():
                    await session.execute(delete(CatVisualEmbedding).where(CatVisualEmbedding.photo_id == photo.id))
                    session.add(CatVisualEmbedding(
                        user_id=job.user_id, cat_id=cat.id, photo_id=photo.id, region="body",
                        view=photo.kind if photo.kind in {"front", "left", "right", "back"} else "unknown",
                        photo_hash=expected_hash, is_solid_coat=is_solid_analysis(cat.analysis), **evidence,
                    ))
                    await session.flush()
                    await refresh_cat_matches(session, cat=cat, settings=self.settings)
                job.status, job.error_message, job.completed_at = "completed", None, datetime.now(UTC)
            except Exception as error:
                await session.rollback()
                job = await session.get(VisionJob, job_id)
                if job is None or job.started_at != claim_started_at or job.status not in {"pending", "running"}:
                    return
                job.attempts += 1
                job.error_message = str(error)[:2000]
                job.status = "failed" if job.attempts >= self.settings.vision_job_max_attempts else "pending"
                job.completed_at = datetime.now(UTC) if job.status == "failed" else None
            await session.commit()
            await publish_committed_events(session)

    async def run_forever(self) -> None:
        factory = get_session_factory()
        while True:
            job_id = None
            async with factory() as session:
                # A crashed worker must not strand running jobs indefinitely.
                cutoff = datetime.now(UTC) - timedelta(seconds=self.settings.vision_job_lease_seconds)
                await session.execute(update(VisionJob).where(
                    VisionJob.status == "running",
                    or_(
                        VisionJob.started_at.is_(None),
                        VisionJob.started_at < cutoff,
                    ),
                ).values(
                    status=case((VisionJob.attempts + 1 >= self.settings.vision_job_max_attempts, "failed"), else_="pending"),
                    attempts=VisionJob.attempts + 1, started_at=None,
                    error_message="O worker excedeu o prazo de processamento.",
                    completed_at=case((VisionJob.attempts + 1 >= self.settings.vision_job_max_attempts, datetime.now(UTC)), else_=None),
                ))
                job = (await session.execute(
                    select(VisionJob).where(VisionJob.status == "pending").order_by(VisionJob.created_at).with_for_update(skip_locked=True).limit(1)
                )).scalar_one_or_none()
                if job is not None:
                    job.status, job.started_at = "running", datetime.now(UTC)
                    job_id = job.id
                await session.commit()
            if job_id is None:
                await asyncio.sleep(self.settings.vision_worker_poll_seconds)
            else:
                await self.process(job_id)


def main() -> None:
    asyncio.run(Sam2DinoWorker(get_settings()).run_forever())


if __name__ == "__main__":
    main()
