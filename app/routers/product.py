"""User-facing views. Private media is shared only within an authorized sighting."""
from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from PIL import Image, ImageOps
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_database_session
from app.persistence_models import (User, RemoteCat, CatPhoto, MissingReport, Sighting,
    MatchSuggestion, Conversation, ConversationMember, Friendship, AppNotification,
    FeedItem, ProductState)
from app.routers.auth import current_user
from app.services.storage_service import photo_storage
from app.services.recognition_service import distance_meters, create_report_matches
from app.services.realtime_events import publish_committed_events, queue_notification
from app.config import get_settings

router = APIRouter(prefix="/app", tags=["app"])


async def _state(session: AsyncSession, user: User) -> ProductState:
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


@router.get("/account")
async def account(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    state = await _state(session, user)
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
    state = await _state(session, user)
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
    state = await _state(session, user)
    new_prefs = dict(state.preferences)
    new_prefs["avatar_url"] = f"/app/account/avatar/{user.id}"
    state.preferences = new_prefs
    await session.commit()
    return {"avatar_url": new_prefs["avatar_url"]}


@router.get("/account/avatar/{target_user_id}")
async def get_avatar(target_user_id: UUID) -> Response:
    for ext in [".jpg", ".png", ".jpeg", ".webp"]:
        try:
            path = photo_storage.absolute_path(photo_storage.profile_key_for(target_user_id, ext))
            parent = path.parent
            if parent.is_dir():
                matches = list(parent.glob("avatar-*"))
                if matches:
                    latest = max(matches, key=lambda p: p.stat().st_mtime)
                    media_type = "image/png" if latest.suffix == ".png" else "image/jpeg"
                    return Response(latest.read_bytes(), media_type=media_type, headers={"Cache-Control": "public, max-age=3600"})
        except Exception:
            pass
    raise HTTPException(404, "Foto de perfil não encontrada.")


class Preferences(BaseModel):
    social_notifications: bool = True
    sighting_notifications: bool = True


@router.put("/preferences", status_code=204)
async def preferences(payload: Preferences, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> None:
    state = await _state(session, user)
    state.preferences = payload.model_dump()
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
        result.append({"id": report.id, "cat_client_id": cat.client_id, "name": (cat.payload or {}).get("name") or "Meu gato", "latitude": report.latitude, "longitude": report.longitude, "radius_meters": report.radius_meters, "status": report.status, "created_at": report.created_at, "resolved_at": report.resolved_at, "clue_count": count})
    return result


async def _clue(session: AsyncSession, user: User, match_id: UUID):
    row = (await session.execute(select(MatchSuggestion, MissingReport, Sighting).join(MissingReport, MissingReport.id == MatchSuggestion.missing_report_id).join(Sighting, Sighting.id == MatchSuggestion.sighting_id).where(MatchSuggestion.id == match_id, MissingReport.user_id == user.id))).one_or_none()
    if row is None:
        raise HTTPException(404, "Pista não encontrada.")
    return row


class ReportArea(BaseModel):
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
    # Re-encode to strip EXIF/GPS and avoid leaking a private original.
    try:
        with Image.open(photo_storage.open(cat.photo_key)) as original:
            thumbnail = ImageOps.exif_transpose(original).convert("RGB")
            thumbnail.thumbnail((1200, 1200))
            output = BytesIO()
            thumbnail.save(output, format="JPEG", quality=85)
    except (OSError, ValueError):
        raise HTTPException(404, "Foto não disponível.")
    return Response(output.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


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
    from app.routers.social import _member_or_403
    await _member_or_403(session, conversation_id, user.id)
    conversation = await session.get(Conversation, conversation_id)
    other = (await session.execute(select(User).join(ConversationMember, ConversationMember.user_id == User.id).where(ConversationMember.conversation_id == conversation_id, User.id != user.id))).scalar_one_or_none()
    return {"username": other.username if other else None, "sighting_context": conversation.match_suggestion_id is not None}


ACHIEVEMENTS = {
    "first": ("Primeira descoberta", "Fotografe seu primeiro gato em um passeio.", 1),
    "angles": ("Um novo olhar", "Adicione fotos complementares a um gato.", 2),
    "days": ("Olhar atento", "Registre descobertas em três dias diferentes.", 3),
    "helper": ("Rede de cuidado", "Contribua com um avistamento de um gato desaparecido.", 1),
}


@router.get("/journey")
async def journey(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[dict]:
    state = await _state(session, user)
    cats = (await session.execute(select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.deleted_at.is_(None)))).scalars().all()
    discoveries = [cat for cat in cats if (cat.payload or {}).get("is_owned") not in (True, 1)]
    photo_counts = (await session.execute(select(CatPhoto.cat_id, func.count()).where(CatPhoto.cat_id.in_([cat.id for cat in cats]), CatPhoto.kind != "primary", CatPhoto.deleted_at.is_(None)).group_by(CatPhoto.cat_id))).all()
    helper = (await session.execute(select(func.count()).select_from(MatchSuggestion).join(Sighting, Sighting.id == MatchSuggestion.sighting_id).where(Sighting.observer_id == user.id, MatchSuggestion.status == "confirmed"))).scalar_one()
    progress = {"first": len(discoveries), "angles": max([n for _, n in photo_counts], default=0), "days": len({str((cat.payload or {}).get("captured_at", cat.created_at))[:10] for cat in discoveries}), "helper": helper}
    unlocked = dict(state.achievements)
    for key, (_, _, target) in ACHIEVEMENTS.items():
        if progress[key] >= target and key not in unlocked:
            unlocked[key] = datetime.now(UTC).isoformat()
            notification = AppNotification(
                user_id=user.id,
                type="achievement",
                payload={"achievement_id": key, "title": ACHIEVEMENTS[key][0]},
            )
            session.add(notification)
            await session.flush()
            queue_notification(session, notification)
    state.achievements = unlocked
    await session.commit()
    await publish_committed_events(session)
    return [{"id": key, "title": title, "description": description, "target": target, "progress": min(progress[key], target), "unlocked_at": unlocked.get(key)} for key, (title, description, target) in ACHIEVEMENTS.items()]


@router.post("/journey/{achievement_id}/share", status_code=204)
async def share_achievement(achievement_id: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> None:
    state = await _state(session, user)
    if not user.username or achievement_id not in state.achievements or achievement_id not in ACHIEVEMENTS:
        raise HTTPException(409, "Escolha seu nome de usuário e desbloqueie a conquista antes de compartilhar.")
    exists = (await session.execute(select(FeedItem.id).where(FeedItem.user_id == user.id, FeedItem.type == "achievement", FeedItem.payload["achievement_id"].as_string() == achievement_id))).scalar_one_or_none()
    if exists is None:
        session.add(FeedItem(user_id=user.id, type="achievement", payload={"achievement_id": achievement_id, "title": ACHIEVEMENTS[achievement_id][0]}))
    await session.commit()
