"""Shared missing-report serialization for API views."""

from app.persistence_models import MissingReport, RemoteCat
from app.schemas import MissingReportResponse


def report_response(report: MissingReport, cat: RemoteCat) -> MissingReportResponse:
    details = report.poster_details or {}
    cat_details = cat.payload or {}
    return MissingReportResponse(
        id=report.id,
        cat_client_id=cat.client_id,
        latitude=report.latitude,
        longitude=report.longitude,
        radius_meters=report.radius_meters,
        status=report.status,
        created_at=report.created_at,
        last_seen_address=details.get("last_seen_address"),
        contacts=details.get("contacts", []),
        poster_contacts=details.get("poster_contacts", details.get("contacts", [])),
        poster_options=details.get("poster_options", {}),
        collar=details.get("collar") or cat_details.get("collar"),
        distinguishing_marks=details.get("distinguishing_marks")
        or cat_details.get("distinguishing_marks"),
        microchip=details.get("microchip") or cat_details.get("microchip"),
    )
