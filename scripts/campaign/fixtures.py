"""Synthetic, deterministic campaign inputs. No real identities or cat labels."""

from datetime import UTC, datetime
import hashlib
from io import BytesIO
from pathlib import Path
import random
from uuid import NAMESPACE_URL, uuid5

SEED = 2102026
NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def uid(name):
    return uuid5(NAMESPACE_URL, f"gatalogo-quality-{SEED}/{name}")


def specifications():
    return {
        "seed": SEED,
        "clock": NOW.isoformat(),
        "F-AC": {"accounts": ["A", "B", "C"], "guest": "G", "devices": ["a1", "a2"]},
        "F-CAT": {"volumes": [0, 1, 40, 499, 500, 501, 2000], "pilot": [1, 40],
                  "states": ["live", "deleted", "foreign", "guest", "missing_analysis"]},
        "F-HIST": {"revisions_per_cat": [1, 10, 100], "page_sizes": [1, 200, 500],
                   "pilot_revisions": 1, "latest_beyond_cursor": True},
        "F-IMG": {"synthetic_only": True, "semantic_cat_labels": False,
                  "files": ["synthetic.png", "corrupt.bin"],
                  "boundary_generation": "generate_boundary_bytes(bound) in fixtures.py"},
        "F-SOC": {"conversations": [0, 1, 20, 100], "messages": [0, 100, 1000],
                  "materialized_in_pilot": False},
        "F-YARD": {"balance": 100, "facility": "market", "level": 1,
                   "operation": "order/chicken/payment=coins", "expected_balance": 88},
        "F-NOT": {"volumes": [0, 19, 20, 21, 100], "equal_timestamp": True},
        "F-NET": {"profiles": ["none", "delay", "drop_before", "drop_after"],
                  "bandwidth_shaping": "PENDING", "packet_loss": "PENDING"},
        "REID": {"state": "BLOCKED", "reason": "No independently labelled consented dataset",
                 "required_columns": ["individual_id", "photo_hash", "session_id", "split",
                                      "light", "view", "distance", "quality", "label_source", "consent"]},
        "ACC": {"state": "PREPARED_SCRIPT_ONLY", "ids": [f"ACC-{i:02}" for i in range(1, 9)],
                "real_assistive_device_execution": "BLOCKED: device/build authorization required"},
    }


def catalog_rows(count, revisions=1):
    """Same logical inputs for both source versions; storage IDs never random."""
    for index in range(count):
        yield {
            "id": uid(f"cat-{index}"), "client_id": uid(f"client-{index}"),
            "user_id": uid("A"), "client_updated_at": NOW,
            "updated_by_device": "a1", "payload": {"name": f"Synthetic {index}", "unknown": [1, 2]},
            "analysis": {"coat_type": "Fixture only"},
            "deleted_at": NOW if count > 1 and index == count - 1 else None,
            "revisions": revisions,
        }


def social_rows(conversations=1, messages=0):
    return [{"id": str(uid(f"conversation-{i}")), "members": [str(uid("A")), str(uid("B"))],
             "messages": [{"id": str(uid(f"message-{i}-{j}")), "body": f"Synthetic {j}",
                           "created_at": NOW.isoformat()} for j in range(messages)]}
            for i in range(conversations)]


def notification_rows(count=21):
    return [{"id": str(uid(f"notification-{i}")), "created_at": NOW.isoformat(),
             "read_at": NOW.isoformat() if i % 2 else None} for i in range(count)]


def generate_boundary_bytes(bound):
    """Byte-boundary fixture only; not a valid semantic image/pixel-limit fixture."""
    if bound < 1 or bound > 16 * 1024 * 1024:
        raise ValueError("Bounded pilot supports at most 16 MiB")
    return {n: b"x" * n for n in (bound - 1, bound, bound + 1)}


def materialize(folder):
    from PIL import Image

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    image = Image.new("RGB", (32, 32))
    image.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(1024)])
    output = BytesIO()
    image.save(output, "PNG")
    files = {"synthetic.png": output.getvalue(), "corrupt.bin": b"not an image"}
    manifest = specifications()
    manifest["image_hashes"] = {}
    for name, data in files.items():
        (folder / name).write_bytes(data)
        manifest["image_hashes"][name] = hashlib.sha256(data).hexdigest()
    return manifest
