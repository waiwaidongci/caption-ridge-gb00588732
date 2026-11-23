from datetime import datetime
from sqlalchemy import JSON, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
from src.constants.enums import Status
from src.utils.time import now

class GlossaryTerm(Base):
    """One row per term so concurrent coverage updates to different terms never conflict."""
    __tablename__ = 'glossary_terms'

    id: Mapped[int] = mapped_column(primary_key=True)
    term: Mapped[str] = mapped_column(String(120), unique=True)
    cost: Mapped[float] = mapped_column(Float)
    coverage: Mapped[list] = mapped_column(JSON)
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)

class GlossaryState(Base):
    """Single-row version counter bumped in the same transaction as every term change."""
    __tablename__ = 'glossary_state'

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(default=0)

class GlossaryPlan(Base):
    __tablename__ = 'glossary_plans'

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('runs.id'))
    budget: Mapped[float] = mapped_column(Float)
    input_version: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(20), default=Status.QUEUED, index=True)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(500))
    attempt: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None]
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
    finished_at: Mapped[datetime | None]
