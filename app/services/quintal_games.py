"""Authoritative timed game sessions; replaying a result never grants again.

Trace validation is a consistency check, not proof against a modified client.
"""
import math
import random
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from sqlalchemy import select
from app.quintal_models import QuintalSession
from app.persistence_models import HabitatTransaction
from app.services.habitat_service import wallet_for
from app.services.quintal_production import utc, iso, day, levels, counter, stock, owned_cat, cat_care, bond, play_care_bonus

def fish_position(state, seconds):
    return .5 + .28 * math.sin(seconds * state["speed"] + state["phase"]) + .1 * math.sin(seconds * .7)

def validate_trace(kind, state, trace):
    if not isinstance(trace, list) or not 16 <= len(trace) <= 200: raise ValueError("Interação insuficiente.")
    previous, inside, inputs = -1, 0., set()
    total_motion, last_position = 0., None
    for point in trace:
        if not isinstance(point, dict): raise ValueError("Interação inválida.")
        t, position = point.get("t"), point.get("position")
        if type(t) not in (float, int) or type(position) not in (float, int) or not math.isfinite(t) or not math.isfinite(position) or not 0 <= position <= 1 or t <= previous or t - previous > 2.1:
            raise ValueError("Sequência de interação inválida.")
        if last_position is not None:
            total_motion += abs(position - last_position)
        inputs.add(round(position, 1))
        if kind == "fish" and previous >= 0 and abs(position - fish_position(state, t)) <= state["zone"] / 2:
            inside += t - previous
        previous, last_position = t, position
    if trace[0]["t"] > 1 or not 15 <= previous <= 30: raise ValueError("Duração da interação inválida.")
    if len(inputs) < 3 or total_motion < .5: raise ValueError("Movimente o controle para completar a atividade.")
    return kind == "play" or inside / previous >= .65

async def session_operation(session, user_id, action, target, data, key, now=None, rng=None):
    now, rng = now or datetime.now(UTC), rng or random.SystemRandom()
    wallet = await wallet_for(session, user_id)
    event_key = f"quintal:{key}"
    request = {"action": action, "target": target, "data": data}
    old = await session.scalar(select(HabitatTransaction).where(HabitatTransaction.user_id == user_id, HabitatTransaction.event_key == event_key))
    if old:
        if old.details["request"] != request: raise ValueError("Chave já usada em outra atividade.")
        return old.details["result"]
    kind = "fish" if "fish" in action else "play"
    if action.startswith("start_"):
        lv = await levels(session, user_id)
        if kind == "fish" and lv.get("pier", 0) < 1: raise ValueError("Conserte o píer primeiro.")
        if kind == "play": await owned_cat(session, wallet, target)
        active = await session.scalar(select(QuintalSession).where(QuintalSession.user_id == user_id, QuintalSession.kind == kind).order_by(QuintalSession.started_at.desc()))
        if active and not active.state.get("result") and now - utc(active.started_at) < timedelta(minutes=5):
            active.state = {**active.state, "result": {"success": False, "quantity": 0, "abandoned": True}}
        state = {"target": target, "zone": .30 if lv.get("rod", 1) == 1 else .375,
            "rod": lv.get("rod", 1), "speed": rng.uniform(.2, .4), "phase": rng.uniform(0, math.tau),
            "bite_seconds": rng.randint(3, 8), "practice": await counter(session, user_id, "fish_day:" + day(now)) >= 12}
        game = QuintalSession(id=uuid4(), user_id=user_id, kind=kind, started_at=now, state=state)
        session.add(game)
        result = {"session_id": str(game.id), "started_at": iso(now), **state}
    else:
        try: game = await session.get(QuintalSession, UUID(target))
        except ValueError as error: raise ValueError("Sessão inválida.") from error
        if not game or game.user_id != user_id or game.kind != kind: raise ValueError("Sessão inexistente.")
        if game.state.get("result"): return game.state["result"]
        elapsed = (now - utc(game.started_at)).total_seconds()
        trace = data.get("trace", [])
        success = validate_trace(kind, game.state, trace)
        wait = game.state["bite_seconds"] if kind == "fish" else 0
        if not trace or not wait + 15 <= elapsed <= 300 or trace[-1].get("t", 999) > elapsed - wait + 1: raise ValueError("Espere e interaja durante a atividade.")
        quantity = 0
        care_effect = None
        if success and kind == "fish":
            metric = "fish_day:" + day(now)
            if not game.state["practice"] and await counter(session, user_id, metric) < 12:
                quantity = 2 if game.state["rod"] >= 3 and rng.random() < .2 else 1
                await stock(session, user_id, {"fish": quantity})
                await counter(session, user_id, metric, 1)
                await counter(session, user_id, "fish", 1, occurred_at=now)
        elif success:
            cat_id = await owned_cat(session, wallet, game.state["target"])
            care = await cat_care(session, wallet, cat_id)
            previous_bond = care.state.get('affinity', 0)
            previous_bonus = play_care_bonus(care.state, now)
            state = {**care.state, "played_at": iso(now)}
            bond(state, "play", 2, now)
            care.state = state
            care_effect = {
                'cat_id': str(cat_id),
                'bond_gained': state.get('affinity', 0) - previous_bond,
                'bond_total': state.get('affinity', 0),
                'play_bonus_before': previous_bonus,
                'play_bonus_after': play_care_bonus(state, now),
                'play_bonus_expires_at': iso(now + timedelta(hours=24)) if not state.get('assisted') else None,
            }
            await counter(session, user_id, "play", 1, occurred_at=now)
            await counter(session, user_id, "care_day:" + day(now), 1)
        result = {"success": success, "quantity": quantity, "practice": game.state["practice"] if kind == "fish" else False}
        if care_effect is not None:
            result['care_effect'] = care_effect
        game.state = {**game.state, "result": result}
    wallet.layout_revision += 1
    session.add(HabitatTransaction(user_id=user_id, event_key=event_key, category="quintal_game", amount=0, details={"request": request, "result": result}))
    await session.flush()
    return result
