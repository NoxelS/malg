"""SQLAlchemy mappings for the canonical campaign and ICP research artifacts.

The JSON payloads preserve each validated Pydantic artifact without coupling
the persistence layer to agent execution or result-file workflows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONPayload = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    """Base class for MALG's SQLAlchemy mappings."""


class Campaign(Base):
    """A validated campaign artifact, identified by its host-controlled ID.

    ``payload`` holds the canonical JSON representation of a
    :class:`malg.core.models.campaign.CampaignCandidate`. It is intentionally
    not populated by agents or result writers at this stage.
    """

    __tablename__ = "campaigns"

    campaign_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONPayload, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    icps: Mapped[list[ICP]] = relationship(back_populates="campaign", passive_deletes=True)


class ICP(Base):
    """A validated ICP artifact belonging to one persisted campaign.

    The campaign/ICP pair and campaign-scoped segment key are unique, matching
    the existing deterministic ICP-history constraints. ``payload`` stores the
    canonical JSON representation of :class:`malg.core.models.icp.ICPResult`.
    """

    __tablename__ = "icps"
    __table_args__ = (
        UniqueConstraint("campaign_id", "icp_id", name="uq_icps_campaign_id_icp_id"),
        UniqueConstraint("campaign_id", "segment_key", name="uq_icps_campaign_id_segment_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.campaign_id", ondelete="CASCADE"), nullable=False
    )
    icp_id: Mapped[str] = mapped_column(String(80), nullable=False)
    segment_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONPayload, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    campaign: Mapped[Campaign] = relationship(back_populates="icps")
