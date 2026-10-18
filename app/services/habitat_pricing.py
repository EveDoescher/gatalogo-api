"""Temporary free testing mode; base prices and ownership remain intact."""
from app.config import get_settings


def effective_price(base_price: int) -> int:
    return 0 if get_settings().habitat_free_shop else base_price


def effective_build_seconds(base_seconds: int) -> int:
    return 0 if get_settings().habitat_instant_build else base_seconds


def shop_item(sku: str, definition: dict) -> dict:
    return {"sku": sku, **definition, "base_price": definition["price"],
            "price": effective_price(definition["price"]),
            "purchasable": definition["price"] > 0}
