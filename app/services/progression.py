"""Confirmed progression. Callers supply evidence from server operations, never XP.

All grants share the user lock and the caller's transaction with the operation
that caused them. Results survive retries and deletion of the source record.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from app.persistence_models import (User, ProductState, ProgressEvent, HabitatTransaction,
                                    HabitatItem, RemoteCat, AppNotification)
from app.services.habitat_service import wallet_for, SHOP
from app.services.realtime_events import queue_notification
from app.config import get_settings

from app.quintal_models import QuintalFacility
from app.services import milestones

log = logging.getLogger(__name__)
RULE_VERSION = 2
REWARDS = {"discovery": (50, 25), "complement": (5, 3), "clue": (100, 50),
           "repost": (5, 2), "post": (10, 3), "comment": (3, 1), "care": (0, 0), "review": (0, 0), "encounter": (0, 0)}
LIMITS = {"post": 1, "comment": 3, "repost": 2, "complement": 2}
CHALLENGES = tuple((mid, title, desc, target, xp, coins)
                   for mid, title, desc, metric, target, xp, coins, points in milestones.ACHIEVEMENTS)
LEVELS = ((1, "Curioso", 0, 100), (2, "Explorador", 100, 300), (3, "Observador", 300, 650),
          (4, "Catalogador", 650, 1200), (5, "Guardião", 1200, 2000),
          (6, "Protetor", 2000, 3200), (7, "Lenda Felina", 3200, 5000))


def level_for(xp, floor=1):
    level = next((row for row in LEVELS if xp < row[3]), LEVELS[-1])
    return LEVELS[max(level[0], min(7, floor)) - 1]


def zone(prefs):
    try:
        return ZoneInfo(prefs.get("timezone", "America/Sao_Paulo"))
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


async def account(session, user_id):
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())
    state = await session.get(ProductState, user_id)
    if state is None:
        state = ProductState(user_id=user_id, preferences={}, achievements={})
        session.add(state)
        await session.flush()
    prefs = dict(state.preferences or {})
    wallet = await wallet_for(session, user_id)
    if not prefs.get("progression_v1"):
        old = dict(prefs.get("gamification", {}))
        xp = int(old.get("xp", 0))
        session.add(ProgressEvent(user_id=user_id, event_key="opening:v1", kind="opening",
            state="confirmed", xp=xp, coins=wallet.balance,
            evidence={"migration": True}, result={"reason": "Saldo preservado"}))
        prefs["progression_v1"] = True
        for section in ("gallery", "medals", "habitat"):
            prefs.setdefault(f"{section}_audience", "friends")
        old["highest_level"] = max(old.get("highest_level", 1), old.get("level", 1), level_for(xp)[0])
        old["cosmetic_level"] = old["highest_level"]
        for key in (state.achievements or {}):
            session.add(ProgressEvent(user_id=user_id, event_key=f"challenge:{key}", kind="challenge",
                state="confirmed", xp=0, coins=0, evidence={"migration": True}, result={}))
        prefs["gamification"] = old
        state.preferences = prefs
        await session.flush()
    prefs = dict(state.preferences or {})
    today = milestones.calendar()["day"]
    offers = dict(prefs.get("milestone_daily_offers", {}))
    if today not in offers:
        offers[today] = "care" if wallet.selected_cat_ids else "album"
        prefs["milestone_daily_offers"] = dict(sorted(offers.items())[-35:])
    # These offers derive only from server-owned unlocks, never client preferences.
    facilities = list((await session.scalars(select(QuintalFacility).where(
        QuintalFacility.user_id == user_id, QuintalFacility.level > 0))).all())
    prefs["quintal_routine_spaces"] = sorted({row.kind for row in facilities} |
                                            ({"cats"} if wallet.selected_cat_ids else set()))
    prefs["preserved_achievements"] = dict(state.achievements or {})
    state.preferences = prefs
    return state, wallet


async def events_for(session, user_id):
    return list((await session.scalars(select(ProgressEvent).where(
        ProgressEvent.user_id == user_id).order_by(ProgressEvent.created_at, ProgressEvent.id))).all())


def projections(events, prefs, now=None):
    view = milestones.project(events, prefs, now)
    return view["daily_missions"] + view["weekly_missions"] + view["initial_missions"], view["achievements"], view["confirmed_individuals"]


async def snapshot(session, user_id):
    state, wallet = await account(session, user_id)
    gamif = state.preferences.get("gamification", {})
    xp = int(gamif.get("xp", 0))
    level, title, minimum, target = level_for(xp, gamif.get("highest_level", 1))
    events = await events_for(session, user_id)
    view = milestones.project(events, state.preferences)
    owned = set((await session.scalars(select(HabitatItem.sku).where(HabitatItem.user_id == user_id))).all())
    goal = next(((sku, item) for sku, item in sorted(SHOP.items(), key=lambda pair: pair[1]["price"])
                 if item["price"] > 0 and sku not in owned), None)
    return dict(xp=xp, new_xp=xp, balance=wallet.balance, level=level, level_title=title,
                level_min_xp=minimum, target_xp=target, level_reward="Selo e título do álbum",
                streak=gamif.get("longest_streak", gamif.get("streak", 0)), multiplier=1.0,
                freeze_shields=0, **view,
                weekly_mission=view["weekly_missions"][0], bonus_mission=view["weekly_missions"][1],
                revision=len(events), as_of=datetime.now(UTC).isoformat(), rewards_enabled=enabled(user_id),
                ranking_scores=milestones.scores(events, state.preferences),
                highlighted_medals=state.preferences.get("highlighted_medals", []),
                rule_version=RULE_VERSION,
                unlocked_achievements=len(state.achievements or {}), total_achievements=len(view["achievements"]),
                next_habitat_goal={"title": goal[1]["name"], "price": goal[1]["price"], "balance": wallet.balance,
                                   "sku": goal[0]} if goal else None)


def enabled(user_id):
    settings = get_settings()
    bucket = int(hashlib.sha256(str(user_id).encode()).hexdigest()[:8], 16) % 100
    return settings.progression_rewards_enabled and bucket < settings.progression_rollout_percent


async def grant(session, user_id, key, kind, *, record_id=None, evidence=None, photo_hash=None,
                xp=None, coins=None):
    state, wallet = await account(session, user_id)
    previous = await session.scalar(select(ProgressEvent).where(
        ProgressEvent.user_id == user_id, ProgressEvent.event_key == key))
    if previous is not None:
        return previous
    evidence = dict(evidence or {})
    local = datetime.now(UTC).astimezone(milestones.CALENDAR)
    evidence.setdefault("local_day", local.date().isoformat())
    evidence.setdefault("local_week", (local.date()-timedelta(days=local.weekday())).isoformat())
    eligible = evidence.get("eligible", True)
    all_events = await events_for(session, user_id)
    if photo_hash and kind in {"discovery", "complement"}:
        eligible = eligible and not any(e.photo_hash == photo_hash and e.kind in {"discovery", "complement", "legacy"}
                                        for e in all_events)
    evidence["activity_valid"] = bool(eligible)
    capped = kind in LIMITS and sum(e.kind == kind and e.evidence.get("eligible") and
        not e.evidence.get("base_reward_capped") and e.evidence.get("local_day") == evidence["local_day"] for e in all_events) >= LIMITS[kind]
    evidence["base_reward_capped"] = capped
    if kind != "correction" and not enabled(user_id):
        eligible = False
        evidence["reason"] = "Novas recompensas ainda não estão ativas nesta conta."
    evidence["eligible"] = bool(eligible)
    base_xp, base_coins = (0, 0) if capped else REWARDS.get(kind, (0, 0))
    xp = (base_xp if xp is None else xp) if eligible else 0
    coins = (base_coins if coins is None else coins) if eligible else 0
    event = ProgressEvent(user_id=user_id, event_key=key, kind=kind, record_id=record_id,
        state=("needs_review" if evidence.get("attributes_confirmed") is False else "confirmed") if eligible else ("recorded" if evidence["activity_valid"] else "ineligible"),
        xp=xp, coins=coins, photo_hash=photo_hash, evidence=evidence, rule_version=RULE_VERSION, result={})
    session.add(event)
    await session.flush()
    if xp or coins:
        gamif = dict(state.preferences.get("gamification", {}))
        gamif["xp"] = int(gamif.get("xp", 0)) + xp
        gamif["highest_level"] = level_for(gamif["xp"], gamif.get("highest_level", 1))[0]
        state.preferences = {**state.preferences, "gamification": gamif}
        wallet.balance += coins
        session.add(HabitatTransaction(user_id=user_id, event_key=f"progress:{event.id}",
            category=kind, amount=coins, photo_hash=photo_hash, details={"event_id": str(event.id), "xp": xp, "rule_version": RULE_VERSION}))
    await session.flush()
    log.info("progression event=%s kind=%s state=%s xp=%s coins=%s", event.id, kind, event.state, xp, coins)
    return event


async def finish(session, event):
    if not event.evidence.get("eligible"):
        event.result = {"summary": await snapshot(session, event.user_id)}
        return event
    state, _ = await account(session, event.user_id)
    missions, challenges, _ = projections(await events_for(session, event.user_id), state.preferences)
    bonuses = list((event.result or {}).get("bonuses", []))
    for m in missions + challenges:
        if m["progress"] < m["target"] or m["completed"]:
            continue
        kind = "achievement" if m["type"] == "achievement" else "mission"
        key = m.get("event_key", f"challenge:{m['id']}")
        bonus = await grant(session, event.user_id, key, kind, record_id=event.record_id,
                            evidence={"source_event_id": str(event.id), "objective_type": m["type"], "ranking_points": m["ranking_points"]}, xp=m["xp_reward"], coins=m.get("coin_reward", 0))
        bonuses.append({"id": m["id"], "title": m["title"], "type": m["type"], "description": m["description"], "xp": bonus.xp, "coins": bonus.coins, "event_id": str(bonus.id)})
        if kind == "achievement" and bonus.state == "confirmed":
            state.achievements = {**(state.achievements or {}), m["id"]: datetime.now(UTC).isoformat()}
    unlocked = list((event.result or {}).get("unlocked_items", []))
    gamif = dict(state.preferences.get("gamification", {}))
    # A cosmetic milestone is delivered once, in the same transaction as XP.
    cosmetics = {2: "yellow_flowers", 3: "soft_grass", 4: "leafy_plant", 5: "oak_tree", 6: "large_log", 7: "detailed_tree"}
    for level in range(gamif.get("cosmetic_level", 1) + 1, gamif.get("highest_level", 1) + 1):
        sku = cosmetics[level]
        key = f"level:{level}:v1"
        if not await session.scalar(select(HabitatItem.id).where(HabitatItem.user_id == event.user_id,
                                                               HabitatItem.purchase_key == key)):
            session.add(HabitatItem(user_id=event.user_id, sku=sku, purchase_key=key))
            unlocked.append({"sku": sku, "name": SHOP[sku]["name"], "level": level})
    gamif["cosmetic_level"] = gamif.get("highest_level", 1)
    state.preferences = {**state.preferences, "gamification": gamif}
    await session.flush()
    summary = await snapshot(session, event.user_id)
    event.result = {**(event.result or {}), "summary": summary, "bonuses": bonuses, "unlocked_items": unlocked}
    if event.kind in {"care", "review", "encounter", "quintal"} and not bonuses:
        return event
    existing_notice = await session.scalar(select(AppNotification).where(
        AppNotification.user_id == event.user_id, AppNotification.type == "progress_result",
        AppNotification.payload["event_id"].as_string() == str(event.id)))
    if existing_notice is None and event.kind != "clue":
        notice = AppNotification(user_id=event.user_id, type="progress_result",
            payload={"event_id": str(event.id), "cat_client_id": event.record_id, "record_type": event.kind})
        session.add(notice)
        await session.flush()
        queue_notification(session, notice)
    return event


def result_for(event):
    return {"event_id": str(event.id), "state": event.state, "record_type": event.kind,
            "record_id": event.record_id, "rule_version": event.rule_version,
            "individual_id": event.evidence.get("individual_id"),
            "attributes": event.evidence.get("attributes", {}),
            "attributes_confirmed": event.evidence.get("attributes_confirmed", False),
            "credits": dict(zip(("xp", "coins"), event.evidence.get("current_credit", [event.xp, event.coins]))),
            "reason": event.evidence.get("reason"), "created_at": event.created_at,
            **(event.result or {})}


async def confirm_photo(session, cat, photo, analysis, *, reference=False):
    """Only called after a successful server analysis, never from sync payloads."""
    await account(session, cat.user_id)
    events = await events_for(session, cat.user_id)
    individual = str((cat.payload or {}).get("individual_id") or cat.client_id)
    owned = (cat.payload or {}).get("is_owned") in (True, 1)
    if cat.payload.get("identity_state") == "pending" and not reference:
        event = await grant(session, cat.user_id, f"pending:{cat.client_id}:{photo.content_hash}", "pending_identity",
            record_id=str(cat.client_id), evidence={"eligible": False, "photo_valid": True,
                "attributes": {k: analysis.get(k) for k in ("primary_color", "coat_type", "hair_type")},
                "attributes_confirmed": float(analysis.get("confidence", 0)) >= .7 and not analysis.get("warnings"),
                "validated_by": "server_analysis", "reason": "Foto salva. Confirme se é um gato novo ou conhecido."}, photo_hash=photo.content_hash)
        event.state = "pending"
        await finish(session, event)
        return event
    seen = any(e.kind in {"discovery", "legacy"} and (e.kind == "legacy" or e.evidence.get("eligible") or e.evidence.get("activity_valid")) and
               e.evidence.get("individual_id", e.record_id) == individual for e in events)
    kind = "complement" if reference or seen or individual != str(cat.client_id) else "discovery"
    if kind == "complement" and not reference and cat.payload.get("identity_state") == "confirmed" and cat.payload.get("contribution_type") == "encounter":
        kind = "encounter"
    key = f"reference:{cat.client_id}:{photo.kind}" if reference else f"photo:{cat.client_id}:{photo.content_hash}"
    uncertain = float(analysis.get("confidence", 0)) < .7 or bool(analysis.get("warnings"))
    attrs = {k: analysis.get(k) for k in ("primary_color", "coat_type", "hair_type")}
    event = await grant(session, cat.user_id, key, kind, record_id=str(cat.client_id),
        photo_hash=photo.content_hash, evidence={"eligible": not owned or reference,
            "individual_id": individual, "attributes": attrs, "attributes_confirmed": not uncertain,
            "validated_by": "server_analysis", "reason": "Cadastro próprio não é descoberta." if owned and not reference else None})
    if not event.result:
        await finish(session, event)
    return event


async def identify(session, cat, chosen, contribution_type=None):
    """Apply online and queued identity choices through the same audited path."""
    from fastapi import HTTPException
    old = str(cat.payload.get("individual_id") or cat.client_id)
    chosen = str(chosen or cat.client_id)
    pending_identity = cat.payload.get("identity_state") == "pending"
    if old == chosen and not pending_identity:
        return chosen
    if chosen != str(cat.client_id):
        try:
            target_id = UUID(chosen)
        except ValueError:
            raise HTTPException(422, "Indivíduo inválido.")
        target = await session.scalar(select(RemoteCat).where(RemoteCat.user_id == cat.user_id,
            RemoteCat.client_id == target_id, RemoteCat.deleted_at.is_(None)))
        if target is None:
            raise HTTPException(422, "O gato conhecido precisa estar no seu catálogo.")
        chosen = str(target.payload.get("individual_id") or target.client_id)
    if old == chosen and not pending_identity:
        return chosen
    revision = int(cat.payload.get("identity_revision", 0)) + 1
    cat.payload = {**cat.payload, "individual_id": chosen, "identity_revision": revision, "identity_state": "confirmed", "contribution_type": contribution_type or cat.payload.get("contribution_type", "encounter" if pending_identity else "angle")}
    all_events = await events_for(session, cat.user_id)
    pending = [e for e in all_events if e.record_id == str(cat.client_id) and e.kind == "pending_identity" and e.state == "pending"]
    for prior in pending:
        already = any(e.kind == "discovery" and (e.evidence.get("eligible") or e.evidence.get("activity_valid")) and
                      (e.evidence.get("individual_id") or e.record_id) == chosen for e in all_events)
        kind = "discovery" if chosen == str(cat.client_id) and not already and not cat.payload.get("is_owned") else "complement" if contribution_type in {"angle", "detail", "clearer"} else "encounter"
        new = await grant(session, cat.user_id, f"identity-confirm:{prior.id}", kind, record_id=str(cat.client_id),
            photo_hash=prior.photo_hash, evidence={**prior.evidence, "eligible": not cat.payload.get("is_owned") or kind == "complement",
                "individual_id": chosen, "contribution_type": contribution_type, "reason": None})
        prior.state = "resolved"
        await finish(session, new)
        all_events.append(new)
    rows = [e for e in await events_for(session, cat.user_id)
            if e.record_id == str(cat.client_id) and e.kind in ("discovery", "complement") and not pending_identity]
    for index, event in enumerate(rows):
        if not event.evidence.get("eligible"):
            continue
        kind = "discovery" if chosen == str(cat.client_id) and index == 0 and not cat.payload.get("is_owned") else "complement"
        old_xp, old_coins = event.evidence.get("current_credit", [event.xp, event.coins])
        xp, coins = REWARDS[kind]
        await grant(session, cat.user_id, f"identity:{event.id}:{revision}", "correction",
            record_id=str(cat.client_id), evidence={"source_event_id": str(event.id),
                "reason": "Identificação corrigida pela pessoa"}, xp=xp-old_xp, coins=coins-old_coins)
        event.kind = kind
        event.evidence = {**event.evidence, "individual_id": chosen, "current_credit": [xp, coins]}
        event.result = {**event.result, "identity_corrected": True}
    await session.flush()
    return chosen


async def reject_photo(session, user_id, client_id, image_bytes, reason):
    """Keep a failed eligibility decision visible after reopening the app."""
    try:
        client_id = UUID(client_id)
    except ValueError:
        return
    await account(session, user_id)
    cat = await session.scalar(select(RemoteCat).where(RemoteCat.user_id == user_id,
        RemoteCat.client_id == client_id, RemoteCat.deleted_at.is_(None)))
    if cat is None:
        return
    digest = hashlib.sha256(image_bytes).hexdigest()
    event = await grant(session, user_id, f"photo:{client_id}:{digest}", "discovery",
        record_id=str(client_id), photo_hash=digest, evidence={"eligible": False, "reason": reason})
    if not event.result:
        await finish(session, event)
    await session.commit()
