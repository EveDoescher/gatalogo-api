"""Authoritative habitat presentation shared by habitat and production routes.

Uses the caller's session and transaction; does not commit or publish events.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.persistence_models import HabitatItem, RemoteCat
from app.services import habitat_encounters as encounters
from app.services.habitat_interactions import care_summary
from app.services.habitat_pricing import effective_price
from app.services.habitat_service import EXPANSION_PRICES, wallet_for, yard_cells


def item_response(item: HabitatItem) -> dict:
    return {
        "id": str(item.id),
        "sku": item.sku,
        "x": item.x,
        "y": item.y,
        "rotation": item.rotation,
        "care_state": item.care_state or {},
    }


async def habitat_snapshot(session: AsyncSession, user_id: UUID) -> dict:
    wallet = await wallet_for(session, user_id)
    from app.services.habitat_interactions import migrate_calendar

    wallet.care_state = migrate_calendar(wallet.care_state or {}, datetime.now(UTC))
    items = (
        (
            await session.execute(
                select(HabitatItem)
                .where(HabitatItem.user_id == user_id)
                .order_by(HabitatItem.created_at, HabitatItem.id)
            )
        )
        .scalars()
        .all()
    )
    cats = (
        (
            await session.execute(
                select(RemoteCat).where(
                    RemoteCat.user_id == user_id, RemoteCat.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    pets = [
        {
            "id": str(cat.client_id),
            "name": (cat.payload or {}).get("name") or "Meu gato",
            "primary_color": (cat.analysis or {}).get("primary_color")
            or ((cat.analysis or {}).get("pattern_map") or {}).get("base_color"),
            "coat_type": (cat.analysis or {}).get("coat_type")
            or ((cat.analysis or {}).get("pattern_map") or {}).get("pattern"),
            "coat_colors": list(
                dict.fromkeys(
                    [
                        *(
                            c["name"]
                            for c in ((cat.analysis or {}).get("colors") or [])
                            if isinstance(c, dict) and isinstance(c.get("name"), str)
                        ),
                        *(
                            c
                            for c in (
                                ((cat.analysis or {}).get("pattern_map") or {}).get(
                                    "accent_colors"
                                )
                                or []
                            )
                            if isinstance(c, str)
                        ),
                    ]
                )
            ),
        }
        for cat in cats
        if (cat.payload or {}).get("is_owned")
    ]
    return {
        "satisfaction": encounters.satisfaction(wallet, items, datetime.now(UTC)),
        "pending_gift": (wallet.care_state or {}).get("pending_gift"),
        "balance": wallet.balance,
        "revision": wallet.layout_revision,
        "layout_rules_version": wallet.layout_rules_version,
        "selected_cat_ids": wallet.selected_cat_ids or [],
        "cat_anchors": wallet.cat_anchors or {},
        "expansion_level": wallet.expansion_level,
        "cat_capacity": 3 + wallet.expansion_level,
        "decoration_capacity": 24 + 8 * wallet.expansion_level,
        "buildable_cells": [
            list(cell) for cell in sorted(yard_cells(wallet.expansion_level))
        ],
        "next_expansion_price": effective_price(
            EXPANSION_PRICES[wallet.expansion_level]
        )
        if wallet.expansion_level < 3
        else None,
        "items": [item_response(item) for item in items],
        "care": care_summary(
            wallet.care_state or {}, 3 + wallet.expansion_level, datetime.now(UTC)
        ),
        "cats": pets,
        "shop_version": 5,
        "free_shop": get_settings().habitat_free_shop,
    }
