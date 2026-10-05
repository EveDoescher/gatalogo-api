from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_database_session
from app.persistence_models import (
    AppNotification,
    CatPhoto,
    Conversation,
    ConversationMember,
    Friendship,
    MatchSuggestion,
    MissingReport,
    RemoteCat,
    Sighting,
    User,
    VisionJob,
    ProgressEvent,
)
from app.routers.auth import current_user
from app.schemas import (
    MatchResponse,
    MissingReportCreate,
    MissingPosterUpdate,
    MissingReportResponse,
    SightingCreate,
    VisionJobResponse,
)
from app.services.recognition_service import create_sighting_matches, create_report_matches
from app.services import progression as progress
from app.services.realtime_events import publish_committed_events, queue_notification
from app.services.vision_jobs import enqueue_recognition

router = APIRouter(prefix="/vision", tags=["vision"])


async def _cat(session: AsyncSession, user: User, client_id: UUID) -> RemoteCat:
    cat = (await session.execute(select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.client_id == client_id, RemoteCat.deleted_at.is_(None)))).scalar_one_or_none()
    if cat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gato não encontrado.")
    return cat


def _report_response(report: MissingReport, cat: RemoteCat) -> MissingReportResponse:
    details = report.poster_details or {}
    cat_details = cat.payload or {}
    return MissingReportResponse(
        id=report.id, cat_client_id=cat.client_id,
        latitude=report.latitude, longitude=report.longitude,
        radius_meters=report.radius_meters, status=report.status,
        created_at=report.created_at,
        last_seen_address=details.get("last_seen_address"),
        contacts=details.get("contacts", []),
        poster_contacts=details.get("poster_contacts", details.get("contacts", [])),
        poster_options=details.get("poster_options", {}),
        collar=details.get("collar") or cat_details.get("collar"),
        distinguishing_marks=details.get("distinguishing_marks") or cat_details.get("distinguishing_marks"),
        microchip=details.get("microchip") or cat_details.get("microchip"),
    )


