"""
Core data models: PenaltyNotice (the uploaded/classified notice)
and Appeal (the AI-drafted, human-approved appeal letter).
"""
import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String, DateTime, Text, Enum, ForeignKey, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.services.authority_links import get_authority_links


class PenaltyType(str, enum.Enum):
    PARKING = "parking"
    CONGESTION_CHARGE = "congestion_charge"
    BUS_LANE = "bus_lane"
    UNKNOWN = "unknown"


class NoticeStatus(str, enum.Enum):
    NEW = "new"
    CLASSIFIED = "classified"
    APPEAL_DRAFTED = "appeal_drafted"
    APPEAL_SUBMITTED = "appeal_submitted"
    PAID = "paid"
    RESOLVED = "resolved"


class PenaltyNotice(Base):
    __tablename__ = "penalty_notices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    raw_text = Column(Text, nullable=False)  # the notice text (OCR'd or pasted)
    penalty_type = Column(Enum(PenaltyType), default=PenaltyType.UNKNOWN)
    classification_confidence = Column(Float, nullable=True)
    vehicle_registration = Column(String, nullable=True)
    reference_number = Column(String, nullable=True)  # PCN/parking-charge reference, NOT the vehicle plate
    amount_gbp = Column(Float, nullable=True)
    issue_date = Column(DateTime, nullable=True)
    deadline_date = Column(DateTime, nullable=True)
    issuing_authority = Column(String, nullable=True)
    status = Column(Enum(NoticeStatus), default=NoticeStatus.NEW)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="notices")
    appeals = relationship("Appeal", back_populates="notice")

    # Plain Python properties, NOT database columns — derived on the fly
    # from issuing_authority, so adding these doesn't require a schema
    # migration (this project has no Alembic migrations set up; every
    # extra DB column would mean another `docker compose down -v`). See
    # app/services/authority_links.py for what these actually resolve to
    # and why (real-website links to help the user submit their own real
    # appeal, since this platform's bot only ever submits to the sandbox).
    @property
    def issuing_authority_website(self) -> Optional[str]:
        return get_authority_links(self.issuing_authority)["website"]

    @property
    def issuing_authority_appeal_url(self) -> Optional[str]:
        return get_authority_links(self.issuing_authority)["appeal_url"]

    @property
    def issuing_authority_verify_url(self) -> Optional[str]:
        """A search link the user can click to manually confirm what this
        platform extracted for THIS notice against the issuing authority's
        own real records — see authority_links.py's docstring for why this
        is a search link, not an automated lookup."""
        return get_authority_links(
            self.issuing_authority, self.reference_number, self.vehicle_registration
        )["verify_url"]


class Appeal(Base):
    __tablename__ = "appeals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    notice_id = Column(UUID(as_uuid=True), ForeignKey("penalty_notices.id"), nullable=False)
    draft_text = Column(Text, nullable=False)      # LLM-generated draft
    final_text = Column(Text, nullable=True)        # after user edits
    approved = Column(String, default="pending")    # pending | approved | rejected
    submission_reference = Column(String, nullable=True)  # set once the bot submits it
    created_at = Column(DateTime, default=datetime.utcnow)

    notice = relationship("PenaltyNotice", back_populates="appeals")
