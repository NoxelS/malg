"""SQLAlchemy mappings for the canonical campaign and ICP research artifacts.

The JSON payloads preserve each validated Pydantic artifact without coupling
the persistence layer to agent execution or result-file workflows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
    account_matches: Mapped[list[AccountMatch]] = relationship(
        back_populates="campaign", passive_deletes=True
    )


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


class Account(Base):
    """A workspace-global organisation with a host-derived durable identity key.

    The account owns identity and currently observed company data. Campaign-specific
    fit, evidence, validation, and contacts are associated through ``AccountMatch``.
    """

    __tablename__ = "accounts"

    account_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identity_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    primary_domain: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONPayload, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    matches: Mapped[list[AccountMatch]] = relationship(
        back_populates="account", passive_deletes=True
    )
    employments: Mapped[list[Employment]] = relationship(
        back_populates="account", passive_deletes=True
    )
    endpoints: Mapped[list[CommunicationEndpoint]] = relationship(
        back_populates="account", passive_deletes=True
    )


class AccountMatch(Base):
    """One account candidate within one campaign and ICP, including validation history."""

    __tablename__ = "account_matches"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "icp_id", "account_id", name="uq_account_matches_campaign_icp_account"
        ),
        ForeignKeyConstraint(
            ["campaign_id", "icp_id"],
            ["icps.campaign_id", "icps.icp_id"],
            name="fk_account_matches_icp",
            ondelete="CASCADE",
        ),
    )

    account_match_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.campaign_id", ondelete="CASCADE"), nullable=False
    )
    icp_id: Mapped[str] = mapped_column(String(80), nullable=False)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"), nullable=False
    )
    fit_score: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="needs_review")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONPayload, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    campaign: Mapped[Campaign] = relationship(back_populates="account_matches")
    account: Mapped[Account] = relationship(back_populates="matches")
    validation_runs: Mapped[list[AccountValidationRun]] = relationship(
        back_populates="account_match", passive_deletes=True
    )


class Contact(Base):
    """A person identified by a public professional identity key.

    Without a public profile URL, the identity key includes the account ID to avoid
    conflating people with the same name at different organisations.
    """

    __tablename__ = "contacts"

    contact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identity_key: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONPayload, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    employments: Mapped[list[Employment]] = relationship(
        back_populates="contact", passive_deletes=True
    )
    endpoints: Mapped[list[CommunicationEndpoint]] = relationship(
        back_populates="contact", passive_deletes=True
    )


class Employment(Base):
    """A sourced, current professional relationship between a contact and account."""

    __tablename__ = "employments"
    __table_args__ = (
        UniqueConstraint("account_id", "contact_id", name="uq_employments_account_contact"),
    )

    employment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[str] = mapped_column(
        ForeignKey("contacts.contact_id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    buyer_role: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONPayload, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    account: Mapped[Account] = relationship(back_populates="employments")
    contact: Mapped[Contact] = relationship(back_populates="employments")


class CommunicationEndpoint(Base):
    """A sourced public business endpoint owned by exactly one account or contact."""

    __tablename__ = "communication_endpoints"
    __table_args__ = (
        CheckConstraint(
            "(account_id IS NOT NULL AND contact_id IS NULL) OR "
            "(account_id IS NULL AND contact_id IS NOT NULL)",
            name="ck_communication_endpoints_one_owner",
        ),
        UniqueConstraint(
            "owner_key", "kind", "normalized_value", name="uq_endpoints_owner_kind_value"
        ),
    )

    endpoint_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE")
    )
    contact_id: Mapped[str | None] = mapped_column(
        ForeignKey("contacts.contact_id", ondelete="CASCADE")
    )
    owner_key: Mapped[str] = mapped_column(String(48), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value: Mapped[str] = mapped_column(Text, nullable=False)
    discovery_method: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONPayload, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    account: Mapped[Account | None] = relationship(back_populates="endpoints")
    contact: Mapped[Contact | None] = relationship(back_populates="endpoints")


class AccountValidationRun(Base):
    """An append-only validation assessment for an account match."""

    __tablename__ = "account_validation_runs"

    validation_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_match_id: Mapped[str] = mapped_column(
        ForeignKey("account_matches.account_match_id", ondelete="CASCADE"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONPayload, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    account_match: Mapped[AccountMatch] = relationship(back_populates="validation_runs")


class ResearchJob(Base):
    """A durable claimable unit of campaign, ICP, or account research."""

    __tablename__ = "research_jobs"
    __table_args__ = (
        Index("ix_research_jobs_status_created_at", "status", "created_at"),
        Index("ix_research_jobs_claim_expires_at", "claim_expires_at"),
    )

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    campaign_id: Mapped[str | None] = mapped_column(String(80))
    icp_id: Mapped[str | None] = mapped_column(String(80))
    account_match_id: Mapped[str | None] = mapped_column(String(36))
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    claim_token: Mapped[str | None] = mapped_column(String(128))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
