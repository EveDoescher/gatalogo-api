import cv2
import numpy as np

from app.config import Settings
from app.exceptions import ColorAnalysisError
from app.models.vision import VisionColor


class ColorService:
    """Mede a paleta da área segmentada, sem incluir o fundo."""

    _MAX_TRAINING_PIXELS = 20_000

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def extract(
        self,
        image: np.ndarray,
        mask: np.ndarray | None,
    ) -> list[VisionColor]:
        if mask is None or image.shape[:2] != mask.shape[:2]:
            raise ColorAnalysisError("A máscara do gato é inválida.")

        cat_pixels = image[mask > 0]
        if len(cat_pixels) < 64:
            raise ColorAnalysisError(
                "A máscara do gato possui pixels insuficientes para medir cores."
            )

        lab_pixels = cv2.cvtColor(
            cat_pixels.reshape(-1, 1, 3),
            cv2.COLOR_BGR2LAB,
        ).reshape(-1, 3).astype(np.float32)
        centers = self._cluster(lab_pixels)
        counts = self._assign_pixels(lab_pixels, centers)
        centers, counts = self._merge_clusters(centers, counts)

        return self._to_vision_colors(centers, counts)

    def _cluster(self, pixels: np.ndarray) -> np.ndarray:
        cluster_count = min(self.settings.color_cluster_count, len(pixels))
        if cluster_count < 2:
            return pixels.mean(axis=0, keepdims=True)

        sample_indexes = np.linspace(
            0,
            len(pixels) - 1,
            min(len(pixels), self._MAX_TRAINING_PIXELS),
            dtype=int,
        )
        samples = pixels[sample_indexes]
        cv2.setRNGSeed(42)
        _, _, centers = cv2.kmeans(
            samples,
            cluster_count,
            None,
            (
                cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
                30,
                0.5,
            ),
            3,
            cv2.KMEANS_PP_CENTERS,
        )
        return centers.astype(np.float32)

    @staticmethod
    def _assign_pixels(
        pixels: np.ndarray,
        centers: np.ndarray,
    ) -> np.ndarray:
        distances = np.linalg.norm(
            pixels[:, None, :] - centers[None, :, :],
            axis=2,
        )
        labels = distances.argmin(axis=1)
        return np.bincount(labels, minlength=len(centers)).astype(np.int64)

    def _merge_clusters(
        self,
        centers: np.ndarray,
        counts: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        clusters = [
            [center.copy(), int(count)]
            for center, count in zip(centers, counts, strict=True)
            if count > 0
        ]

        changed = True
        while changed and len(clusters) > 1:
            changed = False
            for first in range(len(clusters)):
                for second in range(first + 1, len(clusters)):
                    distance = np.linalg.norm(
                        clusters[first][0] - clusters[second][0]
                    )
                    if distance <= self.settings.color_merge_distance:
                        self._combine(clusters, first, second)
                        changed = True
                        break
                if changed:
                    break

        total = sum(cluster[1] for cluster in clusters)
        while len(clusters) > 1:
            smallest = min(range(len(clusters)), key=lambda index: clusters[index][1])
            if clusters[smallest][1] / total >= self.settings.color_min_coverage:
                break
            nearest = min(
                (index for index in range(len(clusters)) if index != smallest),
                key=lambda index: np.linalg.norm(
                    clusters[smallest][0] - clusters[index][0]
                ),
            )
            self._combine(clusters, nearest, smallest)

        return (
            np.asarray([cluster[0] for cluster in clusters]),
            np.asarray([cluster[1] for cluster in clusters]),
        )

    @staticmethod
    def _combine(
        clusters: list[list[np.ndarray | int]],
        target: int,
        source: int,
    ) -> None:
        target_center, target_count = clusters[target]
        source_center, source_count = clusters[source]
        total = int(target_count) + int(source_count)
        clusters[target] = [
            (
                (np.asarray(target_center) * int(target_count))
                + (np.asarray(source_center) * int(source_count))
            ) / total,
            total,
        ]
        clusters.pop(source)

    def _to_vision_colors(
        self,
        centers: np.ndarray,
        counts: np.ndarray,
    ) -> list[VisionColor]:
        total = int(counts.sum())
        ordered = sorted(
            zip(centers, counts, strict=True),
            key=lambda item: int(item[1]),
            reverse=True,
        )
        shade_groups = self._shade_groups(
            [np.asarray(center, dtype=np.float32) for center, _ in ordered]
        )
        colors: list[VisionColor] = []
        accumulated = 0.0

        for index, (center, count) in enumerate(ordered):
            if index == len(ordered) - 1:
                percentage = round(100.0 - accumulated, 1)
            else:
                percentage = round((int(count) / total) * 100, 1)
                accumulated += percentage

            colors.append(
                VisionColor(
                    hex=ColorService._lab_to_hex(center),
                    percentage=max(0.0, percentage),
                    shade_group=shade_groups[index],
                )
            )
        return colors

    def _shade_groups(self, centers: list[np.ndarray]) -> list[int]:
        """Agrupa apenas alterações de luminosidade de uma mesma cromia.

        A componente L do espaço Lab muda muito com sombra, pelo branco do
        ambiente e pela exposição da câmera. Já a/b tende a preservar o
        pigmento. Preto é mantido separado para não ser confundido com uma
        sombra de uma pelagem clara.
        """
        representatives: list[np.ndarray] = []
        groups: list[int] = []

        for center in centers:
            if center[0] <= self.settings.color_black_l_threshold:
                groups.append(len(representatives))
                representatives.append(center)
                continue

            candidate_groups = [
                index
                for index, representative in enumerate(representatives)
                if representative[0] > self.settings.color_black_l_threshold
            ]
            if candidate_groups:
                closest = min(
                    candidate_groups,
                    key=lambda index: np.linalg.norm(
                        center[1:] - representatives[index][1:]
                    ),
                )
                chroma_distance = np.linalg.norm(
                    center[1:] - representatives[closest][1:]
                )
                if chroma_distance <= self.settings.color_shade_chroma_distance:
                    groups.append(closest)
                    continue

            groups.append(len(representatives))
            representatives.append(center)

        return groups

    @staticmethod
    def _lab_to_hex(center: np.ndarray) -> str:
        lab = np.clip(np.rint(center), 0, 255).astype(np.uint8)
        bgr = cv2.cvtColor(
            lab.reshape(1, 1, 3),
            cv2.COLOR_LAB2BGR,
        )[0, 0]
        red, green, blue = int(bgr[2]), int(bgr[1]), int(bgr[0])
        return f"#{red:02X}{green:02X}{blue:02X}"
