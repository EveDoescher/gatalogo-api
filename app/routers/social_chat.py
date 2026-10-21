from __future__ import annotations

from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
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
    ConversationSummaryResponse,
    MessageCreate,
    MessageEditRequest,
    MessageReactionRequest,
    MessageResponse,
)

router = APIRouter(prefix="/social", tags=["social"])


@router.get("/conversations", response_model=list[ConversationSummaryResponse])
async def conversations_list(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> list[ConversationSummaryResponse]:
    return await chat_queries.conversations_list(user=user, session=session)


@router.get("/conversations/{conversation_id}")
async def conversation_details(
    conversation_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    return await chat_queries.conversation_details(
        conversation_id=conversation_id, user=user, session=session
    )


@router.get(
    "/conversations/{conversation_id}/messages", response_model=list[MessageResponse]
)
async def messages(
    conversation_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> list[MessageResponse]:
    return await chat_queries.messages(
        conversation_id=conversation_id, user=user, session=session
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_message(
    conversation_id: UUID,
    payload: MessageCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> MessageResponse:
    return await chat_commands.send_message(
        conversation_id=conversation_id, payload=payload, user=user, session=session
    )


@router.post("/conversations/{conversation_id}/photo")
async def upload_chat_photo(
    conversation_id: UUID,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    return await chat_commands.upload_chat_photo(
        conversation_id=conversation_id, file=file, user=user, session=session
    )


@router.get("/conversations/{conversation_id}/photo/{filename}")
async def get_chat_photo(
    conversation_id: UUID,
    filename: str,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_database_session),
) -> Response:
    return await chat_queries.get_chat_photo(
        conversation_id=conversation_id,
        filename=filename,
        token=token,
        authorization=authorization,
        session=session,
    )


@router.post("/conversations/{conversation_id}/read")
async def mark_conversation_read(
    conversation_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    return await chat_commands.mark_conversation_read(
        conversation_id=conversation_id, user=user, session=session
    )


@router.post("/conversations/{conversation_id}/messages/{message_id}/reactions")
async def toggle_message_reaction(
    conversation_id: UUID,
    message_id: UUID,
    payload: MessageReactionRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    return await chat_commands.toggle_message_reaction(
        conversation_id=conversation_id,
        message_id=message_id,
        payload=payload,
        user=user,
        session=session,
    )


@router.put(
    "/conversations/{conversation_id}/messages/{message_id}",
    response_model=MessageResponse,
)
async def edit_message(
    conversation_id: UUID,
    message_id: UUID,
    payload: MessageEditRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> MessageResponse:
    return await chat_commands.edit_message(
        conversation_id=conversation_id,
        message_id=message_id,
        payload=payload,
        user=user,
        session=session,
    )


@router.delete("/conversations/{conversation_id}/messages/{message_id}")
async def delete_message(
    conversation_id: UUID,
    message_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    return await chat_commands.delete_message(
        conversation_id=conversation_id,
        message_id=message_id,
        user=user,
        session=session,
    )


@router.get("/users/{user_id}/presence")
async def user_presence(
    user_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    return await chat_queries.user_presence(user_id=user_id, user=user, session=session)


from app.services import chat_queries, chat_commands
