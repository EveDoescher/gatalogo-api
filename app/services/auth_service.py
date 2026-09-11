from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from uuid import UUID

import jwt
from fastapi import HTTPException, status
from pwdlib import PasswordHash
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.persistence_models import EmailOtp, User, UserSession

logger = logging.getLogger(__name__)
password_hash = PasswordHash.recommended()


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_email(value: str) -> str:
    return value.strip().casefold()


def hash_refresh_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_otp(*, code: str, email: str, purpose: str, settings: Settings) -> str:
    message = f"{normalize_email(email)}:{purpose}:{code}".encode("utf-8")
    return hmac.new(settings.otp_pepper.encode("utf-8"), message, hashlib.sha256).hexdigest()


def invalid_credentials() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas.")


async def create_otp(
    session: AsyncSession,
    *,
    email: str,
    purpose: str,
    user_id: UUID | None = None,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    normalized = normalize_email(email)
    recent = (await session.execute(select(EmailOtp).where(EmailOtp.email == normalized, EmailOtp.purpose == purpose).order_by(EmailOtp.created_at.desc()).limit(1))).scalar_one_or_none()
    if recent is not None and (utc_now() - recent.created_at).total_seconds() < 60:
        raise HTTPException(429, "Aguarde um minuto antes de solicitar outro código.")
    await session.execute(
        update(EmailOtp)
        .where(
            EmailOtp.email == normalized,
            EmailOtp.purpose == purpose,
            EmailOtp.consumed_at.is_(None),
        )
        .values(consumed_at=utc_now())
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    session.add(
        EmailOtp(
            user_id=user_id,
            email=normalized,
            purpose=purpose,
            code_hash=hash_otp(code=code, email=normalized, purpose=purpose, settings=settings),
            expires_at=utc_now() + timedelta(minutes=settings.otp_ttl_minutes),
        )
    )
    await session.flush()
    await _send_otp(email=normalized, code=code, purpose=purpose, settings=settings)


async def consume_otp(
    session: AsyncSession,
    *,
    email: str,
    purpose: str,
    code: str,
    settings: Settings | None = None,
) -> EmailOtp:
    settings = settings or get_settings()
    result = await session.execute(
        select(EmailOtp)
        .where(
            EmailOtp.email == normalize_email(email),
            EmailOtp.purpose == purpose,
            EmailOtp.consumed_at.is_(None),
        )
        .order_by(EmailOtp.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    otp = result.scalar_one_or_none()
    now = utc_now()
    if otp is None or otp.expires_at <= now or otp.attempts >= settings.otp_max_attempts:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Código inválido ou expirado.")
    otp.attempts += 1
    expected = hash_otp(code=code, email=email, purpose=purpose, settings=settings)
    if not hmac.compare_digest(otp.code_hash, expected):
        # Persist failed attempts even when the request dependency rolls back.
        await session.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Código inválido ou expirado.")
    otp.consumed_at = now
    return otp


async def issue_tokens(
    session: AsyncSession,
    *,
    user: User,
    device_id: str | None,
    settings: Settings | None = None,
) -> dict[str, object]:
    settings = settings or get_settings()
    now = utc_now()
    refresh = secrets.token_urlsafe(48)
    db_session = UserSession(
        user_id=user.id,
        device_id=device_id,
        refresh_token_hash=hash_refresh_token(refresh),
        expires_at=now + timedelta(days=settings.refresh_token_days),
    )
    session.add(db_session)
    await session.flush()
    access = jwt.encode(
        {
            "sub": str(user.id),
            "sid": str(db_session.id),
            "type": "access",
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_minutes),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": settings.access_token_minutes * 60,
        "user_id": user.id,
        "email": user.email,
    }


async def authenticated_user(token: str, session: AsyncSession) -> User:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
        )
        if payload.get("type") != "access":
            raise jwt.InvalidTokenError("wrong token type")
        user_id = UUID(payload["sub"])
        session_id = UUID(payload["sid"])
    except (jwt.PyJWTError, KeyError, ValueError) as error:
        raise invalid_credentials() from error
    active_session = await session.get(UserSession, session_id)
    if (
        active_session is None
        or active_session.user_id != user_id
        or active_session.revoked_at is not None
        or active_session.expires_at <= utc_now()
    ):
        raise invalid_credentials()
    user = await session.get(User, user_id)
    if user is None or user.status != "active":
        raise invalid_credentials()
    return user


async def _send_otp(*, email: str, code: str, purpose: str, settings: Settings) -> None:
    if not settings.smtp_enabled:
        logger.warning("OTP de desenvolvimento para %s (%s): %s", email, purpose, code)
        return
    message = EmailMessage()
    message["Subject"] = "Seu código do Gatálogo"
    message["From"] = settings.smtp_from
    message["To"] = email
    message.set_content(
        f"Seu código de verificação do Gatálogo é {code}. Ele expira em {settings.otp_ttl_minutes} minutos."
    )

    def send() -> None:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)

    import asyncio
    await asyncio.to_thread(send)
