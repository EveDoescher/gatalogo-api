from datetime import UTC, datetime
from uuid import UUID
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_database_session
from app.persistence_models import User, ProgressEvent, RemoteCat, CatVisualEmbedding, SyncChange
from app.dependencies import current_user
from app.services import progression as progress
from app.services.realtime_events import publish_committed_events

router = APIRouter(prefix="/app/progress", tags=["progress"])


class ExperienceEvent(BaseModel):
    event_id: UUID
    name: Literal["capture_saved", "capture_cancelled", "result_opened", "collection_opened", "habitat_opened", "audio_changed"]


@router.post("/experience", status_code=204)
async def experience(payload: ExperienceEvent, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    # Only categorical usage signals: no photo, location, text or reward.
    await progress.account(session, user.id)
    key = f"experience:{payload.event_id}"
    existing = await session.scalar(select(ProgressEvent.id).where(ProgressEvent.user_id == user.id, ProgressEvent.event_key == key))
    if existing is None:
        from sqlalchemy import func
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        count = await session.scalar(select(func.count()).select_from(ProgressEvent).where(
            ProgressEvent.user_id == user.id, ProgressEvent.kind == "experience", ProgressEvent.created_at >= today))
        if count < 100:
            session.add(ProgressEvent(user_id=user.id, event_key=key, kind="experience", state="confirmed",
                xp=0, coins=0, evidence={"name": payload.name}, result={}))
    await session.commit()


@router.get("")
async def summary(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    result = await progress.snapshot(session, user.id)
    await session.commit()
    return result


@router.get("/results")
async def history(limit: int = Query(50, ge=1, le=200), cursor: UUID | None = None,
                  user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    query = select(ProgressEvent).where(ProgressEvent.user_id == user.id,
        ProgressEvent.kind.in_((*progress.REWARDS, "pending_identity")))
    if cursor:
        before = await session.get(ProgressEvent, cursor)
        if before is None or before.user_id != user.id:
            raise HTTPException(404, "Resultado não encontrado.")
        boundary = select(ProgressEvent.created_at).where(ProgressEvent.id == before.id).scalar_subquery()
        query = query.where(or_(ProgressEvent.created_at < boundary,
            and_(ProgressEvent.created_at == boundary, ProgressEvent.id < before.id)))
    rows = await session.scalars(query.order_by(ProgressEvent.created_at.desc(), ProgressEvent.id.desc()).limit(limit))
    return [progress.result_for(e) for e in rows]


@router.get("/results/{event_id}")
async def result(event_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    event = await session.get(ProgressEvent, event_id)
    if event is None or event.user_id != user.id:
        raise HTTPException(404, "Resultado não encontrado.")
    return progress.result_for(event)


@router.get("/records/{client_id}")
async def record_result(client_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    cat = await _cat(session, user.id, client_id)
    event = await session.scalar(select(ProgressEvent).where(ProgressEvent.user_id == user.id,
        ProgressEvent.record_id == str(client_id), ProgressEvent.kind.in_(("discovery", "complement", "encounter", "pending_identity")))
        .order_by(ProgressEvent.created_at.desc()).limit(1))
    if event:
        return progress.result_for(event)
    return {"event_id": None, "state": "pending", "record_id": str(client_id),
            "record_type": "owned" if cat.payload.get("is_owned") else "discovery",
            "credits": {"xp": 0, "coins": 0}, "attributes": {},
            "reason": "Salvo. Aguardando análise e confirmação do servidor."}


async def _cat(session, user_id, client_id):
    cat = await session.scalar(select(RemoteCat).where(RemoteCat.user_id == user_id,
        RemoteCat.client_id == client_id, RemoteCat.deleted_at.is_(None)))
    if cat is None:
        raise HTTPException(404, "Registro não encontrado.")
    return cat


class AttributeReview(BaseModel):
    primary_color: Literal["Preto", "Branco", "Cinza", "Laranja", "Marrom", "Creme", "Outro"]
    coat_type: Literal["Sólido", "Bicolor", "Tricolor", "Rajado", "Tuxedo", "Escaminha", "Colorpoint", "Outro"]
    hair_type: Literal["Curto", "Médio", "Longo", "Sem pelo", "Outro"]


@router.put("/results/{event_id}/attributes")
async def review(event_id: UUID, payload: AttributeReview, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    await progress.account(session, user.id)
    event = await session.get(ProgressEvent, event_id)
    if event is None or event.user_id != user.id or event.evidence.get("validated_by") != "server_analysis":
        raise HTTPException(404, "Resultado não disponível para revisão.")
    event.evidence = {**event.evidence, "attributes": payload.model_dump(), "attributes_confirmed": True,
                      "reviewed_by": str(user.id)}
    if event.state == "needs_review":
        event.state = "confirmed"
    cat = await _cat(session, user.id, UUID(event.record_id))
    cat.analysis = {**(cat.analysis or {}), **payload.model_dump()}
    session.add(SyncChange(user_id=user.id, cat_id=cat.id))
    # The base grant is immutable; only newly completed objectives can pay here.
    reviewed = await progress.grant(session, user.id, f"review:{event.id}", "review", record_id=event.record_id,
        evidence={"source_event_id": str(event.id)})
    await progress.finish(session, event)
    await progress.finish(session, reviewed)
    await session.commit()
    await publish_committed_events(session)
    return progress.result_for(event)


@router.get("/records/{client_id}/suggestions")
async def suggestions(client_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    cat = await _cat(session, user.id, client_id)
    current = await session.scalar(select(CatVisualEmbedding).where(CatVisualEmbedding.cat_id == cat.id).limit(1))
    if current is None:
        return []
    # Private, within this account only. This is a suggestion, never a merge.
    distance = CatVisualEmbedding.embedding.cosine_distance(current.embedding)
    rows = (await session.execute(select(RemoteCat, distance.label("distance"))
        .join(CatVisualEmbedding, CatVisualEmbedding.cat_id == RemoteCat.id)
        .where(RemoteCat.user_id == user.id, RemoteCat.id != cat.id, RemoteCat.deleted_at.is_(None))
        .order_by(distance).limit(3))).all()
    return [{"client_id": str(c.client_id), "name": c.payload.get("name") or "Gato conhecido",
             "message": "Pode ser o mesmo gato. Você decide."} for c, d in rows if d < .25]


class IdentityChoice(BaseModel):
    individual_id: UUID
    contribution_type: Literal["angle", "detail", "clearer", "encounter"] = "encounter"


@router.put("/records/{client_id}/identity")
async def identity(client_id: UUID, payload: IdentityChoice, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    await progress.account(session, user.id)
    cat = await _cat(session, user.id, client_id)
    chosen = await progress.identify(session, cat, payload.individual_id, payload.contribution_type)
    session.add(SyncChange(user_id=user.id, cat_id=cat.id))
    result = {"individual_id": chosen, "summary": await progress.snapshot(session, user.id)}
    await session.commit()
    await publish_committed_events(session)
    return result


@router.get("/clues/{match_id}")
async def contributed_clue(match_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    from app.persistence_models import MatchSuggestion, Sighting, MissingReport
    match = await session.get(MatchSuggestion, match_id)
    sighting = await session.get(Sighting, match.sighting_id) if match else None
    report = await session.get(MissingReport, match.missing_report_id) if match else None
    if not sighting or user.id not in (sighting.observer_id, report.user_id):
        raise HTTPException(404, "Pista não encontrada.")
    return {"match_id": str(match.id), "report_id": str(report.id), "status": match.status,
            "report_status": report.status, "note": sighting.note,
            "message": "O tutor confirmou que sua pista ajudou." if match.status == "confirmed" else "O tutor ainda está avaliando esta pista."}
