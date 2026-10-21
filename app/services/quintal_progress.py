"""Project production counters into existing milestones, with zero base XP."""
from app.services.quintal_inventory import rows, PROGRESS_METRICS
from app.services.quintal_clock import utc, CALENDAR
from app.persistence_models import HabitatTransaction
from sqlalchemy import select
from app.quintal_models import QuintalCounter
from app.services import progression as progress
from datetime import date, timedelta
from app.services.quintal_rewards import record_progress_rewards, pending_rewards

METRICS = PROGRESS_METRICS

def dated(local_day):
    if local_day is None:
        return {"local_day": None, "local_week": None, "historical_undated": True}
    return {"local_day": local_day.isoformat(),
            "local_week": (local_day-timedelta(days=local_day.weekday())).isoformat()}

async def publish_progress(session, user_id):
    pending = list((await session.scalars(select(HabitatTransaction).where(
        HabitatTransaction.user_id == user_id,
        HabitatTransaction.category == "quintal_activity",
        HabitatTransaction.details["projected"].as_boolean().is_not(True),
    ))).all())
    pending.sort(key=lambda row: (row.details["occurred_at"], row.event_key))
    counters = await rows(session, QuintalCounter, user_id)
    last = None
    for row in counters:
        if (row.metric not in METRICS and not row.metric.startswith("care_day:")) or row.quantity <= row.projected_quantity: continue
        journal_quantity = sum(t.details["quantity"] for t in pending if t.details["metric"] == row.metric)
        delta = row.quantity - row.projected_quantity - journal_quantity
        if delta <= 0: continue
        # Old aggregate-only progress has no trustworthy date. Preserve lifetime
        # totals without crediting today's daily/weekly objectives.
        local_day = date.fromisoformat(row.metric.split(":", 1)[1]) if row.metric.startswith("care_day:") else None
        evidence = {"metric": "quintal_care_day" if local_day else "quintal_" + row.metric,
                    "quantity": delta, "value": row.quantity-journal_quantity,
                    "validated_by": "quintal_production", **dated(local_day)}
        last = await progress.grant(session, user_id, f"quintal:legacy:{row.metric}:{row.quantity-journal_quantity}", "quintal", xp=0, coins=0,
            evidence=evidence)
    for transaction in pending:
        entry = transaction.details
        local_day = utc(entry["occurred_at"]).astimezone(CALENDAR).date()
        last = await progress.grant(session, user_id, transaction.event_key, "quintal", xp=0, coins=0,
            evidence={"metric": "quintal_" + entry["metric"], "quantity": entry["quantity"],
                      "value": entry["value"], "occurred_at": entry["occurred_at"],
                      "validated_by": "quintal_production", **dated(local_day)})
        transaction.details = {**entry, "projected": True}
    for row in counters:
        if row.metric in METRICS or row.metric.startswith("care_day:"):
            row.projected_quantity = row.quantity
    if last:
        await progress.finish(session, last)
        await record_progress_rewards(session, last)
    result = dict(last.result or {}) if last else {}
    celebrations = await pending_rewards(session, user_id)
    if celebrations:
        result["celebrations"] = celebrations
    return result
