import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Float, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    external_id: Mapped[str] = mapped_column(String(100), index=True)
    height_cm: Mapped[float] = mapped_column(Float)
    shoulder_cm: Mapped[float] = mapped_column(Float)
    waist_cm: Mapped[float] = mapped_column(Float)
    front_waist_width_cm: Mapped[float] = mapped_column(Float)
    side_waist_depth_cm: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), default="accepted")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    front_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    side_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class DemoMeasurement(Base):
    __tablename__ = "demo_measurements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    external_id: Mapped[str] = mapped_column(String(100), index=True)
    height_cm: Mapped[float] = mapped_column(Float)
    shoulder_cm: Mapped[float] = mapped_column(Float)
    waist_cm: Mapped[float] = mapped_column(Float)
    front_waist_width_cm: Mapped[float] = mapped_column(Float)
    side_waist_depth_cm: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), default="demo")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    front_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    side_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

