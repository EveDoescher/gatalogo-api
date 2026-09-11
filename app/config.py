from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    max_image_mb: int = 10
    max_image_pixels: int = 36_000_000
    # O detector e o SAM 2 processam esta imagem no servidor. 960 px preserva
    # detalhes de pelagem e reduz quase pela metade os pixels frente a 1280.
    vision_max_side: int = 960
    # O Gemini recebe apenas o recorte já segmentado; 640 px é suficiente para
    # padrão e cores, com menos upload e menor tempo de inferência externa.
    gemini_max_side: int = 640
    jpeg_quality: int = 88
    # A resposta estruturada pode conter mapa de regiões e cores; 1024 evita
    # truncamento de JSON sem voltar ao limite original de 1536 tokens.
    gemini_max_output_tokens: int = 1024
    analysis_cache_dir: str = ".analysis-cache"
    local_semantic_gate_enabled: bool = True
    local_solid_dominance_threshold: float = 0.96
    local_solid_min_detector_confidence: float = 0.85

    # Os limites abaixo rejeitam apenas imagens evidentemente inviáveis.
    # Eles devem ser calibrados com fotos reais antes de ficarem mais rígidos.
    min_blur_score: float = 8.0
    min_contrast: float = 8.0
    max_extreme_exposure_ratio: float = 0.98

    model_cache_dir: str = ".model-cache"
    detector_confidence: float = 0.6
    # Segunda tentativa apenas quando o limiar principal não encontra gato.
    # Mantemos o limiar principal conservador para não aumentar falsos positivos.
    # 0,40 recupera gatos pequenos reais, mantendo aviso obrigatório de revisão.
    detector_fallback_confidence: float = 0.40
    gemini_max_retries: int = 1
    sam2_checkpoint: str = ".model-cache/sam2.1_hiera_tiny.pt"
    sam2_config: str = "configs/sam2.1/sam2.1_hiera_t.yaml"
    vision_device: str = "cpu"
    # Um segundo gato com pelo menos 35% da área do principal é relevante.
    secondary_cat_max_area_ratio: float = 0.35
    cat_crop_padding_ratio: float = 0.05
    color_cluster_count: int = 4
    color_min_coverage: float = 0.03
    color_merge_distance: float = 18.0
    # A cromia (a/b em Lab) separa pigmento de variações de luz (L).
    color_shade_chroma_distance: float = 14.0
    color_black_l_threshold: float = 45.0

    database_url: str = (
        "postgresql+asyncpg://gatalogo:gatalogo@localhost:5432/gatalogo"
    )
    jwt_secret: str = "development-secret-change-me"
    jwt_issuer: str = "gatalogo-api"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5
    otp_pepper: str = "development-otp-pepper-change-me"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "no-reply@example.com"
    smtp_use_tls: bool = True
    smtp_enabled: bool = False
    google_web_client_id: str | None = None
    # JSON de conta de serviço usado somente pelo servidor para enviar FCM.
    # Nunca é incluído no app móvel ou versionado no repositório.
    fcm_service_account_file: str | None = None
    fcm_project_id: str | None = None
    photo_storage_dir: str = "storage"
    tombstone_retention_days: int = 90

    # Worker de reconhecimento em CPU. O processo web apenas grava jobs pendentes.
    vision_worker_poll_seconds: float = 2.0
    vision_job_max_attempts: int = 3
    dinov2_model_name: str = "dinov2_vits14"
    match_similarity_threshold: float = Field(default=0.78, ge=0, le=1)
    # Compatibilidade de configuração: nenhum limiar de corpo promove sozinho
    # gatos sólidos. A decisão atual exige revisão independentemente do valor.
    solid_coat_similarity_threshold: float = Field(default=0.88, ge=0, le=1)
    match_min_quality: float = Field(default=0.35, ge=0, le=1)
    # Provisional evidence is recorded for evaluation before notifying owners.
    match_shadow_mode: bool = True
    match_fine_pair_limit: int = Field(default=6, ge=1, le=25)
    match_max_distance_meters: int = Field(default=50_000, ge=100, le=50_000)
    vision_job_lease_seconds: int = Field(default=180, ge=30)

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def resolve_project_paths(self):
        """Mantém modelos locais no projeto, mesmo fora da raiz no terminal."""
        self.model_cache_dir = str(
            _resolve_project_path(self.model_cache_dir)
        )
        self.sam2_checkpoint = str(
            _resolve_project_path(self.sam2_checkpoint)
        )
        self.analysis_cache_dir = str(
            _resolve_project_path(self.analysis_cache_dir)
        )
        self.photo_storage_dir = str(
            _resolve_project_path(self.photo_storage_dir)
        )
        return self

    @property
    def max_image_bytes(self) -> int:
        return self.max_image_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path
