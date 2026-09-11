from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    Field,
    model_validator,
)


CoatType = Literal[
    "Sólido",
    "Bicolor",
    "Tricolor",
    "Rajado",
    "Tuxedo",
    "Escaminha",
    "Colorpoint",
    "Outro",
]

CoatColorName = Literal[
    "Preto",
    "Branco",
    "Cinza",
    "Laranja",
    "Marrom",
    "Creme",
    "Outro",
]

ValidationReason = Literal[
    "ACCEPTED",
    "NOT_A_CAT",
    "MULTIPLE_CATS",
    "LOW_QUALITY",
    "CAT_NOT_IDENTIFIABLE",
    "SEXUAL_CONTENT",
    "GRAPHIC_CONTENT",
    "UNSAFE_CONTENT",
]

ImageQuality = Literal[
    "GOOD",
    "MEDIUM",
    "LOW",
]

BodyRegion = Literal[
    "head_top",
    "face",
    "muzzle",
    "left_ear",
    "right_ear",
    "chest",
    "back",
    "left_flank",
    "right_flank",
    "belly",
    "front_left_leg",
    "front_right_leg",
    "rear_left_leg",
    "rear_right_leg",
    "tail",
]

RegionVisibility = Literal[
    "VISIBLE",
    "PARTIAL",
    "UNKNOWN",
]

MarkingType = Literal[
    "SOLID",
    "PATCH",
    "STRIPE",
    "SPOT",
    "POINT",
    "MOTTLED",
    "GRADIENT",
    "OTHER",
]

EdgeType = Literal[
    "HARD",
    "SOFT",
    "GRADIENT",
    "IRREGULAR",
    "UNKNOWN",
]


class CoatColor(BaseModel):
    name: CoatColorName
    percentage: float = Field(
        ge=0,
        le=100,
    )


class SemanticColorAssignment(BaseModel):
    cluster_index: int = Field(ge=0)
    name: CoatColorName


class ValidationResult(BaseModel):
    accepted: bool
    reason: ValidationReason

    cat_count: int = Field(
        ge=0,
    )

    quality: ImageQuality
    message: str


class RegionMarking(BaseModel):
    color: CoatColorName

    marking_type: MarkingType

    # Percentual aproximado desta marca
    # dentro da região visível.
    coverage: float = Field(
        ge=0,
        le=100,
    )

    edge: EdgeType


class CoatRegion(BaseModel):
    region: BodyRegion

    visibility: RegionVisibility

    # Cor predominante da região.
    # Deve ser null quando visibility=UNKNOWN.
    dominant_color: CoatColorName | None = None

    dominant_coverage: float | None = Field(
        default=None,
        ge=0,
        le=100,
    )

    markings: list[RegionMarking] = Field(
        default_factory=list,
    )

    confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )


class PatternMap(BaseModel):
    # Cor que melhor representa a base geral
    # observável da pelagem.
    base_color: CoatColorName

    pattern: CoatType

    # Cores relevantes além da base.
    accent_colors: list[CoatColorName] = Field(
        default_factory=list,
    )

    # Mapa espacial da pelagem.
    regions: list[CoatRegion] = Field(
        default_factory=list,
    )


class GeminiCoatAnalysis(BaseModel):
    validation: ValidationResult

    coat_type: CoatType | None = None
    color_assignments: list[SemanticColorAssignment] = Field(
        default_factory=list,
    )

    confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )

    pattern_map: PatternMap | None = None

    @model_validator(mode="after")
    def validate_analysis_state(self):
        if not self.validation.accepted:
            return self

        if self.coat_type is None:
            raise ValueError(
                "Imagem aceita sem coat_type."
            )

        if not self.color_assignments:
            raise ValueError(
                "Imagem aceita sem color_assignments."
            )

        if self.confidence is None:
            raise ValueError(
                "Imagem aceita sem confidence."
            )

        if self.pattern_map is None:
            raise ValueError(
                "Imagem aceita sem pattern_map."
            )

        return self


class CatAnalysisResponse(BaseModel):
    cat_id: str

    coat_type: CoatType
    primary_color: CoatColorName

    colors: list[CoatColor]

    confidence: float = Field(
        ge=0,
        le=1,
    )

    pattern_map: PatternMap

    # Avisos não bloqueantes para orientar a revisão humana da imagem/máscara.
    warnings: list[str] = Field(default_factory=list)

    analyzed_at: datetime
