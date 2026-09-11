from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel, Field


class ImageQualityMetrics(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    blur_score: float = Field(ge=0)
    brightness: float = Field(ge=0, le=255)
    contrast: float = Field(ge=0)
    underexposed_ratio: float = Field(ge=0, le=1)
    overexposed_ratio: float = Field(ge=0, le=1)
    usable: bool


class VisionColor(BaseModel):
    hex: str = Field(pattern=r"^#[0-9A-F]{6}$")
    percentage: float = Field(ge=0, le=100)
    # Agrupa amostras de cromia semelhante que diferem sobretudo pela luz.
    # É metadado interno: a fusão continua usando os percentuais medidos.
    shade_group: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class PreparedImage:
    """Representação normalizada usada apenas dentro do pipeline."""

    original_width: int
    original_height: int
    vision_image: np.ndarray


@dataclass(frozen=True)
class SegmentationResult:
    """Saída local da segmentação da instância principal do gato."""

    cat_count: int
    detector_confidence: float | None
    mask: np.ndarray | None
    bbox: tuple[int, int, int, int] | None
    cat_crop: np.ndarray | None
    ambiguous_multiple_cats: bool = False
    detector_fallback_used: bool = False
    warnings: tuple[str, ...] = ()
