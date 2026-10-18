"""In-app readiness notices. Marking a notice read never collects resources."""
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from sqlalchemy import select
from app.persistence_models import HabitatTransaction, AppNotification
from app.services.realtime_events import queue_notification

async def notify_ready(session, user_id, projection):
    groups = defaultdict(list)
    for t in projection["tasks"]:
        if t["status"] == "ready": groups["kitchen" if t["kind"] == "cook" else "market"].append("task:" + t["id"])
    now = projection["server_now"]
    for facility in projection['facilities']:
        if facility['target_level'] and facility['ready_at'] <= now:
            space = 'pier' if facility['kind'] == 'rod' else facility['kind']
            groups[space].append(f"build:{facility['kind']}:{facility['target_level']}:{facility['ready_at']}")
    for p in projection["plots"]:
        if p["crop"] and p["ready_at"] <= now: groups["garden"].append(f"plot:{p['slot']}:{p['started_at']}")
    for r in projection["residents"]:
        if r.get("ready", 0): groups[r["kind"]].append(f"resident:{r['kind']}:{r.get('ready_since')}")
    titles = {"kitchen": "Rações prontas na cozinha", "market": "Sua encomenda chegou", "garden": "A horta tem colheitas prontas", "coop": "Há ovos nos ninhos", "pasture": "Há leite para coletar", "orchard": "Frutas prontas no pomar"}
    for space, markers in groups.items():
        new = []
        for marker in markers:
            key = "qnotice:" + marker
            existing = await session.scalar(select(HabitatTransaction.id).where(HabitatTransaction.user_id == user_id, HabitatTransaction.event_key == key))
            if existing: continue
            session.add(HabitatTransaction(user_id=user_id, event_key=key, category="production_notice", amount=0, details={"space": space}))
            new.append(marker)
        if new:
            title = 'Uma melhoria está pronta no Quintal' if any(m.startswith('build:') for m in new) else titles[space]
            notice = AppNotification(user_id=user_id, type="quintal_ready", payload={"space": space, "title": title, "count": len(new)})
            session.add(notice)
            await session.flush()
            session.add(HabitatTransaction(user_id=user_id,
                event_key='qpush:' + str(notice.id), category='quintal_push', amount=0,
                details={'notification_id': str(notice.id), 'state': 'pending', 'attempts': 0,
                         'delivered': [], 'next_attempt': 0, 'markers': new,
                         'expires_at': (datetime.now(UTC) + timedelta(hours=24)).timestamp()}))
            queue_notification(session, notice)
