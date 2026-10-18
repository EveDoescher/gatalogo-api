"""Normalized durable production state; immutable operations live in transactions."""
import uuid
from datetime import datetime
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class QuintalFacility(Base):
    __tablename__ = "quintal_facilities"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), primary_key=True)
    level: Mapped[int] = mapped_column(Integer, default=0)
    target_level: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class QuintalResource(Base):
    __tablename__ = "quintal_resources"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    resource: Mapped[str] = mapped_column(String(48), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)

class QuintalTask(Base):
    __tablename__ = "quintal_tasks"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    target: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(24), default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    outputs: Mapped[dict] = mapped_column(JSON, default=dict)

class QuintalPlot(Base):
    __tablename__ = "quintal_plots"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    crop: Mapped[str | None] = mapped_column(String(24), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    watered: Mapped[int] = mapped_column(Integer, default=0)

class QuintalCounter(Base):
    __tablename__ = "quintal_counters"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    metric: Mapped[str] = mapped_column(String(64), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    projected_quantity: Mapped[int] = mapped_column(Integer, default=0)

class QuintalResident(Base):
    __tablename__ = "quintal_residents"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), primary_key=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)

class QuintalCatCare(Base):
    __tablename__ = "quintal_cat_care"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    cat_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)

class QuintalPreference(Base):
    __tablename__ = "quintal_preferences"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    cat_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    recipe: Mapped[str] = mapped_column(String(24), primary_key=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)

class QuintalSession(Base):
    __tablename__ = "quintal_sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[dict] = mapped_column(JSON, default=dict)
