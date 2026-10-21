from uuid import UUID
from pydantic import BaseModel, Field


class CatPostCreate(BaseModel):
    cat_name: str = Field(default="", max_length=100)
    request_id: UUID | None = None
    note: str = Field(default="", max_length=2000)
    location_name: str | None = None
    photo_base64: str | None = None
    photo_url: str | None = None
    is_public: bool = True


class CommentCreate(BaseModel):
    text: str
