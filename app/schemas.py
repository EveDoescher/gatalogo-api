from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)


class VerifyOtpRequest(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")
    device_id: str | None = Field(default=None, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    device_id: str | None = Field(default=None, max_length=128)


class GoogleLoginRequest(BaseModel):
    id_token: str = Field(min_length=20, max_length=8192)
    device_id: str | None = Field(default=None, max_length=128)


class AppleLoginRequest(BaseModel):
    id_token: str = Field(min_length=20, max_length=8192)
    device_id: str | None = Field(default=None, max_length=128)
    user_identifier: str | None = Field(default=None, max_length=256)
    email: str | None = Field(default=None, max_length=256)


class DeactivateRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=1024)
    device_id: str | None = Field(default=None, max_length=128)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")
    password: str = Field(min_length=12, max_length=256)


class AuthTokens(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user_id: UUID
    email: EmailStr


class AccountResponse(BaseModel):
    id: UUID
    email: EmailStr
    username: str | None = None
    status: str
    created_at: datetime


class UsernameRequest(BaseModel):
    username: str = Field(pattern=r"^[a-z0-9_]{3,32}$")


class PublicUserResponse(BaseModel):
    id: UUID
    username: str
    avatar_url: str | None = None
    bio: str | None = None


class MissingReportCreate(BaseModel):
    cat_client_id: UUID
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_meters: int = Field(ge=100, le=50_000)


class MissingReportResponse(BaseModel):
    id: UUID
    cat_client_id: UUID
    latitude: float
    longitude: float
    radius_meters: int
    status: str
    created_at: datetime


class SightingCreate(BaseModel):
    observed_at: datetime | None = None
    cat_client_id: UUID
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    note: str | None = Field(default=None, max_length=1000)


class MatchResponse(BaseModel):
    id: UUID
    report_id: UUID
    sighting_id: UUID
    similarity: float
    status: str
    restricted: bool
    # Similaridade não é probabilidade de identidade. Evidência é explicável.
    evidence: dict[str, Any] = Field(default_factory=dict)
    # Só vem preenchido para o dono após confirmar o match.
    latitude: float | None = None
    longitude: float | None = None
    conversation_id: UUID | None = None


class VisionJobResponse(BaseModel):
    id: UUID
    status: str
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class FriendInviteCreate(BaseModel):
    username: str = Field(pattern=r"^[a-z0-9_]{3,32}$")


class FriendInviteResponse(BaseModel):
    id: UUID
    sender: PublicUserResponse
    recipient: PublicUserResponse
    status: str
    created_at: datetime


class FeedItemResponse(BaseModel):
    id: UUID
    username: str
    type: str
    payload: dict[str, Any]
    created_at: datetime


class MessageCreate(BaseModel):
    body: str = Field(default="", max_length=4000)
    type: str = "text"
    media_url: str | None = None
    location_lat: float | None = None
    location_lng: float | None = None
    location_title: str | None = None
    cat_data: dict[str, Any] | None = None
    reply_to_id: UUID | None = None


class MessageReactionRequest(BaseModel):
    emoji: str = Field(min_length=1, max_length=16)


class MessageEditRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class MessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    sender_id: UUID
    body: str
    type: str = "text"
    media_url: str | None = None
    location_lat: float | None = None
    location_lng: float | None = None
    location_title: str | None = None
    cat_data: dict[str, Any] | None = None
    read_at: datetime | None = None
    is_read: bool = False
    reply_to_id: UUID | None = None
    reply_to: dict[str, Any] | None = None
    reactions: dict[str, Any] = Field(default_factory=dict)
    is_edited: bool = False
    is_deleted: bool = False
    created_at: datetime


class ConversationUserResponse(BaseModel):
    id: UUID
    username: str
    avatar_url: str | None = None
    is_online: bool = False
    last_seen_at: datetime | None = None


class ConversationSummaryResponse(BaseModel):
    id: UUID
    other_user: ConversationUserResponse
    last_message: MessageResponse | None = None
    unread_count: int = 0
    sighting_context: bool = False
    updated_at: datetime


class DeviceTokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)
    platform: Literal["android", "ios"]
    preferences: dict[str, bool] = Field(default_factory=dict)


class SyncOperation(BaseModel):
    client_id: UUID
    updated_at: datetime
    device_id: str = Field(min_length=1, max_length=128)
    deleted_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    analysis: dict[str, Any] | None = None


class SyncPushRequest(BaseModel):
    operations: list[SyncOperation] = Field(default_factory=list, max_length=500)


class SyncCatRecord(BaseModel):
    client_id: UUID
    updated_at: datetime
    device_id: str
    deleted_at: datetime | None
    payload: dict[str, Any]
    analysis: dict[str, Any] | None
    photo_available: bool
    photo_url: str | None
    reference_photos: list["ReferencePhotoResponse"] = Field(default_factory=list)
    avatar: "AvatarResponse | None" = None
    revision: int


class SyncPullResponse(BaseModel):
    changes: list[SyncCatRecord]
    next_cursor: int


class ReferencePhotoResponse(BaseModel):
    kind: Literal["front", "left", "right", "back"]
    status: str
    photo_url: str | None = None
    updated_at: datetime


class AvatarResponse(BaseModel):
    version: int
    status: str
    preview_url: str | None = None
    texture_urls: dict[str, str] = Field(default_factory=dict)
    coat_map: dict[str, Any] | None = None
    error_message: str | None = None
    updated_at: datetime


class UserSearchResult(BaseModel):
    id: UUID
    username: str
    avatar_url: str | None = None
    bio: str | None = None
    mutual_friends_count: int = 0
    friendship_status: str = "none"


class UserSuggestion(BaseModel):
    id: UUID
    username: str
    avatar_url: str | None = None
    bio: str | None = None
    mutual_friends_count: int = 0
    reason: str = "Explorador ativo"


class PublicProfileResponse(BaseModel):
    id: UUID
    username: str
    bio: str = ""
    avatar_url: str | None = None
    level: int = 1
    level_title: str = "Curioso"
    xp: int = 0
    streak: int = 0
    longest_streak: int = 0
    cats_count: int = 0
    achievements: list[dict[str, Any]] = Field(default_factory=list)
    friendship_status: str = "none"
    mutual_friends_count: int = 0
    public_cats: list[dict[str, Any]] = Field(default_factory=list)


class LeaderboardEntry(BaseModel):
    rank: int
    user_id: UUID
    username: str
    avatar_url: str | None = None
    xp: int = 0
    level: int = 1
    level_title: str = "Curioso"
    streak: int = 0
    is_self: bool = False
