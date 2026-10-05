"""One public projection for posters, community details and shared feed cards."""
from app.schemas import PosterOptions


def public_poster_details(report, cat):
    if report.status != "active":
        return {"contacts": [], "poster_options": PosterOptions().model_dump()}
    details = report.poster_details or {}
    options = PosterOptions(**details.get("poster_options", {})).model_dump()
    traits = cat.payload or {}
    return {
        "contacts": [{"phone": c["phone"], "whatsapp": c.get("whatsapp", False)}
                     for c in details.get("poster_contacts", details.get("contacts", [])) if c.get("included", True)],
        "last_seen_address": details.get("last_seen_address") if options["address"] else None,
        "created_at": report.created_at if options["date"] else None,
        "poster_options": options,
        **{key: (details.get(key) or traits.get(key)) if options[key] else None
           for key in ("collar", "distinguishing_marks", "microchip")},
    }
