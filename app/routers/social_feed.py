from __future__ import annotations
from app.social_schemas import (
    CatPostCreate as CatPostCreate,
    CommentCreate as CommentCreate,
)
from app.services.social_feed_commands import _share_report as _share_report

from datetime import datetime
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_database_session
from app.dependencies import current_user
from app.persistence_models import (
    User,
)
from app.schemas import (
    FeedItemResponse,
)
from app.services.social_graph import OFFICIAL_USER_ID as OFFICIAL_USER_ID

router = APIRouter(prefix="/social", tags=["social"])


@router.get("/reports")
async def community_reports(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
):
    return await social_feed_queries.community_reports(user=user, session=session)


@router.get("/reports/{report_id}")
async def community_report(
    report_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
):
    return await social_feed_queries.community_report(
        report_id=report_id, user=user, session=session
    )


@router.get("/gallery/{cat_id}/photo")
async def gallery_photo(
    cat_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
):
    return await social_feed_queries.gallery_photo(
        cat_id=cat_id, user=user, session=session
    )


@router.post("/reports/{report_id}/repost", response_model=FeedItemResponse)
async def repost_report(
    report_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
):
    return await social_feed_commands.repost_report(
        report_id=report_id, user=user, session=session
    )


@router.post("/reports/{report_id}/publish", response_model=FeedItemResponse)
async def publish_report(
    report_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
):
    return await social_feed_commands.publish_report(
        report_id=report_id, user=user, session=session
    )


# _share_report is owned by the command service.


@router.get("/feed", response_model=list[FeedItemResponse])
async def feed(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: datetime | None = Query(default=None),
    cursor_id: UUID | None = Query(default=None),
    mode: str = Query(default="all"),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> list[FeedItemResponse]:
    return await social_feed_queries.feed(
        limit=limit,
        cursor=cursor,
        cursor_id=cursor_id,
        mode=mode,
        user=user,
        session=session,
    )


@router.post("/posts/photo")
async def upload_post_photo(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    return await social_feed_commands.upload_post_photo(
        file=file, user=user, session=session
    )


@router.get("/posts/photo/{filename}")
async def get_post_photo(
    filename: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> Response:
    return await social_feed_queries.get_post_photo(
        filename=filename, user=user, session=session
    )


@router.post(
    "/posts", response_model=FeedItemResponse, status_code=status.HTTP_201_CREATED
)
async def create_post(
    payload: CatPostCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> FeedItemResponse:
    return await social_feed_commands.create_post(
        payload=payload, user=user, session=session
    )


@router.post("/feed/{item_id}/like")
async def toggle_like(
    item_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    return await social_feed_commands.toggle_like(
        item_id=item_id, user=user, session=session
    )


@router.post("/feed/{item_id}/comments")
async def add_comment(
    item_id: UUID,
    payload: CommentCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> list[dict]:
    return await social_feed_commands.add_comment(
        item_id=item_id, payload=payload, user=user, session=session
    )


from app.services import social_feed_queries, social_feed_commands
