"""Normalize the temporary CC-BY cat GLB for the Gatálogo mobile renderer.

The downloaded Sketchfab asset uses the legacy
KHR_materials_pbrSpecularGlossiness extension.  The app renderer expects the
standard glTF PBR material, so this script keeps the geometry/UVs intact,
renames the render entities, and replaces the legacy material declarations.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path


ENTITY_NAMES = ("cat_body", "cat_eyes", "cat_face_detail", "cat_nose_detail")


def normalize(source: Path, destination: Path) -> None:
    data = source.read_bytes()
    if data[:4] != b"glTF":
        raise ValueError("O arquivo de origem não é um GLB.")
    json_length = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20 : 20 + json_length])
    binary_start = 20 + json_length
    binary_length = struct.unpack_from("<I", data, binary_start)[0]
    binary = data[binary_start + 8 : binary_start + 8 + binary_length]

    for index, node in enumerate(document.get("nodes", [])):
        if index >= 2 and index - 2 < len(ENTITY_NAMES) and "mesh" in node:
            node["name"] = ENTITY_NAMES[index - 2]

    for material in document.get("materials", []):
        legacy = material.pop("extensions", {}).get(
            "KHR_materials_pbrSpecularGlossiness", {}
        )
        color = legacy.get("diffuseFactor", [1, 1, 1, 1])
        material["pbrMetallicRoughness"] = {
            "baseColorFactor": color,
            "metallicFactor": 0.0,
            "roughnessFactor": 0.82,
        }
        material.pop("extensions", None)
        material["doubleSided"] = True

    used = [item for item in document.get("extensionsUsed", []) if item != "KHR_materials_pbrSpecularGlossiness"]
    if used:
        document["extensionsUsed"] = used
    else:
        document.pop("extensionsUsed", None)
    document.pop("extensionsRequired", None)
    document.setdefault("asset", {}).setdefault("extras", {}).update(
        {
            "gatalogo_attribution": "Cat Low Poly by lilyjoyhanna, CC-BY-4.0",
            "gatalogo_entities": list(ENTITY_NAMES),
        }
    )

    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" " * ((4 - len(encoded) % 4) % 4)
    binary += b"\0" * ((4 - len(binary) % 4) % 4)
    header = b"glTF" + struct.pack("<II", 2, 12 + 8 + len(encoded) + 8 + len(binary))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(
        header
        + struct.pack("<I4s", len(encoded), b"JSON")
        + encoded
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )


if __name__ == "__main__":
    repo = Path(__file__).resolve().parents[2]
    normalize(
        repo / "catlogue" / "assets" / "models" / "cat_low_poly_source.glb",
        repo / "catlogue" / "assets" / "models" / "cat_low_poly.glb",
    )
