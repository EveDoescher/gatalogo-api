from __future__ import annotations

from datetime import UTC, datetime, timedelta
import secrets
import hmac
from pydantic import BaseModel, EmailStr, Field

from fastapi import APIRouter, Depends, Header, HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_database_session
from app.persistence_models import AuthIdentity, EmailOtp, User, UserSession
from app.schemas import (
    AccountResponse,
    AuthTokens,
    GoogleLoginRequest,
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    UsernameRequest,
    VerifyOtpRequest,
)
from app.services.auth_service import (
    authenticated_user,
    consume_otp,
    create_otp,
    hash_refresh_token,
    invalid_credentials,
    issue_tokens,
    normalize_email,
    password_hash,
    utc_now,
)

router = APIRouter(prefix="/auth", tags=["auth"])

class ResetCodeRequest(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")


class NewPasswordRequest(BaseModel):
    reset_token: str = Field(min_length=40, max_length=200)
    password: str = Field(min_length=12, max_length=256)


@router.post("/verify-email/resend", status_code=202)
async def resend_verification(payload: PasswordResetRequest, session: AsyncSession = Depends(get_database_session)) -> dict:
    user = (await session.execute(select(User).where(User.email == normalize_email(str(payload.email))))).scalar_one_or_none()
    if user is not None and user.status in {"pending", "deactivated"}:
        await create_otp(session, email=user.email, purpose="verify_email", user_id=user.id)
        await session.commit()
    return {"message": "Se houver uma conta aguardando confirmação, enviamos um novo código."}


@router.post("/password-reset/verify")
async def verify_reset_code(payload: ResetCodeRequest, session: AsyncSession = Depends(get_database_session)) -> dict:
    otp = await consume_otp(session, email=str(payload.email), purpose="reset_password", code=payload.code)
    token = secrets.token_urlsafe(48)
    # Short-lived, single-use grant; it cannot authenticate an app session.
    await session.execute(update(EmailOtp).where(EmailOtp.user_id == otp.user_id, EmailOtp.purpose == "reset_grant", EmailOtp.consumed_at.is_(None)).values(consumed_at=utc_now()))
    session.add(EmailOtp(user_id=otp.user_id, email=otp.email, purpose="reset_grant",
        code_hash=hash_refresh_token(token), expires_at=utc_now() + timedelta(minutes=10)))
    await session.commit()
    return {"reset_token": token}


@router.post("/password-reset/complete", status_code=204)
async def complete_reset(payload: NewPasswordRequest, session: AsyncSession = Depends(get_database_session)) -> None:
    grant = (await session.execute(select(EmailOtp).where(EmailOtp.purpose == "reset_grant", EmailOtp.code_hash == hash_refresh_token(payload.reset_token)).with_for_update())).scalar_one_or_none()
    if grant is None or grant.consumed_at is not None or grant.expires_at <= utc_now():
        raise HTTPException(400, "Autorização expirada. Solicite um novo código.")
    user = await session.get(User, grant.user_id)
    if user is None or user.status != "active":
        raise HTTPException(400, "Não foi possível redefinir a senha.")
    grant.consumed_at = utc_now()
    user.password_hash = password_hash.hash(payload.password)
    await session.execute(update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).values(revoked_at=utc_now()))
    await session.commit()



async def current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_database_session),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise invalid_credentials()
    return await authenticated_user(authorization.removeprefix("Bearer "), session)


@router.put("/username", response_model=AccountResponse)
async def set_username(
    payload: UsernameRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> User:
    """Nome social único, escolhido uma vez e sem expor o e-mail."""
    if user.username is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="O nome de usuário não pode ser alterado.")
    username = payload.username.lower()
    existing = (await session.execute(select(User.id).where(User.username == username))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este nome de usuário já está em uso.")
    user.username = username
    await session.commit()
    await session.refresh(user)
    return user


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
async def register(payload: RegisterRequest, session: AsyncSession = Depends(get_database_session)) -> dict[str, str]:
    email = normalize_email(str(payload.email))
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is not None and user.status == "active" and user.password_hash is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este e-mail já possui uma conta.")
    if user is None:
        user = User(email=email, password_hash=password_hash.hash(payload.password), status="pending")
        session.add(user)
        await session.flush()
    else:
        user.password_hash = password_hash.hash(payload.password)
        user.status = "pending"
        user.deactivated_at = None
    await create_otp(session, email=email, purpose="verify_email", user_id=user.id)
    await session.commit()
    return {"message": "Enviamos um código de verificação para seu e-mail."}


@router.post("/verify-email", response_model=AuthTokens)
async def verify_email(payload: VerifyOtpRequest, session: AsyncSession = Depends(get_database_session)) -> dict[str, object]:
    otp = await consume_otp(session, email=str(payload.email), purpose="verify_email", code=payload.code)
    user = await session.get(User, otp.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Código inválido ou expirado.")
    user.status = "active"
    user.deactivated_at = None
    tokens = await issue_tokens(session, user=user, device_id=payload.device_id)
    await session.commit()
    return tokens


@router.post("/login", response_model=AuthTokens)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_database_session)) -> dict[str, object]:
    result = await session.execute(select(User).where(User.email == normalize_email(str(payload.email))))
    user = result.scalar_one_or_none()
    if user is None or user.password_hash is None or not password_hash.verify(payload.password, user.password_hash):
        raise invalid_credentials()
    if user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Confirme seu e-mail para entrar.")
    tokens = await issue_tokens(session, user=user, device_id=payload.device_id)
    await session.commit()
    return tokens


