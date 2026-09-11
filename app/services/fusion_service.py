from datetime import datetime, timezone

from app.exceptions import GeminiAnalysisError
from app.models.analysis import (
    CatAnalysisResponse,
    CoatColor,
    GeminiCoatAnalysis,
)
from app.models.vision import VisionColor


class FusionService:
    """Combina percentuais físicos com a interpretação semântica do Gemini."""

    def combine(
        self,
        *,
        cat_id: str,
        measured_colors: list[VisionColor],
        semantic: GeminiCoatAnalysis,
        warnings: list[str] | tuple[str, ...] = (),
    ) -> CatAnalysisResponse:
        if semantic.coat_type is None or semantic.confidence is None:
            raise GeminiAnalysisError("A análise semântica retornou dados incompletos.")
        if semantic.pattern_map is None:
            raise GeminiAnalysisError("O mapa de pelagem não foi retornado.")

        assignments = {
            assignment.cluster_index: assignment.name
            for assignment in semantic.color_assignments
        }
        if len(assignments) != len(semantic.color_assignments):
            raise GeminiAnalysisError("Há clusters de cor repetidos na resposta semântica.")
        if set(assignments) != set(range(len(measured_colors))):
            raise GeminiAnalysisError(
                "A resposta semântica não nomeou todos os clusters medidos."
            )

        percentages: dict[str, float] = {}
        order: list[str] = []
        for index, color in enumerate(measured_colors):
            name = assignments[index]
            if name not in percentages:
                percentages[name] = 0.0
                order.append(name)
            percentages[name] += color.percentage

        colors = [
            CoatColor(name=name, percentage=round(percentages[name], 1))
            for name in order
        ]
        colors[-1].percentage = round(
            100.0 - sum(color.percentage for color in colors[:-1]),
            1,
        )
        primary_color = max(colors, key=lambda color: color.percentage).name

        return CatAnalysisResponse(
            cat_id=cat_id,
            coat_type=semantic.coat_type,
            primary_color=primary_color,
            colors=colors,
            confidence=semantic.confidence,
            pattern_map=semantic.pattern_map,
            warnings=list(warnings),
            analyzed_at=datetime.now(timezone.utc),
        )
