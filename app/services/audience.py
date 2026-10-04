from sqlalchemy import select
from app.persistence_models import Friendship, ProductState


async def permits(session, viewer_id, owner_id, section, *, as_friend=False):
    state = await session.get(ProductState, owner_id)
    audience = (state.preferences or {}).get(f"{section}_audience", "friends") if state else "friends"
    if viewer_id == owner_id and not as_friend:
        return True
    if audience == "public":
        return True
    if audience != "friends":
        return False
    return as_friend or bool(await session.scalar(select(Friendship.id).where(
        Friendship.user_id == viewer_id, Friendship.friend_id == owner_id)))


async def can_read_post(session, item, viewer_id):
    if item.user_id == viewer_id or (item.payload or {}).get("is_public", True):
        return True
    return bool(await session.scalar(select(Friendship.id).where(
        Friendship.user_id == viewer_id, Friendship.friend_id == item.user_id)))
