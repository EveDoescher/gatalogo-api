"""Create coherent, evidence-led coat assets for the mobile cat avatar.

The temporary low-poly GLB has overlapping UV islands on its body mesh. A
painted atlas cannot place a chest patch or a flank stripe reliably: the same
pixel can be sampled by unrelated parts of the mesh. Until the model has a
semantic UV unwrap, the 3D model deliberately receives a uniform, measured
base colour. Region-specific markings are represented only in the library
preview, where their anatomy can be placed from the ``pattern_map``.
"""

from __future__ import annotations

import hashlib
import io
import math
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from PIL import Image, ImageDraw

from app.services.storage_service import photo_storage


STYLE_VERSION = "coat_atlas_v2"
PALETTE = {
    "Preto": (30, 31, 35),
    "Branco": (246, 244, 239),
    "Cinza": (113, 119, 128),
    "Laranja": (207, 111, 43),
    "Marrom": (105, 70, 45),
    "Creme": (222, 194, 143),
    "Outro": (132, 124, 114),
}


def _color(value: str | None, fallback: str = "Outro") -> tuple[int, int, int]:
    return PALETTE.get(value or fallback, PALETTE[fallback])


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _webp(frames: list[Image.Image]) -> bytes:
    output = io.BytesIO()
    frames[0].save(
        output,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=180,
        loop=0,
        quality=88,
        method=6,
    )
    return output.getvalue()


def _shade(color: tuple[int, int, int], amount: int) -> tuple[int, int, int]:
    return tuple(max(0, min(255, value + amount)) for value in color)


