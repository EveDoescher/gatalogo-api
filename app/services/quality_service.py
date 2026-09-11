import cv2
import numpy as np

from app.config import Settings
from app.models.vision import ImageQualityMetrics


class QualityService:
    """Mede qualidade técnica sem fazer suposições sobre a pelagem."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def analyze(self, image: np.ndarray) -> ImageQualityMetrics:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(gray.mean())
        contrast = float(gray.std())
        underexposed_ratio = float((gray <= 15).mean())
        overexposed_ratio = float((gray >= 240).mean())

        # Não rejeitamos fotos comuns por desfoque isolado: pelo curto e liso
        # naturalmente tem menos bordas. O hard reject exige baixa informação
        # visual combinada a exposição quase totalmente extrema.
        extreme_exposure = max(
            underexposed_ratio,
            overexposed_ratio,
        ) >= self.settings.max_extreme_exposure_ratio
        low_detail = (
            blur_score < self.settings.min_blur_score
            and contrast < self.settings.min_contrast
        )

        return ImageQualityMetrics(
            width=int(image.shape[1]),
            height=int(image.shape[0]),
            blur_score=round(blur_score, 2),
            brightness=round(brightness, 2),
            contrast=round(contrast, 2),
            underexposed_ratio=round(underexposed_ratio, 4),
            overexposed_ratio=round(overexposed_ratio, 4),
            usable=not (extreme_exposure and low_detail),
        )
