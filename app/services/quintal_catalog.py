"""Versioned game balance. Monetary test discounts never alter these prices."""
from app.services.habitat_pricing import effective_build_seconds, effective_price

BOND_REWARDS = ((20, "friendship_garden"), (50, "yellow_flowers"),
                (100, "leafy_plant"), (200, "oak_tree"))

CROPS = {
    "corn": ("Milho", 1, 1800, 2), "wheat": ("Trigo", 1, 2700, 3),
    "carrot": ("Cenoura", 1, 3600, 2), "turnip": ("Nabo", 2, 5400, 3),
    "pumpkin": ("Abóbora", 2, 7200, 2), "melon": ("Melão", 3, 14400, 3),
}
RECIPES = {
    "garden": ("Mistura da horta", {"wheat": 2, "carrot": 1}, 600, 3, 12, "kitchen", 1),
    "fish": ("Mistura com peixe", {"fish": 1, "wheat": 2}, 1200, 3, 16, "pier", 1),
    "egg": ("Mistura com ovo", {"egg": 1, "corn": 1, "wheat": 1}, 1500, 3, 18, "coop", 1),
    "chicken": ("Mistura com frango", {"chicken": 1, "wheat": 2, "carrot": 1}, 2400, 4, 20, "market", 1),
    "beef": ("Mistura com carne", {"beef": 1, "wheat": 2, "pumpkin": 1}, 3600, 4, 22, "garden", 2),
    "creamy": ("Mistura cremosa", {"milk": 1, "egg": 1, "wheat": 2}, 2700, 4, 20, "pasture", 1),
    "apple": ("Mistura com maçã", {"wheat": 1, "apple": 2}, 1200, 3, 16, "orchard", 1),
    "pear": ("Mistura com pera", {"wheat": 1, "pear": 2}, 1200, 3, 16, "orchard", 1),
    "special": ("Mistura especial", {"fish": 1, "chicken": 1, "pumpkin": 1}, 7200, 6, 24, "kitchen", 2),
}
# label, base price, seconds, Quintal expansion, prerequisites (counter or facility)
FACILITIES = {
    "garden:1": ("Horta · 4 canteiros", 0, 0, 0, {"care": 1}),
    "garden:2": ("Horta · 8 canteiros", 120, 3600, 2, {"harvest": 10}),
    "garden:3": ("Horta · 12 canteiros", 220, 7200, 3, {"harvest": 30}),
    "kitchen:1": ("Cozinha", 40, 600, 0, {"harvest": 1}),
    "kitchen:2": ("Cozinha ampliada", 140, 3600, 2, {"cook": 10}),
    "pier:1": ("Consertar o píer e acesso", 80, 1800, 1, {"cook": 1}),
    "market:1": ("Mercadinho rural", 0, 0, 0, {"facility:kitchen": 1}),
    "coop:1": ("Galinheiro · 2 galinhas", 100, 3600, 1, {"facility:kitchen": 1, "crop:corn": 1}),
    "coop:2": ("Galinheiro · 4 galinhas", 200, 7200, 3, {"egg": 20}),
    "pasture:1": ("Pasto · 1 vaquinha", 180, 7200, 2, {"facility:coop": 1}),
    "pasture:2": ("Pasto · 2 vaquinhas", 260, 14400, 3, {"milk_collection": 10}),
    "rod:1": ("Vara básica", 0, 0, 0, {"facility:pier": 1}),
    "rod:2": ("Vara melhorada", 100, 0, 0, {"fish": 10}),
    "rod:3": ("Vara avançada", 240, 0, 3, {"fish": 30}),
    "orchard:1": ("Pomar · 2 fruteiras", 280, 14400, 3, {"facility:garden": 3}),
}
ORDERS = {
    "chicken": ("Encomendar frango", 12, {"corn": 2, "wheat": 1}, 3600, 2, 0),
    "beef": ("Encomendar carne", 20, {"milk": 2, "wheat": 2}, 7200, 2, 2),
}
PREFERENCES = ["Odeia", "Não é fã", "Aceita", "Normal", "Gosta", "Gosta muito", "Favorita"]
SATISFACTION = [0, .25, .65, 1, 1.2, 1.4, 1.65]
RESOURCE_NAMES = {**{k: v[0] for k, v in CROPS.items()}, "egg": "Ovo", "milk": "Leite", "fish": "Peixe", "chicken": "Frango", "beef": "Carne", "apple": "Maçã", "pear": "Pera"}

def order_product_options(order_id):
    definition = ORDERS[order_id]
    options = [{"id": "harvest", "name": "Produtos", "products": definition[2]}]
    if order_id == "chicken":
        options.append({"id": "fruit", "name": "Frutas", "products": {"apple": 3, "pear": 3},
                        "facility": "orchard", "level": 1})
    return options


def catalog():
    from app.services.habitat_service import SHOP
    return {
        "bond_rewards": [{"threshold": threshold, "sku": sku, "name": SHOP[sku]["name"],
                          "destination": "decorations"} for threshold, sku in BOND_REWARDS],
        "version": 2, "preferences": PREFERENCES, "resources": RESOURCE_NAMES,
        "crops": [{"id": k, "name": v[0], "level": v[1], "seconds": v[2], "yield": v[3]} for k, v in CROPS.items()],
        "recipes": [{"id": k, "name": v[0], "ingredients": v[1], "seconds": v[2], "yield": v[3], "satisfaction": v[4], "facility": v[5], "level": v[6]} for k, v in RECIPES.items()],
        "facilities": [{"id": k, "name": v[0], "base_price": v[1], "price": effective_price(v[1]), "base_seconds": v[2], "seconds": effective_build_seconds(v[2]), "expansion": v[3], "requirements": v[4], **({"description": "Colha maçãs e peras para preparar rações na cozinha ou trocar por frango no Mercadinho."} if k == "orchard:1" else {})} for k, v in FACILITIES.items()],
        "orders": [{"id": k, "name": v[0], "base_price": v[1], "price": effective_price(v[1]), "products": v[2], "product_options": order_product_options(k), "seconds": v[3], "yield": v[4], "expansion": v[5]} for k, v in ORDERS.items()],
        "fishing_daily_limit": 12, "food_capacity": 2,
    }