@router.post("/google", response_model=AuthTokens)
async def google_login(payload: GoogleLoginRequest, session: AsyncSession = Depends(get_database_session)) -> dict[str, object]:
    settings = get_settings()
    if not settings.google_web_client_id:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Login Google não configurado.")
    try:
        claims = google_id_token.verify_oauth2_token(
            payload.id_token,
            google_requests.Request(),
            settings.google_web_client_id,
        )
        if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"} or not claims.get("email_verified"):
            raise ValueError("Google account not verified")
        email = normalize_email(str(claims["email"]))
        subject = str(claims["sub"])
    except Exception as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token Google inválido.") from error
    identity = (await session.execute(select(AuthIdentity).where(AuthIdentity.provider == "google", AuthIdentity.subject == subject))).scalar_one_or_none()
    user = await session.get(User, identity.user_id) if identity else None
    if user is None:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = User(email=email, status="active")
            session.add(user)
            await session.flush()
        session.add(AuthIdentity(user_id=user.id, provider="google", subject=subject))
    if user.status == "deactivated":
        user.status = "active"
        user.deactivated_at = None
    if user.status != "active":
        user.status = "active"
    tokens = await issue_tokens(session, user=user, device_id=payload.device_id)
    await session.commit()
    return tokens


@router.post("/refresh", response_model=AuthTokens)
async def refresh(payload: RefreshRequest, session: AsyncSession = Depends(get_database_session)) -> dict[str, object]:
    result = await session.execute(
        select(UserSession).where(UserSession.refresh_token_hash == hash_refresh_token(payload.refresh_token)).with_for_update()
    )
    old_session = result.scalar_one_or_none()
    if old_session is None or old_session.revoked_at is not None or old_session.expires_at <= utc_now():
        raise invalid_credentials()
    user = await session.get(User, old_session.user_id)
    if user is None or user.status != "active":
        raise invalid_credentials()
    old_session.revoked_at = utc_now()
    tokens = await issue_tokens(session, user=user, device_id=payload.device_id or old_session.device_id)
    await session.commit()
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_database_session),
) -> None:
    if authorization and authorization.startswith("Bearer "):
        try:
            import jwt
            claims = jwt.decode(authorization.removeprefix("Bearer "), get_settings().jwt_secret, algorithms=["HS256"], issuer=get_settings().jwt_issuer)
            session_id = claims.get("sid")
            if session_id:
                user_session = await session.get(UserSession, session_id)
                if user_session:
                    user_session.revoked_at = utc_now()
                    await session.commit()
        except Exception:
            pass


@router.get("/me", response_model=AccountResponse)
async def me(user: User = Depends(current_user)) -> User:
    return user


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(payload: PasswordResetRequest, session: AsyncSession = Depends(get_database_session)) -> dict[str, str]:
    user = (await session.execute(select(User).where(User.email == normalize_email(str(payload.email))))).scalar_one_or_none()
    if user is not None and user.status == "active" and user.password_hash is not None:
        await create_otp(session, email=user.email, purpose="reset_password", user_id=user.id)
        await session.commit()
    return {"message": "Se houver uma conta compatível, enviamos um código para o e-mail."}


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_password_reset(payload: PasswordResetConfirmRequest, session: AsyncSession = Depends(get_database_session)) -> None:
    otp = await consume_otp(session, email=str(payload.email), purpose="reset_password", code=payload.code)
    user = await session.get(User, otp.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Código inválido ou expirado.")
    user.password_hash = password_hash.hash(payload.password)
    await session.execute(update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).values(revoked_at=utc_now()))
    await session.commit()


@router.post("/deactivate", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> None:
    user.status = "deactivated"
    user.deactivated_at = utc_now()
    await session.execute(update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).values(revoked_at=utc_now()))
    await session.commit()
