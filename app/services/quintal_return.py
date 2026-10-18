"""Explicit receipts: acknowledging one shown event cannot hide a newer one."""
from hashlib import sha256
from uuid import uuid4
from sqlalchemy import select
from app.persistence_models import HabitatTransaction


def record_reaction(session, user_id, cat_id, reaction):
    key = 'qreaction:' + str(uuid4())
    session.add(HabitatTransaction(user_id=user_id, event_key=key,
        category='quintal_reaction', amount=0,
        details={**reaction, 'target': str(cat_id)}))
    return key


async def visible_updates(session, user_id, candidates):
    reactions = (await session.scalars(select(HabitatTransaction).where(
        HabitatTransaction.user_id == user_id,
        HabitatTransaction.category == 'quintal_reaction',
        HabitatTransaction.details['acknowledged'].as_boolean().is_not(True),
    ))).all()
    for reaction in reactions:
        if not reaction.details.get('acknowledged'):
            candidates.append({**reaction.details, 'event_id': reaction.event_key})
    result = []
    for item in candidates:
        if item.get('event_id', '').startswith('qreaction:'):
            result.append(item)
            continue
        source = item.pop('_source')
        key = 'qsummary:' + sha256(source.encode()).hexdigest()
        receipt = await session.scalar(select(HabitatTransaction).where(
            HabitatTransaction.user_id == user_id, HabitatTransaction.event_key == key))
        if receipt is None:
            receipt = HabitatTransaction(user_id=user_id, event_key=key,
                category='quintal_summary', amount=0, details={})
            session.add(receipt)
        if not receipt.details.get('acknowledged'):
            result.append({**item, 'event_id': key})
    return result


async def acknowledge(session, user_id, event_ids):
    if not event_ids:
        return 0  # Older clients must never mark unseen future events as read.
    receipts = (await session.scalars(select(HabitatTransaction).where(
        HabitatTransaction.user_id == user_id,
        HabitatTransaction.event_key.in_(event_ids),
        HabitatTransaction.category.in_(['quintal_summary', 'quintal_reaction']),
    ))).all()
    for receipt in receipts:
        receipt.details = {**receipt.details, 'acknowledged': True}
    return len(receipts)
