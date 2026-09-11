from __future__ import annotations

import math
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.persistence_models import (AppNotification, CatPhoto, CatVisualEmbedding,
                                    MatchSuggestion, MissingReport, RemoteCat, Sighting)
from app.services.recognition_features import model_version, near_duplicate, verify_local_features
from app.services.dense_features import compare_dense_features
from app.services.realtime_events import queue_event, queue_notification


def valid_coordinates(latitude, longitude) -> bool:
    return (isinstance(latitude, (int, float)) and not isinstance(latitude, bool)
            and isinstance(longitude, (int, float)) and not isinstance(longitude, bool)
            and math.isfinite(latitude) and math.isfinite(longitude)
            and -90 <= latitude <= 90 and -180 <= longitude <= 180)


def distance_meters(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    if not valid_coordinates(lat_a, lon_a) or not valid_coordinates(lat_b, lon_b):
        return math.inf
    d_lat, d_lon = math.radians(lat_b - lat_a), math.radians(lon_b - lon_a)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat_a)) * math.cos(math.radians(lat_b)) * math.sin(d_lon / 2) ** 2
    return 2 * 6_371_000.0 * math.asin(math.sqrt(min(1.0, max(0.0, a))))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("Vetores devem ter a mesma dimensão não vazia.")
    if not all(math.isfinite(value) for value in (*left, *right)):
        raise ValueError("Vetor não finito.")
    left_norm, right_norm = math.hypot(*left), math.hypot(*right)
    if not left_norm or not right_norm:
        raise ValueError("Vetor sem informação.")
    return min(1.0, max(-1.0, sum((a / left_norm) * (b / right_norm) for a, b in zip(left, right))))


def is_solid_analysis(analysis: dict | None) -> bool:
    if not analysis:
        return False
    coat = str(analysis.get("coat_type") or "").casefold()
    colors = analysis.get("colors") or []
    return "sólid" in coat or "solid" in coat or len(colors) == 1


def _dense_priority(evidence: dict) -> tuple:
    similarity = evidence["similarity"]
    return (evidence["geometry_consistent"], evidence["inliers"],
            similarity if similarity is not None else -2)


def compare_embeddings(sources: list, targets: list, settings: Settings,
                       *, uncertain_coat: bool = False) -> dict | None:
    """Visual retrieval plus local geometric corroboration; never identity proof."""
    pairs = []
    for source in sources:
        for target in targets:
            if (source.model_version != target.model_version
                    or source.model_version != model_version(settings)
                    or source.region != target.region
                    or len(source.embedding) != 384 or len(target.embedding) != 384
                    or not math.isfinite(source.quality_score) or not math.isfinite(target.quality_score)
                    or min(source.quality_score, target.quality_score) < settings.match_min_quality):
                continue
            # Opposite flanks have different markings; unknown views only retrieve.
            if source.view != "unknown" and target.view != "unknown" and source.view != target.view:
                continue
            try:
                similarity = cosine_similarity(list(source.embedding), list(target.embedding))
            except (ValueError, TypeError):
                continue
            pairs.append((similarity, source, target))
    if not pairs:
        return None
    pairs.sort(key=lambda pair: pair[0], reverse=True)
    best, source, target = pairs[0]
    if best < settings.match_similarity_threshold:
        return None
    solid = uncertain_coat or any(row.is_solid_coat for row in sources + targets)
    support, used_source, used_target = [], [], []
    geometry = {"verified": False, "inliers": 0, "matches": 0, "coverage": 0.0}
    dense = compare_dense_features(None, None)
    fine_checks = 0
    duplicate_image = False
    for similarity, a, b in pairs:
        if similarity < settings.match_similarity_threshold:
            break
        if near_duplicate(a, b):
            duplicate_image = True
            continue
        if not any(near_duplicate(a, previous) for previous in used_source) and not any(near_duplicate(b, previous) for previous in used_target):
            support.append(similarity)
            used_source.append(a)
            used_target.append(b)
        if fine_checks < settings.match_fine_pair_limit:
            fine_checks += 1
            local = verify_local_features((a.features or {}).get("local"), (b.features or {}).get("local"))
            if (local["verified"], local["inliers"], local["matches"]) > (geometry["verified"], geometry["inliers"], geometry["matches"]):
                geometry = local
            patch_result = compare_dense_features((a.features or {}).get("dense"), (b.features or {}).get("dense"))
            if _dense_priority(patch_result) > _dense_priority(dense):
                dense = patch_result
                dense["source_photo_id"], dense["target_photo_id"] = str(a.photo_id), str(b.photo_id)
    reasons = []
    if solid:
        reasons.append("solid_or_unknown_coat_requires_individual_evidence")
    geometric_support = geometry["verified"] or dense["geometry_consistent"]
    if not geometric_support:
        reasons.append("no_geometric_corroboration")
    if len(support) < 2:
        reasons.append("insufficient_independent_photos")
    if duplicate_image:
        reasons.append("duplicate_image_is_not_independent_evidence")
    restricted = solid or (not geometric_support and len(support) < 2)
    return {"similarity": best, "restricted": restricted,
            "evidence": {"decision": "insufficient_evidence" if restricted else "possible_match",
                         "identity_confirmed": False, "score_kind": "cosine_similarity",
                         "calibrated": False, "model_version": source.model_version,
                         "probability": None,
                         "shadow_mode": settings.match_shadow_mode,
                         "would_notify": not restricted,
                         "notification_eligible": not restricted and not settings.match_shadow_mode,
                         "distinct_photo_pairs": len(support), "local": geometry,
                         "dense": dense, "fine_pairs_checked": fine_checks,
                         "corroborated_similarity": sum(support[:3]) / len(support[:3]) if support else None,
                         "solid_or_unknown_coat": solid, "reasons": reasons,
                         "capture_guidance": ["Fotografe o rosto de frente, com olhos e orelhas visíveis.",
                                              "Registre peito, patas, cauda e pequenas marcas, se visíveis.",
                                              "Inclua os dois lados do corpo, sem forçar ou perseguir o gato."] if solid else []}}