@router.post("/cats/{client_id}/enqueue", response_model=VisionJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def enqueue_cat(client_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> VisionJobResponse:
    cat = await _cat(session, user, client_id)
    photo = (await session.execute(select(CatPhoto).where(CatPhoto.cat_id == cat.id, CatPhoto.kind == "primary", CatPhoto.deleted_at.is_(None)))).scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Envie uma foto antes de iniciar o reconhecimento.")
    job = await enqueue_recognition(session, cat=cat, photo=photo)
    await session.commit()
    return VisionJobResponse(id=job.id, status=job.status, error_message=job.error_message, created_at=job.created_at, completed_at=job.completed_at)


@router.get("/cats/{client_id}/jobs", response_model=list[VisionJobResponse])
async def cat_jobs(client_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[VisionJobResponse]:
    cat = await _cat(session, user, client_id)
    jobs = (await session.execute(select(VisionJob).where(VisionJob.cat_id == cat.id).order_by(VisionJob.created_at.desc()))).scalars().all()
    return [VisionJobResponse(id=job.id, status=job.status, error_message=job.error_message, created_at=job.created_at, completed_at=job.completed_at) for job in jobs]


@router.post("/missing-reports", response_model=MissingReportResponse, status_code=status.HTTP_201_CREATED)
async def create_report(payload: MissingReportCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> MissingReportResponse:
    await progress.account(session, user.id)
    cat = await _cat(session, user, payload.cat_client_id)
    await session.refresh(cat, with_for_update=True)
    if (cat.payload or {}).get("is_owned") not in (True, 1):
        raise HTTPException(status_code=422, detail="Cadastre o gato em Meus gatos antes de abrir um alerta.")
    existing = (await session.execute(select(MissingReport).where(MissingReport.cat_id == cat.id, MissingReport.status == "active"))).scalar_one_or_none()
    if existing is not None:
        return _report_response(existing, cat)
    if payload.radius_meters > get_settings().match_max_distance_meters:
        raise HTTPException(status_code=422, detail="Raio excede o limite configurado.")
    cat_details = cat.payload or {}
    report = MissingReport(
        user_id=user.id, cat_id=cat.id, latitude=payload.latitude,
        longitude=payload.longitude, radius_meters=payload.radius_meters,
        poster_details={
            "last_seen_address": (payload.last_seen_address or "").strip() or None,
            "contacts": [contact.model_dump() for contact in payload.contacts],
            "collar": cat_details.get("collar"),
            "distinguishing_marks": cat_details.get("distinguishing_marks"),
            "microchip": cat_details.get("microchip"),
        },
    )
    session.add(report)
    await session.flush()
    # Alertas novos também verificam avistamentos já analisados; não dependem
    # de uma nova foto ser enviada para começar a procurar.
    await create_report_matches(session, report=report, settings=get_settings())
    await session.commit()
    await publish_committed_events(session)
    await session.refresh(report)
    return _report_response(report, cat)


@router.get("/missing-reports", response_model=list[MissingReportResponse])
async def reports(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[MissingReportResponse]:
    rows = (await session.execute(select(MissingReport, RemoteCat).join(RemoteCat, RemoteCat.id == MissingReport.cat_id).where(MissingReport.user_id == user.id).order_by(MissingReport.created_at.desc()))).all()
    return [_report_response(report, cat) for report, cat in rows]


@router.put("/missing-reports/{report_id}/poster", response_model=MissingReportResponse)
async def update_poster(report_id: UUID, payload: MissingPosterUpdate, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> MissingReportResponse:
    report = (await session.execute(select(MissingReport).where(MissingReport.id == report_id, MissingReport.user_id == user.id).with_for_update())).scalar_one_or_none()
    if report is None:
        raise HTTPException(404, "Alerta não encontrado.")
    if report.status != "active":
        raise HTTPException(409, "Este alerta já foi encerrado.")
    cat = await session.get(RemoteCat, report.cat_id)
    if cat is None or cat.deleted_at is not None:
        raise HTTPException(404, "Gato não disponível.")
    contacts = [contact.model_dump() for contact in payload.contacts]
    report.poster_details = {**(report.poster_details or {}),
        "poster_options": payload.options.model_dump(), "poster_contacts": contacts,
        "contacts": [{"phone": c["phone"], "whatsapp": c["whatsapp"]} for c in contacts if c["included"]]}
    await session.commit()
    await session.refresh(report)
    return _report_response(report, cat)


@router.post("/missing-reports/{report_id}/resolve", status_code=status.HTTP_204_NO_CONTENT)
async def resolve_report(report_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> None:
    report = (await session.execute(select(MissingReport).where(MissingReport.id == report_id, MissingReport.user_id == user.id).with_for_update())).scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alerta não encontrado.")
    report.status, report.resolved_at = "resolved", datetime.now(UTC)
    await session.commit()


@router.post("/sightings", status_code=status.HTTP_201_CREATED)
async def create_sighting(payload: SightingCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict[str, str]:
    await progress.account(session, user.id)
    if payload.client_event_id:
        previous = await session.scalar(select(ProgressEvent).where(ProgressEvent.user_id == user.id,
            ProgressEvent.event_key == f"sighting:{payload.client_event_id}"))
        if previous:
            expected = {"cat": str(payload.cat_client_id), "latitude": payload.latitude, "longitude": payload.longitude}
            if previous.evidence != expected:
                raise HTTPException(409, "Esta chave já pertence a outro avistamento.")
            return {"id": previous.record_id, "status": "recorded"}
    cat = await _cat(session, user, payload.cat_client_id)
    await session.refresh(cat, with_for_update=True)
    observed_at = payload.observed_at
    if observed_at is not None and (observed_at.tzinfo is None or observed_at > datetime.now(UTC)):
        raise HTTPException(422, "Informe uma data de avistamento válida.")
    query = select(Sighting).where(
        Sighting.cat_id == cat.id, Sighting.observer_id == user.id,
        Sighting.latitude == payload.latitude, Sighting.longitude == payload.longitude)
    if observed_at is not None:
        query = query.where(Sighting.created_at == observed_at)
    sighting = (await session.execute(query.limit(1))).scalar_one_or_none()
    if sighting is None:
        sighting = Sighting(observer_id=user.id, cat_id=cat.id, latitude=payload.latitude, longitude=payload.longitude, note=payload.note)
        if observed_at is not None:
            sighting.created_at = observed_at
        session.add(sighting)
        await session.flush()
    await create_sighting_matches(session, sighting=sighting, settings=get_settings())
    if payload.client_event_id:
        session.add(ProgressEvent(user_id=user.id, event_key=f"sighting:{payload.client_event_id}",
            kind="sighting", state="confirmed", record_id=str(sighting.id), xp=0, coins=0,
            evidence={"cat": str(payload.cat_client_id), "latitude": payload.latitude, "longitude": payload.longitude}, result={}))
    await session.commit()
    await publish_committed_events(session)
    return {"id": str(sighting.id), "status": "recorded"}


@router.get("/matches", response_model=list[MatchResponse])
async def matches(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[MatchResponse]:
    rows = (await session.execute(
        select(MatchSuggestion, MissingReport, Sighting)
        .join(MissingReport, MissingReport.id == MatchSuggestion.missing_report_id)
        .join(Sighting, Sighting.id == MatchSuggestion.sighting_id)
        .where(MissingReport.user_id == user.id)
        .order_by(MatchSuggestion.created_at.desc())
    )).all()
    return [
        MatchResponse(
            id=match.id,
            report_id=report.id,
            sighting_id=sighting.id,
            similarity=match.similarity,
            status=match.status,
            restricted=match.restricted,
            evidence=match.evidence or {},
            latitude=sighting.latitude if match.location_revealed_at else None,
            longitude=sighting.longitude if match.location_revealed_at else None,
            conversation_id=(await session.execute(select(Conversation.id).where(Conversation.match_suggestion_id == match.id))).scalar_one_or_none(),
        )
        for match, report, sighting in rows
    ]


@router.post("/matches/{match_id}/{decision}", response_model=MatchResponse)
async def decide_match(match_id: UUID, decision: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> MatchResponse:
    if decision not in {"confirm", "dismiss", "reopen"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Decisão inválida.")
    query = (select(MatchSuggestion, MissingReport, Sighting)
        .join(MissingReport, MissingReport.id == MatchSuggestion.missing_report_id)
        .join(Sighting, Sighting.id == MatchSuggestion.sighting_id)
        .where(MatchSuggestion.id == match_id, MissingReport.user_id == user.id))
    initial = (await session.execute(query)).one_or_none()
    if initial is None:
        raise HTTPException(status_code=404, detail="Correspondência não encontrada.")
    # Same order as queued sightings: account first, then case/record locks.
    await progress.account(session, initial[2].observer_id)
    row = (await session.execute(query.with_for_update().execution_options(populate_existing=True))).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Correspondência não encontrada.")
    match, report, sighting = row
    if match.status == "stale" or report.status != "active":
        raise HTTPException(status_code=409, detail="Esta sugestão não está mais ativa.")
    if decision == "reopen":
        if match.status != "dismissed":
            raise HTTPException(status_code=409, detail="Apenas pistas descartadas podem ser reabertas.")
        match.status = "pending"
    else:
        if match.status in {"confirmed", "dismissed"}:
            raise HTTPException(status_code=409, detail="Esta sugestão já recebeu uma decisão.")
        match.status = "confirmed" if decision == "confirm" else "dismissed"
    conversation_id = None
    if decision == "confirm":
        if sighting.observer_id != report.user_id:
            event = await progress.grant(session, sighting.observer_id,
                f"clue:{report.id}:{sighting.observer_id}", "clue", record_id=str(match.id),
                evidence={"report_id": str(report.id), "sighting_id": str(sighting.id), "confirmed_by": str(user.id)})
            if not event.result:
                await progress.finish(session, event)
        match.location_revealed_at = datetime.now(UTC)
        notification = AppNotification(
            user_id=sighting.observer_id,
            type="match_confirmed",
            payload={"match_id": str(match.id), "report_id": str(report.id)},
        )
        session.add(notification)
        await session.flush()
        queue_notification(session, notification)
        friends = (await session.execute(select(Friendship.id).where(Friendship.user_id == user.id, Friendship.friend_id == sighting.observer_id))).scalar_one_or_none()
        if friends is not None:
            conversation = Conversation(match_suggestion_id=match.id)
            session.add(conversation)
            await session.flush()
            session.add_all((ConversationMember(conversation_id=conversation.id, user_id=user.id), ConversationMember(conversation_id=conversation.id, user_id=sighting.observer_id)))
            conversation_id = conversation.id
    await session.commit()
    await publish_committed_events(session)
    return MatchResponse(id=match.id, report_id=report.id, sighting_id=sighting.id, similarity=match.similarity, status=match.status, restricted=match.restricted, latitude=sighting.latitude if match.location_revealed_at else None, longitude=sighting.longitude if match.location_revealed_at else None, conversation_id=conversation_id)


@router.get("/notifications")
async def notifications(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[dict]:
    items = (await session.execute(select(AppNotification).where(AppNotification.user_id == user.id).order_by(AppNotification.created_at.desc()).limit(100))).scalars().all()
    return [{"id": str(item.id), "type": item.type, "payload": item.payload, "read_at": item.read_at, "created_at": item.created_at} for item in items]
