from contextlib import asynccontextmanager

from fastapi import (
    FastAPI,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.exceptions import (
    ColorAnalysisError,
    GeminiAnalysisError,
    InvalidImageError,
    MissingApiKeyError,
    RejectedImageError,
    SegmentationError,
)
from app.models.analysis import CatAnalysisResponse
from app.database import get_database_session
from app.persistence_models import CatPhoto, RemoteCat, SyncChange, User
from app.routers.auth import current_user, router as auth_router
from app.routers.sync import router as sync_router
from app.routers.social import router as social_router
from app.routers.vision import router as vision_router
from app.routers.product import router as product_router
from app.routers.websocket import router as websocket_router
from app.services.storage_service import photo_storage
from app.services.vision_jobs import enqueue_recognition
from app.services.analysis_service import AnalysisService
from app.database import get_session_factory
from app.services.websocket_manager import ws_manager


@asynccontextmanager
async def lifespan(_: FastAPI):
    await ws_manager.start_notification_bridge(get_session_factory())
    try:
        yield
    finally:
        await ws_manager.stop_notification_bridge()


app = FastAPI(
    title="Gatálogo AI API",
    description="Serviço de análise visual de pelagem para o aplicativo Gatálogo.",
    version="0.7.0",
    lifespan=lifespan,
)
app.include_router(auth_router)
app.include_router(sync_router)
app.include_router(social_router)
app.include_router(vision_router)
app.include_router(product_router)
app.include_router(websocket_router)


async def _read_image_with_limit(
    image: UploadFile,
    *,
    max_bytes: int,
) -> bytes:
    """Evita acumular uploads arbitrariamente grandes na memória."""
    chunks: list[bytes] = []
    total = 0

    while chunk := await image.read(1024 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise InvalidImageError(
                "A imagem ultrapassa o limite de tamanho permitido."
            )
        chunks.append(chunk)

    return b"".join(chunks)


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()

    return {
        "status": "ok",
        "model": settings.gemini_model,
        "gemini_key": (
            "configured"
            if settings.gemini_api_key
            else "missing"
        ),
    }


@app.post(
    "/analyze",
    response_model=CatAnalysisResponse,
)
async def analyze_cat(
    cat_id: str = Form(..., min_length=1, max_length=128),
    image: UploadFile = File(...),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> CatAnalysisResponse:
    settings = get_settings()

    try:
        image_bytes = await _read_image_with_limit(
            image,
            max_bytes=settings.max_image_bytes,
        )
        service = AnalysisService(settings)
        result = await service.analyze(
            cat_id=cat_id,
            image_bytes=image_bytes,
        )
        from uuid import UUID
        try:
            client_id = UUID(cat_id)
        except ValueError as error:
            raise InvalidImageError("O identificador do gato é inválido.") from error
        cat = (
            await session.execute(
                select(RemoteCat).where(
                    RemoteCat.user_id == user.id,
                    RemoteCat.client_id == client_id,
                )
            )
        ).scalar_one_or_none()
        if cat is None:
            from datetime import UTC, datetime
            cat = RemoteCat(
                user_id=user.id,
                client_id=client_id,
                payload={"status": "completed"},
                client_updated_at=datetime.now(UTC),
                updated_by_device="analysis-api",
            )
            session.add(cat)
            await session.flush()
        photo_key, photo_hash = photo_storage.save(
            user_id=user.id,
            cat_id=cat.id,
            data=image_bytes,
            suffix=".jpg",
        )
        cat.photo_key = photo_key
        cat.photo_hash = photo_hash
        cat.photo_content_type = image.content_type or "image/jpeg"
        primary = (await session.execute(
            select(CatPhoto).where(CatPhoto.cat_id == cat.id, CatPhoto.kind == "primary")
        )).scalar_one_or_none()
        if primary is None:
            primary = CatPhoto(
                cat_id=cat.id,
                kind="primary",
                storage_key=photo_key,
                content_hash=photo_hash,
                content_type=cat.photo_content_type,
                status="ready",
                analysis=result.model_dump(mode="json"),
            )
            session.add(primary)
        else:
            primary.storage_key = photo_key
            primary.content_hash = photo_hash
            primary.content_type = cat.photo_content_type
            primary.deleted_at = None
            primary.analysis = result.model_dump(mode="json")
        cat.analysis = result.model_dump(mode="json")
        cat.payload = {**(cat.payload or {}), "status": "completed"}
        await session.flush()
        await enqueue_recognition(session, cat=cat, photo=primary)
        session.add(SyncChange(user_id=user.id, cat_id=cat.id))
        await session.commit()
        return result

    except MissingApiKeyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "MISSING_API_KEY",
                "message": str(error),
            },
        ) from error

    except SegmentationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SEGMENTATION_UNAVAILABLE",
                "message": str(error),
            },
        ) from error

    except ColorAnalysisError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "COLOR_ANALYSIS_FAILED",
                "message": str(error),
            },
        ) from error

    except InvalidImageError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_IMAGE",
                "message": str(error),
            },
        ) from error

    except RejectedImageError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": error.reason,
                "message": error.message,
            },
        ) from error

    except GeminiAnalysisError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "ANALYSIS_FAILED",
                "message": str(error),
            },
        ) from error

    finally:
        await image.close()