class AvatarService:
    """A conservative coat composer: no randomly placed markings."""

    texture_size = 1024

    def build(self, analysis: dict[str, Any] | None) -> tuple[dict[str, bytes], bytes, dict[str, Any], str]:
        analysis = analysis or {}
        pattern_map = dict(analysis.get("pattern_map") or {})
        base_name = pattern_map.get("base_color") or analysis.get("primary_color") or "Outro"
        base = _color(base_name)
        palette = self._palette(analysis, pattern_map, base_name)
        regions = self._visible_regions(pattern_map)

        # The current GLB UV map overlaps, so patterned paint would be visibly
        # wrong. A uniform texture keeps the 3D cat's colour and form honest.
        textures = {
            "cat_body": _png(self._solid_texture(base)),
            "cat_eyes": _png(self._flat_texture((137, 177, 108), (29, 42, 30))),
            "cat_face_detail": _png(self._solid_texture(base)),
            "cat_nose_detail": _png(self._flat_texture((188, 120, 126), (78, 48, 54))),
        }
        preview = _webp([self._preview_frame(base, regions, index) for index in range(12)])
        coat_map = {
            "base_color": base_name,
            "accent_colors": [name for name in palette if name != base_name],
            "pattern": pattern_map.get("pattern") or analysis.get("coat_type") or "Outro",
            "regions": regions,
            "confidence": analysis.get("confidence"),
            "style": STYLE_VERSION,
            "model_texture_mode": "base_color_only_pending_semantic_uv",
            "generated_at": datetime.now(UTC).isoformat(),
        }
        digest = hashlib.sha256()
        for key in sorted(textures):
            digest.update(textures[key])
        digest.update(preview)
        return textures, preview, coat_map, digest.hexdigest()

    def save(self, *, user_id: UUID, cat_id: UUID, analysis: dict[str, Any] | None, version: int) -> tuple[dict[str, str], str, dict[str, Any], str]:
        textures, preview, coat_map, digest = self.build(analysis)
        keys: dict[str, str] = {}
        for entity, data in textures.items():
            key, _ = photo_storage.save_avatar_asset(
                user_id=user_id,
                cat_id=cat_id,
                version=version,
                name=entity,
                data=data,
                suffix=".png",
            )
            keys[entity] = key
        preview_key, _ = photo_storage.save_avatar_asset(
            user_id=user_id,
            cat_id=cat_id,
            version=version,
            name="turntable",
            data=preview,
            suffix=".webp",
        )
        return keys, preview_key, coat_map, digest

    def _palette(self, analysis: dict[str, Any], pattern_map: dict[str, Any], base_name: str) -> list[str]:
        values: list[str] = [base_name]
        for value in pattern_map.get("accent_colors") or []:
            if value in PALETTE and value not in values:
                values.append(value)
        for item in analysis.get("colors") or []:
            value = item.get("name") if isinstance(item, dict) else None
            if value in PALETTE and value not in values:
                values.append(value)
        return values[:3]

    def _visible_regions(self, pattern_map: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            region
            for region in pattern_map.get("regions") or []
            if isinstance(region, dict)
            and region.get("visibility") in {"VISIBLE", "PARTIAL"}
            and region.get("region")
            and region.get("dominant_color") in PALETTE
        ]

    def _solid_texture(self, color: tuple[int, int, int]) -> Image.Image:
        return Image.new("RGB", (self.texture_size, self.texture_size), color)

    def _flat_texture(self, primary: tuple[int, int, int], secondary: tuple[int, int, int]) -> Image.Image:
        image = Image.new("RGB", (96, 96), primary)
        draw = ImageDraw.Draw(image)
        draw.ellipse((28, 28, 68, 68), fill=secondary)
        return image.resize((self.texture_size, self.texture_size), Image.Resampling.NEAREST)

    def _preview_frame(self, base: tuple[int, int, int], regions: list[dict[str, Any]], index: int) -> Image.Image:
        """Draw a readable low-poly cat; markings require observed regions."""
        size = 512
        image = Image.new("RGBA", (size, size), (247, 246, 250, 255))
        draw = ImageDraw.Draw(image)
        draw.ellipse((112, 430, 405, 462), fill=(34, 29, 38, 28))

        angle = index / 12 * math.tau
        side = math.sin(angle)
        body_width = int(206 - abs(side) * 24)
        center_x = 256 + int(side * 13)
        left, right = center_x - body_width // 2, center_x + body_width // 2

        mask = Image.new("L", (size, size), 0)
        mask_draw = ImageDraw.Draw(mask)
        # Head, ears, torso, legs and tail form a clear sitting-cat silhouette.
        mask_draw.polygon([(center_x - 71, 160), (center_x - 57, 86), (center_x - 20, 123), (center_x + 20, 123), (center_x + 57, 86), (center_x + 71, 160), (center_x + 60, 228), (center_x - 60, 228)], fill=255)
        mask_draw.polygon([(left + 29, 205), (right - 29, 205), (right, 347), (right - 27, 423), (left + 27, 423), (left, 347)], fill=255)
        mask_draw.rounded_rectangle((left + 36, 337, left + 74, 443), radius=17, fill=255)
        mask_draw.rounded_rectangle((right - 74, 337, right - 36, 443), radius=17, fill=255)
        tail_x = right - 9 if side >= 0 else left + 9
        tail_end = tail_x + (70 if side >= 0 else -70)
        mask_draw.line([(tail_x, 340), (tail_end, 374), (tail_end + (10 if side >= 0 else -10), 424)], fill=255, width=25, joint="curve")

        coat = Image.new("RGBA", (size, size), (*base, 255))
        self._paint_observed_regions(coat, regions, base, center_x, left, right)
        image.alpha_composite(Image.composite(coat, Image.new("RGBA", (size, size)), mask))

        # Lighting facets give depth but do not overwrite measured markings.
        lighting = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        lighting_draw = ImageDraw.Draw(lighting)
        lighting_draw.polygon([(left + 29, 205), (center_x, 230), (center_x - 18, 415), (left + 27, 423)], fill=(*_shade(base, -13), 42))
        lighting_draw.polygon([(center_x, 230), (right - 29, 205), (right - 27, 423), (center_x + 18, 415)], fill=(*_shade(base, 12), 36))
        image.alpha_composite(lighting)
        draw = ImageDraw.Draw(image)
        draw.ellipse((center_x - 39, 160, center_x - 18, 181), fill=(35, 41, 31, 230))
        draw.ellipse((center_x + 18, 160, center_x + 39, 181), fill=(35, 41, 31, 230))
        draw.polygon([(center_x - 9, 192), (center_x + 9, 192), (center_x, 201)], fill=(119, 76, 80, 230))
        return image.convert("RGB")

    def _paint_observed_regions(self, coat: Image.Image, regions: list[dict[str, Any]], base: tuple[int, int, int], center_x: int, left: int, right: int) -> None:
        draw = ImageDraw.Draw(coat)
        shapes = {
            "head_top": [(center_x - 55, 125), (center_x + 55, 125), (center_x + 43, 169), (center_x - 43, 169)],
            "face": [(center_x - 52, 152), (center_x + 52, 152), (center_x + 39, 224), (center_x - 39, 224)],
            "muzzle": [(center_x - 28, 184), (center_x + 28, 184), (center_x + 23, 218), (center_x - 23, 218)],
            "chest": [(center_x - 43, 222), (center_x + 43, 222), (center_x + 27, 351), (center_x - 27, 351)],
            "back": [(left + 28, 210), (right - 28, 210), (right - 42, 274), (left + 42, 274)],
            "left_flank": [(left + 8, 264), (center_x - 6, 248), (center_x - 20, 399), (left + 27, 416)],
            "right_flank": [(center_x + 6, 248), (right - 8, 264), (right - 27, 416), (center_x + 20, 399)],
            "front_left_leg": [(left + 36, 337), (left + 74, 337), (left + 74, 440), (left + 36, 440)],
            "front_right_leg": [(right - 74, 337), (right - 36, 337), (right - 36, 440), (right - 74, 440)],
            "tail": [(right - 6, 327), (right + 61, 365), (right + 71, 424), (right + 45, 424)],
        }
        for region in regions:
            shape = shapes.get(region.get("region"))
            color_name = region.get("dominant_color")
            if not shape or not color_name:
                continue
            color = _color(color_name)
            confidence = float(region.get("confidence") or 0)
            if confidence and confidence < 0.45:
                continue
            markings = region.get("markings") or []
            if not markings and color != base:
                draw.polygon(shape, fill=(*color, 255))
                continue
            for marking in markings:
                marking_color = _color(marking.get("color") or color_name)
                coverage = float(marking.get("coverage") or 0)
                kind = marking.get("marking_type")
                if kind == "STRIPE":
                    self._draw_stripes(draw, shape, marking_color, max(2, round(coverage / 27)))
                elif kind in {"PATCH", "SPOT", "MOTTLED"} and coverage >= 8:
                    self._draw_patch(draw, shape, marking_color, coverage)
                elif kind in {"SOLID", "POINT", "GRADIENT"}:
                    draw.polygon(shape, fill=(*marking_color, 255))

    def _draw_patch(self, draw: ImageDraw.ImageDraw, shape: list[tuple[int, int]], color: tuple[int, int, int], coverage: float) -> None:
        xs, ys = zip(*shape)
        width, height = max(xs) - min(xs), max(ys) - min(ys)
        scale = max(0.28, min(0.9, math.sqrt(coverage / 100)))
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        rx, ry = width * scale * 0.45, height * scale * 0.45
        draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=(*color, 255))

    def _draw_stripes(self, draw: ImageDraw.ImageDraw, shape: list[tuple[int, int]], color: tuple[int, int, int], count: int) -> None:
        xs, ys = zip(*shape)
        for index in range(count):
            y = min(ys) + (index + 1) * (max(ys) - min(ys)) / (count + 1)
            draw.line([(min(xs), y), (max(xs), y - 16)], fill=(*color, 255), width=10)


avatar_service = AvatarService()
