"""User-facing views. Private media is shared only within an authorized sighting."""
from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from PIL import Image, ImageOps
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_database_session
from app.persistence_models import (User, RemoteCat, CatPhoto, MissingReport, Sighting,
    MatchSuggestion, Conversation, ConversationMember, Friendship, AppNotification,
    FeedItem, ProductState)
from app.routers.auth import current_user
from app.schemas import FavoritesSyncRequest, FeedbackRequest
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
    report = await session.get(MissingReport, report_id)
    if report is None or report.status != "active":
        raise HTTPException(404, "Alerta não encontrado ou inativo.")
    cat = await session.get(RemoteCat, report.cat_id)
    if cat is None or cat.deleted_at is not None:
        raise HTTPException(404, "Gato não encontrado.")
    photo = (await session.execute(
        select(CatPhoto).where(
            CatPhoto.cat_id == cat.id,
            CatPhoto.kind == "primary",
            CatPhoto.deleted_at.is_(None),
        )
    )).scalar_one_or_none()
    storage_key = photo.storage_key if photo else cat.photo_key
    if not storage_key:
        raise HTTPException(404, "Foto não disponível.")
    try:
        with Image.open(photo_storage.open(storage_key)) as original:
            thumbnail = ImageOps.exif_transpose(original).convert("RGB")
            thumbnail.thumbnail((1200, 1200))
            output = BytesIO()
            thumbnail.save(output, format="JPEG", quality=85)
    except (OSError, ValueError):
        raise HTTPException(404, "Foto não disponível.")
    return Response(output.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=300"})


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
    "first": ("Primeira descoberta", "Fotografe seu primeiro gato em um passeio ao ar livre.", 1),
    "collector_5": ("Olhar Curioso", "Aviste e catalogue 5 gatos diferentes pelo seu bairro.", 5),
    "collector_10": ("Rastreador de Pegadas", "Catalogue 10 gatos diferentes em sua jornada.", 10),
    "master_collector": ("Mestre do Gatálogo", "Reúna 20 gatos catalogados na sua coleção.", 20),
    "legend_collector": ("Enciclopédia Felina", "Alcance a incrível marca de 50 gatos catalogados.", 50),
    "black_cat": ("Pantera Urbana", "Catalogue um gato com pelagem predominantemente preta.", 1),
    "orange_tabby": ("Sol Alaranjado", "Catalogue um gato com pelagem ruiva ou laranja.", 1),
    "calico": ("Mosaico da Sorte", "Catalogue um gato com pelagem tricolor ou cálico.", 1),
    "siamese_pattern": ("Olhar Royal", "Catalogue um gato com padrão siamês ou colorpoint.", 1),
    "bicolor": ("Frajola Elegante", "Catalogue um gato bicolor ou frajola.", 1),
    "neighborhood_3": ("Explorador de Quarteirões", "Registre gatos em pelo menos 3 locais diferentes.", 3),
    "city_wanderer": ("Andarilho Urbano", "Registre gatos em 5 locais diferentes.", 5),
    "night_owl": ("Observador Noturno", "Catalogue um gato no período noturno (após 19h).", 1),
    "early_bird": ("Madrugador", "Catalogue um gato nas primeiras horas da manhã (antes das 8h).", 1),
    "days": ("Olhar Atento", "Registre descobertas em três dias diferentes.", 3),
    "streak_3": ("Ritmo Felino", "Mantenha uma sequência de 3 dias de atividade.", 3),
    "streak_7": ("Semana Guardiã", "Mantenha uma sequência de 7 dias de atividade.", 7),
    "streak_30": ("Devoção Felina", "Mantenha uma sequência lendária de 30 dias de atividade.", 30),
    "angles": ("Um Novo Olhar", "Adicione fotos complementares a um gato.", 2),
    "portrait": ("Retrato Detalhado", "Adicione notas descritivas detalhadas a 3 gatos.", 3),
    "angles_master": ("Galeria Completa", "Adicione 4 ou mais fotos ao perfil de um mesmo gato.", 4),
    "helper": ("Rede de Cuidado", "Contribua com um avistamento ou pista de um gato desaparecido.", 1),
    "guardian_shield": ("Vigilante do Bairro", "Verifique casos de busca ativa para apoiar tutores.", 1),
    "poster_share": ("Voz da Esperança", "Compartilhe um cartaz de busca para mobilizar a comunidade.", 1),
    "social_heart": ("Coração Comunitário", "Curta 5 publicações no feed da comunidade.", 5),
    "commentator": ("Conversa Afiada", "Participe com comentários em 3 publicações.", 3),
    "first_share": ("Espalhando Carinho", "Compartilhe uma descoberta felina com amigos.", 1),
    "owned_pet": ("Meu Companheiro", "Cadastre um gato sob seus cuidados como pet próprio.", 1),
}

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
    (2, "Explorador", 100, 300, "Desbloqueia novos filtros e modos"),
    (3, "Observador", 300, 650, "Bônus de XP de avistamento +5%"),
    (4, "Catalogador", 650, 1200, "Badge exclusiva de perfil"),
    (5, "Guardião", 1200, 2000, "Prioridade em alertas de resgate"),
    (6, "Protetor", 2000, 3200, "Título honorário de proteção"),
    (7, "Lenda Felina", 3200, 5000, "Status máximo da comunidade"),
]