async def _embeddings(session: AsyncSession, cat_id: UUID, settings: Settings) -> list:
    return list((await session.execute(
        select(CatVisualEmbedding).join(CatPhoto, CatPhoto.id == CatVisualEmbedding.photo_id)
        .where(CatVisualEmbedding.cat_id == cat_id, CatPhoto.cat_id == cat_id,
               CatPhoto.deleted_at.is_(None), CatVisualEmbedding.photo_hash == CatPhoto.content_hash,
               CatVisualEmbedding.model_version == model_version(settings))
    )).scalars().all())


async def create_sighting_matches(session: AsyncSession, *, sighting: Sighting,
                                  settings: Settings, report_id: UUID | None = None) -> int:
    source_cat = await session.get(RemoteCat, sighting.cat_id)
    if source_cat is None or source_cat.deleted_at is not None or not valid_coordinates(sighting.latitude, sighting.longitude):
        return 0
    query = select(MissingReport).where(
        MissingReport.status == "active", MissingReport.user_id != sighting.observer_id,
        MissingReport.cat_id != sighting.cat_id,
        func.abs(MissingReport.latitude - sighting.latitude) <= settings.match_max_distance_meters / 111_000,
    )
    if report_id is not None:
        query = query.where(MissingReport.id == report_id)
    # Serializes suggestions per report, including notifications.
    reports = (await session.execute(query.order_by(MissingReport.id).with_for_update())).scalars().all()
    sources = None
    created = 0
    for report in reports:
        if not 0 < report.radius_meters <= settings.match_max_distance_meters:
            continue
        if distance_meters(report.latitude, report.longitude, sighting.latitude, sighting.longitude) > report.radius_meters:
            continue
        target_cat = await session.get(RemoteCat, report.cat_id)
        if target_cat is None or target_cat.deleted_at is not None:
            continue
        if sources is None:
            sources = await _embeddings(session, sighting.cat_id, settings)
        targets = await _embeddings(session, report.cat_id, settings)
        result = compare_embeddings(sources, targets, settings,
                                    uncertain_coat=not (source_cat.analysis or {}).get("coat_type")
                                    or not (target_cat.analysis or {}).get("coat_type")
                                    or is_solid_analysis(source_cat.analysis) or is_solid_analysis(target_cat.analysis))
        existing = (await session.execute(select(MatchSuggestion).where(
            MatchSuggestion.missing_report_id == report.id,
            MatchSuggestion.sighting_id == sighting.id))).scalar_one_or_none()
        if existing is not None and existing.status in {"confirmed", "dismissed"}:
            continue
        if result is None:
            if existing is not None:
                existing.status, existing.restricted = "stale", True
            continue
        notify = (
            existing is None or existing.status == "stale"
            or bool((existing.evidence or {}).get("shadow_mode"))
        )
        if existing is None:
            existing = MatchSuggestion(id=uuid4(), missing_report_id=report.id, sighting_id=sighting.id)
            session.add(existing)
            created += 1
        existing.similarity, existing.restricted = result["similarity"], result["restricted"]
        existing.evidence, existing.status = result["evidence"], "pending"
        if notify and not settings.match_shadow_mode:
            previous = (await session.execute(select(AppNotification.id).where(
                AppNotification.user_id == report.user_id, AppNotification.type == "possible_match",
                AppNotification.payload["match_id"].as_string() == str(existing.id)))).scalar_one_or_none()
            if previous is None:
                message = "Um gato com evidências visuais compatíveis foi encontrado na região. Confira as fotos." if not result["restricted"] else "Uma pista visual compatível foi avistada na sua região. Confira as fotos."
                notification = AppNotification(
                    user_id=report.user_id,
                    type="possible_match",
                    payload={"match_id": str(existing.id), "message": message},
                )
                session.add(notification)
                await session.flush()
                queue_notification(session, notification)
                queue_event(
                    session,
                    user_id=report.user_id,
                    event="new_clue",
                    data={"report_id": str(report.id), "match_id": str(existing.id)},
                )
        await session.flush()
    return created


