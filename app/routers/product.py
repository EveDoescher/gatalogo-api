"""User-facing views. Private media is shared only within an authorized sighting."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field, field_validator
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from app.services import progression as progress, milestones
from sqlalchemy import select, func
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_database_session
from app.services.report_presenter import report_response as _report_response
from app.persistence_models import (User, RemoteCat, MissingReport, Sighting,
    MatchSuggestion, Conversation, ConversationMember, Friendship, AppNotification,
    FeedItem, ProductState)
from app.dependencies import current_user
from app.schemas import FavoritesSyncRequest, FeedbackRequest
from app.services.storage_service import photo_storage
from app.services.recognition_service import distance_meters, create_report_matches
from app.services.realtime_events import publish_committed_events
from app.config import get_settings

from app.services.conversations import member_or_403 as _member_or_403

from app.services.report_media import report_photo_bytes, photo_thumbnail_bytes

router = APIRouter(prefix="/app", tags=["app"])


async def _get_or_create_product_state(session: AsyncSession, user: User) -> ProductState:
    await session.flush()
    await session.refresh(user, with_for_update=True)
    state = await session.get(ProductState, user.id)
    if state is None:
        state = ProductState(user_id=user.id, preferences={}, achievements={})
        session.add(state)
        await session.flush()
    return state


class UpdateAccountRequest(BaseModel):
    username: str | None = None
    bio: str | None = None
    avatar_preset: Literal['bapinho', 'bapinha'] | None = None


@router.get("/account")
async def account(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    state = await _get_or_create_product_state(session, user)
    await session.commit()
    return {
        "username": user.username,
        "email": user.email,
        "bio": state.preferences.get("bio", ""),
        "avatar_url": state.preferences.get("avatar_url"),
        "preferences": {"social_notifications": True, "sighting_notifications": True, **state.preferences},
    }


@router.put("/account")
async def update_account(payload: UpdateAccountRequest, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    state = await _get_or_create_product_state(session, user)
    if payload.username is not None:
        clean = payload.username.strip().lower().replace("@", "")
        if clean and clean != user.username:
            existing = (await session.execute(select(User).where(User.username == clean, User.id != user.id))).scalar_one_or_none()
            if existing is not None:
                raise HTTPException(409, "Este nome de usuário já está em uso.")
            user.username = clean
    if payload.bio is not None:
        new_prefs = dict(state.preferences)
        new_prefs["bio"] = payload.bio.strip()
        state.preferences = new_prefs
    if payload.avatar_preset is not None:
        state.preferences = {**state.preferences,
            'avatar_url': f'/app/account/avatar/preset/{payload.avatar_preset}'}
    await session.commit()
    return {
        "username": user.username,
        "email": user.email,
        "bio": state.preferences.get("bio", ""),
        "avatar_url": state.preferences.get("avatar_url"),
    }


@router.post("/account/avatar")
async def upload_avatar(file: UploadFile = File(...), user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    data = await file.read()
    if not data:
        raise HTTPException(400, "Arquivo vazio.")
    suffix = Path(file.filename or "avatar.jpg").suffix or ".jpg"
    photo_storage.save_profile_photo(user_id=user.id, data=data, suffix=suffix)
    state = await _get_or_create_product_state(session, user)
    new_prefs = dict(state.preferences)
    new_prefs["avatar_url"] = f"/app/account/avatar/{user.id}?v={uuid.uuid4().hex}"
    state.preferences = new_prefs
    await session.commit()
    return {"avatar_url": new_prefs["avatar_url"]}


@router.get('/account/avatar/preset/{preset}')
async def get_avatar_preset(preset: Literal['bapinho', 'bapinha']) -> Response:
    path = Path(__file__).resolve().parents[1] / 'assets' / 'avatars' / f'{preset}.png'
    return Response(path.read_bytes(), media_type='image/png',
                    headers={'Cache-Control': 'public, max-age=86400'})


@router.get("/account/avatar/{target_user_id}")
async def get_avatar(target_user_id: UUID) -> Response:
    try:
        path = photo_storage.absolute_path(photo_storage.profile_key_for(target_user_id, ".jpg"))
        parent = path.parent
        if parent.is_dir():
            matches = [f for f in parent.glob("avatar*") if f.is_file()]
            if matches:
                latest = max(matches, key=lambda p: p.stat().st_mtime)
                suffix = latest.suffix.lower()
                media_type = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
                return Response(latest.read_bytes(), media_type=media_type, headers={"Cache-Control": "public, max-age=3600"})
    except Exception:
        pass
    raise HTTPException(404, "Foto de perfil não encontrada.")


class Preferences(BaseModel):
    quintal_notifications: bool = True
    social_notifications: bool = True
    sighting_notifications: bool = True
    gallery_audience: Literal["private", "friends", "public"] = "friends"
    medals_audience: Literal["private", "friends", "public"] = "friends"
    habitat_audience: Literal["private", "friends", "public"] = "friends"
    timezone: str = "America/Sao_Paulo"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Fuso horário inválido.")
        return value


@router.put("/preferences", status_code=204)
async def preferences(payload: Preferences, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> None:
    state = await _get_or_create_product_state(session, user)
    state.preferences = {**(state.preferences or {}), **payload.model_dump(exclude_unset=True)}
    await session.commit()


@router.post("/notifications/{notification_id}/read", status_code=204)
async def read_notification(notification_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> None:
    item = await session.get(AppNotification, notification_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(404, "Notificação não encontrada.")
    item.read_at = item.read_at or datetime.now(UTC)
    await session.commit()


@router.get("/reports")
async def reports(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[dict]:
    rows = (await session.execute(select(MissingReport, RemoteCat).join(RemoteCat, RemoteCat.id == MissingReport.cat_id).where(MissingReport.user_id == user.id).order_by(MissingReport.created_at.desc()))).all()
    result = []
    for report, cat in rows:
        count = (await session.execute(select(func.count()).select_from(MatchSuggestion).where(MatchSuggestion.missing_report_id == report.id, MatchSuggestion.status.in_(["pending", "confirmed"])))).scalar_one()
        result.append({"id": report.id, "cat_client_id": cat.client_id, "name": (cat.payload or {}).get("name") or "Meu gato", "latitude": report.latitude, "longitude": report.longitude, "radius_meters": report.radius_meters, "status": report.status, "created_at": report.created_at, "resolved_at": report.resolved_at, "clue_count": count, **_report_response(report, cat).model_dump()})
    return result


@router.get("/reports/nearby")
async def nearby_reports(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(default=15.0, ge=0.5, le=100.0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> list[dict]:
    rows = (await session.execute(
        select(MissingReport, RemoteCat)
        .join(RemoteCat, RemoteCat.id == MissingReport.cat_id)
        .where(MissingReport.status == "active")
        .order_by(MissingReport.created_at.desc())
    )).all()

    results = []
    max_meters = radius_km * 1000.0
    for report, cat in rows:
        dist = distance_meters(latitude, longitude, report.latitude, report.longitude)
        effective_radius = max(float(report.radius_meters), max_meters)
        if dist <= effective_radius:
            count = (await session.execute(
                select(func.count())
                .select_from(MatchSuggestion)
                .where(MatchSuggestion.missing_report_id == report.id, MatchSuggestion.status.in_(["pending", "confirmed"]))
            )).scalar_one()
            results.append({
                "id": report.id,
                "cat_client_id": cat.client_id,
                "name": (cat.payload or {}).get("name") or "Gatinho",
                "latitude": report.latitude,
                "longitude": report.longitude,
                "radius_meters": report.radius_meters,
                "distance_meters": round(dist / 100) * 100,
                "is_owner": report.user_id == user.id,
                "created_at": report.created_at,
                "clue_count": count,
                "photo_url": f"/app/reports/{report.id}/photo",
            })
    results.sort(key=lambda r: r["distance_meters"])
    return results


@router.get("/reports/{report_id}/photo")
async def report_photo(
    report_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> Response:
    data = await report_photo_bytes(session, report_id)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=300"})

async def _clue(session: AsyncSession, user: User, match_id: UUID):
    row = (await session.execute(select(MatchSuggestion, MissingReport, Sighting).join(MissingReport, MissingReport.id == MatchSuggestion.missing_report_id).join(Sighting, Sighting.id == MatchSuggestion.sighting_id).where(MatchSuggestion.id == match_id, MissingReport.user_id == user.id))).one_or_none()
    if row is None:
        raise HTTPException(404, "Pista não encontrada.")
    return row


class ReportArea(BaseModel):
    last_seen_address: str | None = Field(default=None, max_length=500)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_meters: int = Field(ge=100, le=50_000)


@router.put("/reports/{report_id}")
async def update_report_area(report_id: UUID, payload: ReportArea, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    report = (await session.execute(select(MissingReport).where(MissingReport.id == report_id, MissingReport.user_id == user.id).with_for_update())).scalar_one_or_none()
    if report is None:
        raise HTTPException(404, "Alerta não encontrado.")
    if report.status != "active":
        raise HTTPException(409, "Este alerta já foi encerrado.")
    if payload.radius_meters > get_settings().match_max_distance_meters:
        raise HTTPException(422, "Raio excede o limite permitido.")
    report.latitude, report.longitude, report.radius_meters = payload.latitude, payload.longitude, payload.radius_meters
    report.poster_details = {**(report.poster_details or {}), "last_seen_address": (payload.last_seen_address or "").strip() or None}
    suggestions = (await session.execute(select(MatchSuggestion).where(MatchSuggestion.missing_report_id == report.id, MatchSuggestion.status == "pending"))).scalars().all()
    for suggestion in suggestions:
        suggestion.status = "stale"
    await session.flush()
    await create_report_matches(session, report=report, settings=get_settings())
    await session.commit()
    await publish_committed_events(session)
    return {"id": report.id, **payload.model_dump()}


@router.get("/reports/{report_id}/clues")
async def clues(report_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[dict]:
    report = await session.get(MissingReport, report_id)
    if report is None or report.user_id != user.id:
        raise HTTPException(404, "Alerta não encontrado.")
    rows = (await session.execute(select(MatchSuggestion, Sighting).join(Sighting, Sighting.id == MatchSuggestion.sighting_id).where(MatchSuggestion.missing_report_id == report.id, MatchSuggestion.status != "stale").order_by(MatchSuggestion.created_at.desc()))).all()
    result = []
    for match, sighting in rows:
        candidate = await session.get(RemoteCat, sighting.cat_id)
        if candidate is None or candidate.deleted_at is not None:
            continue
        distance = distance_meters(report.latitude, report.longitude, sighting.latitude, sighting.longitude)
        conversation = (await session.execute(select(Conversation.id).where(Conversation.match_suggestion_id == match.id))).scalar_one_or_none()
        result.append({"id": match.id, "report_id": report.id, "status": match.status, "created_at": sighting.created_at,
            "distance_meters": round(distance / 100) * 100, "note": sighting.note,
            "photo_url": f"/app/clues/{match.id}/photo", "conversation_id": conversation,
            "latitude": sighting.latitude if match.location_revealed_at else None,
            "longitude": sighting.longitude if match.location_revealed_at else None})
    return result


@router.get("/clues/{match_id}/photo")
async def clue_photo(match_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> Response:
    match, report, sighting = await _clue(session, user, match_id)
    cat = await session.get(RemoteCat, sighting.cat_id)
    if match.status == "stale" or cat is None or cat.deleted_at is not None or not cat.photo_key:
        raise HTTPException(404, "Foto não disponível.")
    return Response(photo_thumbnail_bytes(cat.photo_key), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.post("/friends/{friend_id}/conversation")
async def start_conversation(friend_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    # Both directions lock the same pair in the same order before lookup/create.
    await session.execute(select(User.id).where(User.id.in_([user.id, friend_id])).order_by(User.id).with_for_update())
    friendship = (await session.execute(select(Friendship).where(Friendship.user_id == user.id, Friendship.friend_id == friend_id).with_for_update())).scalar_one_or_none()
    if friendship is None:
        raise HTTPException(403, "As conversas estão disponíveis entre amigos.")
    mine = select(ConversationMember.conversation_id).where(ConversationMember.user_id == user.id)
    theirs = select(ConversationMember.conversation_id).where(ConversationMember.user_id == friend_id)
    conversation = (await session.execute(select(Conversation).where(Conversation.id.in_(mine), Conversation.id.in_(theirs), Conversation.match_suggestion_id.is_(None)).limit(1))).scalar_one_or_none()
    if conversation is None:
        conversation = Conversation()
        session.add(conversation)
        await session.flush()
        session.add_all([ConversationMember(conversation_id=conversation.id, user_id=uid) for uid in (user.id, friend_id)])
    await session.commit()
    return {"id": conversation.id}


@router.get("/conversations/{conversation_id}")
async def conversation_context(conversation_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    await _member_or_403(session, conversation_id, user.id)
    conversation = await session.get(Conversation, conversation_id)
    other = (await session.execute(select(User).join(ConversationMember, ConversationMember.user_id == User.id).where(ConversationMember.conversation_id == conversation_id, User.id != user.id))).scalar_one_or_none()
    return {"username": other.username if other else None, "sighting_context": conversation.match_suggestion_id is not None}


ACHIEVEMENTS = milestones.LEGACY_ACHIEVEMENTS

ACHIEVEMENT_XP: dict[str, int] = {
    "legend_collector": 250,
    "streak_30": 250,
    "master_collector": 150,
    "city_wanderer": 150,
    "streak_7": 150,
    "angles_master": 150,
    "helper": 150,
    "collector_10": 100,
    "neighborhood_3": 100,
    "night_owl": 100,
    "early_bird": 100,
    "streak_3": 100,
    "angles": 100,
    "portrait": 100,
    "poster_share": 100,
    "commentator": 100,
    "calico": 100,
    "siamese_pattern": 100,
}

LEVELS = [
    (1, "Curioso", 0, 100, "Início da jornada felina"),
    (2, "Explorador", 100, 300, "Flores para o Quintal"),
    (3, "Observador", 300, 650, "Selo de observador"),
    (4, "Catalogador", 650, 1200, "Badge exclusiva de perfil"),
    (5, "Guardião", 1200, 2000, "Selo de guardião"),
    (6, "Protetor", 2000, 3200, "Título honorário de proteção"),
    (7, "Lenda Felina", 3200, 5000, "Status máximo da comunidade"),
]


def calculate_level(xp: int) -> tuple[int, str, int, int, str]:
    for lvl, title, min_x, target, reward in LEVELS:
        if xp < target:
            return lvl, title, min_x, target, reward
    return 7, "Lenda Felina", 3200, 5000, "Status máximo da comunidade"


def calculate_multiplier(streak: int) -> float:
    return 1.0


@router.get("/gamification")
async def get_gamification(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    result = await progress.snapshot(session, user.id)
    await session.commit()
    return result


class GamificationActionRequest(BaseModel):
    action: str
    context_id: str | None = None


@router.post("/gamification/action")
async def record_gamification_action(payload: GamificationActionRequest, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    # Compatibility for old clients: an unverified action can never mint XP.
    result = await progress.snapshot(session, user.id)
    await session.commit()
    return {**result, "earned_xp": 0, "level_up": False, "missions_completed": []}


@router.get("/journey")
async def journey(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[dict]:
    result = await progress.snapshot(session, user.id)
    rows = result["achievements"]
    await session.commit()
    return rows


class MedalHighlights(BaseModel):
    ids: list[str] = Field(max_length=5)


@router.put("/journey/highlights", status_code=204)
async def highlight_medals(payload: MedalHighlights, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    state, _ = await progress.account(session, user.id)
    if len(set(payload.ids)) != len(payload.ids) or any(mid not in (state.achievements or {}) for mid in payload.ids):
        raise HTTPException(422, "Escolha até cinco medalhas que você já conquistou.")
    state.preferences = {**state.preferences, "highlighted_medals": payload.ids}
    await session.commit()


@router.post("/journey/{achievement_id}/share", status_code=204)
async def share_achievement(achievement_id: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> None:
    state = await _get_or_create_product_state(session, user)
    definitions = {**ACHIEVEMENTS, **{c[0]: (c[1], c[2], c[3]) for c in progress.CHALLENGES}}
    if not user.username or achievement_id not in state.achievements or achievement_id not in definitions:
        raise HTTPException(409, "Escolha seu nome de usuário e desbloqueie a conquista antes de compartilhar.")
    exists = (await session.execute(select(FeedItem.id).where(FeedItem.user_id == user.id, FeedItem.type == "achievement", FeedItem.payload["achievement_id"].as_string() == achievement_id))).scalar_one_or_none()
    if exists is None:
        session.add(FeedItem(user_id=user.id, type="achievement", payload={"achievement_id": achievement_id, "title": definitions[achievement_id][0]}))
    await session.commit()


@router.get("/account/export")
async def export_user_data(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    state = await _get_or_create_product_state(session, user)
    cats = (await session.execute(select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.deleted_at.is_(None)))).scalars().all()
    reports = (await session.execute(select(MissingReport, RemoteCat.client_id).join(
        RemoteCat, MissingReport.cat_id == RemoteCat.id).where(
        MissingReport.user_id == user.id, RemoteCat.user_id == user.id))).all()
    sightings = (await session.execute(select(Sighting).where(Sighting.observer_id == user.id))).scalars().all()

    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "account": {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "status": user.status,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "preferences": state.preferences,
            "achievements": state.achievements,
        },
        "cats": [
            {
                "id": str(c.id),
                "client_id": str(c.client_id),
                "name": c.payload.get("name") if c.payload else None,
                "captured_at": c.payload.get("captured_at") if c.payload else None,
                "location_name": c.payload.get("location_name") if c.payload else None,
                "is_owned": c.payload.get("is_owned") if c.payload else False,
                "analysis": c.analysis,
            }
            for c in cats
        ],
        "missing_reports": [
            {
                "id": str(r.id),
                "cat_client_id": str(cat_client_id),
                "latitude": r.latitude,
                "longitude": r.longitude,
                "radius_meters": r.radius_meters,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r, cat_client_id in reports
        ],
        "sightings": [
            {
                "id": str(s.id),
                "cat_id": str(s.cat_id),
                "latitude": s.latitude,
                "longitude": s.longitude,
                "note": s.note,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in sightings
        ],
    }


@router.post("/feedback", status_code=201)
async def submit_feedback(payload: FeedbackRequest, user: User = Depends(current_user)) -> dict:
    feedback_id = str(uuid.uuid4())
    return {
        "id": feedback_id,
        "status": "received",
        "category": payload.category,
        "message": "Agradecemos o seu feedback! Nossa equipe irá analisá-lo.",
    }


@router.get("/favorites")
async def get_favorites(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    state = await _get_or_create_product_state(session, user)
    return {"favorites": list(state.preferences.get("favorites", []))}


@router.put("/favorites")
async def update_favorites(payload: FavoritesSyncRequest, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    state = await _get_or_create_product_state(session, user)
    prefs = dict(state.preferences or {})
    prefs["favorites"] = list(set(payload.favorites))
    state.preferences = prefs
    flag_modified(state, "preferences")
    await session.commit()
    return {"favorites": prefs["favorites"]}


