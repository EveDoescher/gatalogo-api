class AnalysisError(Exception):
    """Erro base da análise de pelagem."""


class MissingApiKeyError(AnalysisError):
    """A chave da Gemini API não foi configurada."""


class InvalidImageError(AnalysisError):
    """O arquivo recebido não pôde ser validado como imagem."""


class NoCatDetectedError(AnalysisError):
    """Nenhum gato foi identificado com confiança suficiente."""


class GeminiAnalysisError(AnalysisError):
    """A chamada ao Gemini falhou ou retornou um resultado inválido."""


class SegmentationError(AnalysisError):
    """O segmentador local não pôde analisar a imagem."""


class ColorAnalysisError(AnalysisError):
    """A paleta local não pôde ser extraída da máscara do gato."""

class AnalysisError(Exception):
    """Erro base da análise de pelagem."""


class MissingApiKeyError(AnalysisError):
    """A chave da Gemini API não foi configurada."""


class InvalidImageError(AnalysisError):
    """O arquivo recebido não pôde ser validado como imagem."""


class RejectedImageError(AnalysisError):
    """A imagem foi rejeitada pelas regras de negócio."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)


class GeminiAnalysisError(AnalysisError):
    """A chamada ao Gemini falhou ou retornou um resultado inválido."""