def calculate_level(xp: int) -> tuple[int, str, int, int, str]:
    for lvl, title, min_x, target, reward in LEVELS:
        if xp < target:
            return lvl, title, min_x, target, reward
    return 7, "Lenda Felina", 3200, 5000, "Status máximo da comunidade"


def calculate_multiplier(streak: int) -> float:
    if streak <= 1:
        return 1.0
    if streak == 2:
        return 1.1
    if streak == 3:
        return 1.2
    if streak < 7:
        return 1.3
    return 1.5


ACTION_XP = {
    "photo": 50,
    "sighting": 40,
    "post": 30,
    "comment": 15,
    "like": 5,
    "share": 25,
    "care": 25,
}


def _get_or_init_gamification(state: ProductState) -> dict:
    prefs = dict(state.preferences) if isinstance(state.preferences, dict) else {}
    gamif = prefs.get("gamification")
    today_str = datetime.now(UTC).date().isoformat()
    if not isinstance(gamif, dict):
        gamif = {
            "xp": 0,
            "streak": 0,
            "longest_streak": 0,
            "last_action_date": None,
            "freeze_shields": 1,
            "last_freeze_refill": today_str,
            "missions_date": today_str,
            "daily_missions": [
                {"id": "daily_photo", "title": "Fotografar ou catalogar 1 gato", "target": 1, "progress": 0, "xp_reward": 60, "completed": False},
                {"id": "daily_social", "title": "Interagir em 2 publicações da comunidade", "target": 2, "progress": 0, "xp_reward": 40, "completed": False},
                {"id": "daily_care", "title": "Verificar área de busca ou atualizar fotos", "target": 1, "progress": 0, "xp_reward": 50, "completed": False},
            ],
            "weekly_mission": {
                "id": "weekly_sightings",
                "title": "Registrar avistamentos em 3 dias diferentes",
                "target": 3,
                "progress": 0,
                "xp_reward": 150,
                "completed": False,
                "week_str": f"{datetime.now(UTC).year}-W{datetime.now(UTC).isocalendar()[1]}",
            },
            "bonus_mission": {
                "id": "bonus_share",
                "title": "Compartilhar uma descoberta ou cartaz de desaparecimento",
                "target": 1,
                "progress": 0,
                "xp_reward": 80,
                "completed": False,
            },
        }
        prefs["gamification"] = dict(gamif)
        state.preferences = prefs
        flag_modified(state, "preferences")
    else:
        changed = False
        if gamif.get("missions_date") != today_str:
            gamif["missions_date"] = today_str
            gamif["daily_missions"] = [
                {"id": "daily_photo", "title": "Fotografar ou catalogar 1 gato", "target": 1, "progress": 0, "xp_reward": 60, "completed": False},
                {"id": "daily_social", "title": "Interagir em 2 publicações da comunidade", "target": 2, "progress": 0, "xp_reward": 40, "completed": False},
                {"id": "daily_care", "title": "Verificar área de busca ou atualizar fotos", "target": 1, "progress": 0, "xp_reward": 50, "completed": False},
            ]
            changed = True
        last_refill = gamif.get("last_freeze_refill")
        if last_refill:
            try:
                refill_date = datetime.fromisoformat(last_refill).date()
                if (datetime.now(UTC).date() - refill_date).days >= 7:
                    gamif["freeze_shields"] = min(gamif.get("freeze_shields", 0) + 1, 2)
                    gamif["last_freeze_refill"] = today_str
                    changed = True
            except Exception:
                gamif["last_freeze_refill"] = today_str
                changed = True
        current_week_str = f"{datetime.now(UTC).year}-W{datetime.now(UTC).isocalendar()[1]}"
        if gamif.get("weekly_mission", {}).get("week_str") != current_week_str:
            gamif["weekly_mission"] = {
                "id": "weekly_sightings",
                "title": "Registrar avistamentos em 3 dias diferentes",
                "target": 3,
                "progress": 0,
                "xp_reward": 150,
                "completed": False,
                "week_str": current_week_str,
            }
            changed = True
        if changed:
            prefs["gamification"] = dict(gamif)
            state.preferences = prefs
            flag_modified(state, "preferences")
    return gamif


