"""
User account model. Passwords are never stored in plaintext — only the
bcrypt hash (see app/services/auth.py).
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base

# Every new account starts with this many free PCN-processing credits, once
# — not a recurring monthly allowance (see app/services/billing_plans.py
# for the full pricing model and reasoning). One credit is spent per
# notice successfully classified, whether via paste, single upload, or one
# file within a batch upload; see the credit-check/decrement logic in
# app/routers/notices.py.
FREE_SIGNUP_CREDITS = 5


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    credits_remaining = Column(Integer, nullable=False, default=FREE_SIGNUP_CREDITS)
    created_at = Column(DateTime, default=datetime.utcnow)

    notices = relationship("PenaltyNotice", back_populates="owner")
    credit_purchases = relationship("CreditPurchase", back_populates="user")
