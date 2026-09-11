from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import Settings
from app.exceptions import InvalidImageError
from app.models.vision import PreparedImage


class PreprocessingService:
    """Valida e cria versões seguras, normalizadas e reduzidas da foto."""

    _SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP"}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def prepare(self, image_bytes: bytes) -> PreparedImage:
        if not image_bytes:
            raise InvalidImageError("A imagem está vazia.")

        if len(image_bytes) > self.settings.max_image_bytes:
            raise InvalidImageError(
                f"A imagem ultrapassa o limite de "
                f"{self.settings.max_image_mb} MB."
            )

        try:
            with Image.open(BytesIO(image_bytes)) as source:
                image_format = source.format
                if image_format not in self._SUPPORTED_FORMATS:
                    raise InvalidImageError(
                        "Formato de imagem não suportado: "
                        f"{image_format or 'desconhecido'}."
                    )

                source_width, source_height = source.size
                if (
                    source_width * source_height
                    > self.settings.max_image_pixels
                ):
                    raise InvalidImageError(
                        "A imagem possui pixels demais para análise segura."
                    )

                source.load()
                image = ImageOps.exif_transpose(source).convert("RGB")

            original_width, original_height = image.size
            if original_width < 128 or original_height < 128:
                raise InvalidImageError(
                    "A imagem é pequena demais para análise."
                )

            vision_rgb = self._resize(image, self.settings.vision_max_side)
            vision_bgr = cv2.cvtColor(
                np.asarray(vision_rgb),
                cv2.COLOR_RGB2BGR,
            )

            return PreparedImage(
                original_width=original_width,
                original_height=original_height,
                vision_image=vision_bgr,
            )

        except InvalidImageError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as error:
            raise InvalidImageError(
                "O arquivo enviado não é uma imagem válida."
            ) from error

    @staticmethod
    def _resize(image: Image.Image, max_side: int) -> Image.Image:
        if max(image.size) <= max_side:
            return image.copy()

        resized = image.copy()
        resized.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        return resized