async def create_report_matches(session: AsyncSession, *, report: MissingReport, settings: Settings) -> int:
    if report.status != "active":
        return 0
    sightings = (await session.execute(select(Sighting).where(
        Sighting.observer_id != report.user_id,
        func.abs(Sighting.latitude - report.latitude) <= report.radius_meters / 111_000,
    ).order_by(Sighting.id))).scalars().all()
    total = 0
    for sighting in sightings:
        total += await create_sighting_matches(session, sighting=sighting, settings=settings, report_id=report.id)
    return total


async def refresh_cat_matches(session: AsyncSession, *, cat: RemoteCat, settings: Settings) -> None:
    """Both sides must trigger matching, regardless of processing order."""
    sightings = (await session.execute(select(Sighting).where(Sighting.cat_id == cat.id))).scalars().all()
    for sighting in sightings:
        await create_sighting_matches(session, sighting=sighting, settings=settings)
    reports = (await session.execute(select(MissingReport).where(
        MissingReport.cat_id == cat.id, MissingReport.status == "active").order_by(MissingReport.id))).scalars().all()
    for report in reports:
        await create_report_matches(session, report=report, settings=settings)


async def sync_catalog_sighting(session: AsyncSession, *, cat: RemoteCat, settings: Settings) -> None:
    """Use recorded cat coordinates; never infer the observer's current GPS."""
    if (cat.payload or {}).get("is_owned") in (True, 1):
        return
    lat, lon = (cat.payload or {}).get("latitude"), (cat.payload or {}).get("longitude")
    if cat.deleted_at is not None or not valid_coordinates(lat, lon):
        return
    existing = (await session.execute(select(Sighting).where(
        Sighting.cat_id == cat.id, Sighting.observer_id == cat.user_id,
        Sighting.latitude == lat, Sighting.longitude == lon).limit(1))).scalar_one_or_none()
    if existing is None:
        existing = Sighting(observer_id=cat.user_id, cat_id=cat.id, latitude=lat, longitude=lon)
        session.add(existing)
        await session.flush()
    await create_sighting_matches(session, sighting=existing, settings=settings)


async def report_owner(session: AsyncSession, report_id: UUID) -> UUID | None:
    report = await session.get(MissingReport, report_id)
    return report.user_id if report else None