@router.get("/gamification")
async def get_gamification(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    state = await _state(session, user)
    gamif = _get_or_init_gamification(state)
    xp = gamif.get("xp", 0)
    lvl, title, min_xp, target_xp, reward = calculate_level(xp)
    streak = gamif.get("streak", 0)
    multiplier = calculate_multiplier(streak)
    unlocked_count = len(state.achievements)
    total_achievements = len(ACHIEVEMENTS)
    await session.commit()
    return {
        "xp": xp,
        "new_xp": xp,
        "level": lvl,
        "level_title": title,
        "level_min_xp": min_xp,
        "target_xp": target_xp,
        "level_reward": reward,
        "streak": streak,
        "longest_streak": gamif.get("longest_streak", 0),
        "multiplier": multiplier,
        "freeze_shields": gamif.get("freeze_shields", 1),
        "last_action_date": gamif.get("last_action_date"),
        "daily_missions": gamif.get("daily_missions", []),
        "weekly_mission": gamif.get("weekly_mission"),
        "bonus_mission": gamif.get("bonus_mission"),
        "unlocked_achievements": unlocked_count,
        "total_achievements": total_achievements,
    }


class GamificationActionRequest(BaseModel):
    action: str
    context_id: str | None = None


@router.post("/gamification/action")
async def record_gamification_action(
    payload: GamificationActionRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    state = await _state(session, user)
    gamif = _get_or_init_gamification(state)
    today = datetime.now(UTC).date()
    today_str = today.isoformat()

    last_action = gamif.get("last_action_date")
    current_streak = gamif.get("streak", 0)
    freeze_shields = gamif.get("freeze_shields", 1)

    if last_action is None:
        current_streak = 1
        gamif["last_action_date"] = today_str
    elif last_action == today_str:
        pass
    else:
        last_date = datetime.fromisoformat(last_action).date()
        diff_days = (today - last_date).days
        if diff_days == 1:
            current_streak += 1
            gamif["last_action_date"] = today_str
        elif diff_days == 2 and freeze_shields > 0:
            freeze_shields -= 1
            gamif["freeze_shields"] = freeze_shields
            current_streak += 1
            gamif["last_action_date"] = today_str
        else:
            current_streak = 1
            gamif["last_action_date"] = today_str

    gamif["streak"] = current_streak
    gamif["longest_streak"] = max(gamif.get("longest_streak", 0), current_streak)

    multiplier = calculate_multiplier(current_streak)
    base_xp = ACTION_XP.get(payload.action, 10)
    earned_xp = int(base_xp * multiplier)

    prev_xp = gamif.get("xp", 0)
    prev_lvl, _, _, _, _ = calculate_level(prev_xp)
    new_xp = prev_xp + earned_xp

    missions_completed_now = []
    for m in gamif.get("daily_missions", []):
        if not m.get("completed", False):
            advances = False
            if m["id"] == "daily_photo" and payload.action in ("photo",):
                advances = True
            elif m["id"] == "daily_social" and payload.action in ("like", "comment", "post"):
                advances = True
            elif m["id"] == "daily_care" and payload.action in ("sighting", "photo", "share", "care"):
                advances = True
            if advances:
                m["progress"] = min(m["progress"] + 1, m["target"])
                if m["progress"] >= m["target"]:
                    m["completed"] = True
                    reward_xp = m.get("xp_reward", 0)
                    new_xp += reward_xp
                    earned_xp += reward_xp
                    missions_completed_now.append(m["title"])

    wm = gamif.get("weekly_mission")
    if wm and not wm.get("completed", False):
        if payload.action in ("photo", "sighting"):
            wm["progress"] = min(wm["progress"] + 1, wm["target"])
            if wm["progress"] >= wm["target"]:
                wm["completed"] = True
                reward_xp = wm.get("xp_reward", 0)
                new_xp += reward_xp
                earned_xp += reward_xp
                missions_completed_now.append(wm["title"])

    bm = gamif.get("bonus_mission")
    if bm and not bm.get("completed", False):
        if payload.action == "share":
            bm["progress"] = min(bm["progress"] + 1, bm["target"])
            if bm["progress"] >= bm["target"]:
                bm["completed"] = True
                reward_xp = bm.get("xp_reward", 0)
                new_xp += reward_xp
                earned_xp += reward_xp
                missions_completed_now.append(bm["title"])

    gamif["xp"] = new_xp
    new_lvl, new_title, min_xp, target_xp, reward = calculate_level(new_xp)
    level_up = new_lvl > prev_lvl

    prefs = dict(state.preferences or {})
    prefs["gamification"] = dict(gamif)
    state.preferences = prefs
    flag_modified(state, "preferences")
    await session.commit()

    return {
        "earned_xp": earned_xp,
        "multiplier": multiplier,
        "xp": new_xp,
        "new_xp": new_xp,
        "level": new_lvl,
        "level_title": new_title,
        "level_min_xp": min_xp,
        "target_xp": target_xp,
        "level_reward": reward,
        "level_up": level_up,
        "streak": current_streak,
        "longest_streak": gamif.get("longest_streak", current_streak),
        "freeze_shields": gamif.get("freeze_shields", freeze_shields),
        "missions_completed": missions_completed_now,
        "daily_missions": gamif.get("daily_missions", []),
        "weekly_mission": wm,
        "bonus_mission": bm,
    }


@router.get("/journey")
async def journey(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[dict]:
    state = await _state(session, user)
    gamif = _get_or_init_gamification(state)
    cats = (await session.execute(select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.deleted_at.is_(None)))).scalars().all()
    discoveries = [cat for cat in cats if (cat.payload or {}).get("is_owned") not in (True, 1)]
    owned_cats = [cat for cat in cats if (cat.payload or {}).get("is_owned") in (True, 1)]
    photo_counts = (await session.execute(select(CatPhoto.cat_id, func.count()).where(CatPhoto.cat_id.in_([cat.id for cat in cats]), CatPhoto.kind != "primary", CatPhoto.deleted_at.is_(None)).group_by(CatPhoto.cat_id))).all()
    helper = (await session.execute(select(func.count()).select_from(MatchSuggestion).join(Sighting, Sighting.id == MatchSuggestion.sighting_id).where(Sighting.observer_id == user.id, MatchSuggestion.status == "confirmed"))).scalar_one()

    # Coat colors and types for diversity achievements
    def _cat_text(cat: RemoteCat) -> str:
        p = cat.payload or {}
        name = p.get("name") or p.get("custom_name") or getattr(cat, "name", "") or ""
        return f"{p.get('primary_color', '')} {p.get('coat_type', '')} {name}".lower()

    black_cats = sum(1 for c in discoveries if any(w in _cat_text(c) for w in ("preto", "black", "pantera", "ébano")))
    orange_cats = sum(1 for c in discoveries if any(w in _cat_text(c) for w in ("laranja", "ruivo", "amarelo", "red", "orange", "ginger")))
    calico_cats = sum(1 for c in discoveries if any(w in _cat_text(c) for w in ("calico", "cálico", "tricolor", "casco", "tortie")))
    siamese_cats = sum(1 for c in discoveries if any(w in _cat_text(c) for w in ("siamês", "siames", "colorpoint", "seal", "pointed")))
    bicolor_cats = sum(1 for c in discoveries if any(w in _cat_text(c) for w in ("bicolor", "frajola", "tuxedo", "preto e branco", "malhado")))

    locations = {str((c.payload or {}).get("location_name")).strip() for c in discoveries if (c.payload or {}).get("location_name")}
    
    # Check capture hours
    night_cats = 0
    morning_cats = 0
    for c in discoveries:
        cap = (c.payload or {}).get("captured_at")
        if cap:
            try:
                dt = datetime.fromisoformat(str(cap))
                if dt.hour >= 19 or dt.hour < 5:
                    night_cats += 1
                elif 5 <= dt.hour < 8:
                    morning_cats += 1
            except Exception:
                pass

    detailed_notes = sum(1 for c in discoveries if len(str((c.payload or {}).get("note") or "")) >= 10)
    streak_val = max(gamif.get("streak", 0), gamif.get("longest_streak", 0))

    progress = {
        "first": len(discoveries),
        "collector_5": len(discoveries),
        "collector_10": len(discoveries),
        "master_collector": len(discoveries),
        "legend_collector": len(discoveries),
        "black_cat": black_cats,
        "orange_tabby": orange_cats,
        "calico": calico_cats,
        "siamese_pattern": siamese_cats,
        "bicolor": bicolor_cats,
        "neighborhood_3": len(locations),
        "city_wanderer": len(locations),
        "night_owl": night_cats,
        "early_bird": morning_cats,
        "days": len({str((cat.payload or {}).get("captured_at") or cat.created_at)[:10] for cat in discoveries}),
        "streak_3": streak_val,
        "streak_7": streak_val,
        "streak_30": streak_val,
        "angles": max([n for _, n in photo_counts], default=0),
        "portrait": detailed_notes,
        "angles_master": max([n for _, n in photo_counts], default=0),
        "helper": helper,
        "guardian_shield": helper,
        "poster_share": 1 if gamif.get("bonus_mission", {}).get("completed") else 0,
        "social_heart": gamif.get("xp", 0) // 10,
        "commentator": gamif.get("xp", 0) // 15,
        "first_share": 1 if gamif.get("bonus_mission", {}).get("completed") else 0,
        "owned_pet": len(owned_cats),
    }

    unlocked = dict(state.achievements)
    achievements_unlocked_now = 0
    for key, (title, _, target) in ACHIEVEMENTS.items():
        val = progress.get(key, 0)
        if val >= target and key not in unlocked:
            unlocked[key] = datetime.now(UTC).isoformat()
            ach_xp = ACHIEVEMENT_XP.get(key, 50)
            gamif["xp"] = gamif.get("xp", 0) + ach_xp
            achievements_unlocked_now += 1
            notification = AppNotification(
                user_id=user.id,
                type="achievement",
                payload={"achievement_id": key, "title": title, "xp_reward": ach_xp},
            )
            session.add(notification)
            await session.flush()
            queue_notification(session, notification)

    if achievements_unlocked_now > 0:
        prefs = dict(state.preferences or {})
        prefs["gamification"] = dict(gamif)
        state.preferences = prefs
        flag_modified(state, "preferences")

    state.achievements = unlocked
    flag_modified(state, "achievements")
    await session.commit()
    await publish_committed_events(session)
    return [
        {
            "id": key,
            "title": title,
            "description": description,
            "target": target,
            "progress": min(progress.get(key, 0), target),
            "unlocked_at": unlocked.get(key),
            "xp_reward": ACHIEVEMENT_XP.get(key, 50),
        }
        for key, (title, description, target) in ACHIEVEMENTS.items()
    ]


@router.post("/journey/{achievement_id}/share", status_code=204)
async def share_achievement(achievement_id: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> None:
    state = await _state(session, user)
    if not user.username or achievement_id not in state.achievements or achievement_id not in ACHIEVEMENTS:
        raise HTTPException(409, "Escolha seu nome de usuário e desbloqueie a conquista antes de compartilhar.")
    exists = (await session.execute(select(FeedItem.id).where(FeedItem.user_id == user.id, FeedItem.type == "achievement", FeedItem.payload["achievement_id"].as_string() == achievement_id))).scalar_one_or_none()
    if exists is None:
        session.add(FeedItem(user_id=user.id, type="achievement", payload={"achievement_id": achievement_id, "title": ACHIEVEMENTS[achievement_id][0]}))
    await session.commit()


@router.get("/account/export")
async def export_user_data(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    state = await _state(session, user)
    cats = (await session.execute(select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.deleted_at.is_(None)))).scalars().all()
    reports = (await session.execute(select(MissingReport).where(MissingReport.user_id == user.id))).scalars().all()
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
                "cat_client_id": str(r.cat_client_id),
                "latitude": r.latitude,
                "longitude": r.longitude,
                "radius_meters": r.radius_meters,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reports
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
async def get_favorites(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[str]:
    state = await _state(session, user)
    return list(state.preferences.get("favorites", []))


@router.put("/favorites")
async def update_favorites(payload: FavoritesSyncRequest, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[str]:
    state = await _state(session, user)
    prefs = dict(state.preferences or {})
    prefs["favorites"] = list(set(payload.favorites))
    state.preferences = prefs
    flag_modified(state, "preferences")
    await session.commit()
    return prefs["favorites"]


