"""
Billing/credits model — records every attempted credit-pack purchase.

This is deliberately separate from real payment processing anywhere else
in the app: this table tracks purchases of the PLATFORM'S OWN service (a
bundle of PCN-processing credits), paid for via Stripe. That's a normal
merchant-of-record sale, not the "platform pays a third party on the
user's behalf" scenario discussed (and declined) for PCN payments in
app/routers/payments.py — see app/services/billing_plans.py for the full
reasoning on why this one's fine to build for real.

A row is created in "pending" status the moment a Stripe Checkout Session
is created (POST /billing/checkout), and flipped to "completed" — which
is the only point credits are actually added to the user's account — once
Stripe's webhook confirms the payment actually went through. This
two-step design, rather than crediting the account immediately on
checkout creation, means an abandoned/failed checkout never grants
credits, and matches Stripe's own recommended pattern (trust the webhook,
not the redirect back to your site, since a user closing the tab after
paying would otherwise never reach a client-side "success" handler).
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class CreditPurchase(Base):
    __tablename__ = "credit_purchases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    plan_id = Column(String, nullable=False)  # key into billing_plans.PLAN_CATALOG
    credits_granted = Column(Integer, nullable=False)
    amount_gbp = Column(Float, nullable=False)
    # Unique so a re-delivered Stripe webhook (Stripe explicitly does not
    # guarantee exactly-once delivery) can't credit the same purchase twice.
    stripe_session_id = Column(String, unique=True, nullable=False, index=True)
    status = Column(String, nullable=False, default="pending")  # pending | completed
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="credit_purchases")


class PCNPayment(Base):
    """A REAL Stripe (test-mode) charge for the platform's own
    facilitation/documentation fee when a user pays several PCNs at once
    through POST /billing/checkout-pcns — see
    app/services/billing_plans.py's PCN_FACILITATION_FEE_GBP_PER_NOTICE
    for why this is a small flat fee unrelated to each notice's own
    amount_gbp, not the parking charge itself.

    Deliberately does NOT touch PenaltyNotice.status — whether a PCN is
    actually settled with the real issuer is tracked separately (and only
    ever simulated) via app/routers/payments.py. This table exists purely
    to record a real sale of the platform's own service, same pattern as
    CreditPurchase above.
    """

    __tablename__ = "pcn_payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    # JSON-encoded list of notice ID strings this facilitation fee covered
    # — stored as text rather than a join table, consistent with this
    # project's "no Alembic, keep schema changes minimal" approach.
    notice_ids = Column(Text, nullable=False)
    notice_count = Column(Integer, nullable=False)
    amount_gbp = Column(Float, nullable=False)  # the facilitation fee actually charged, not the PCN total
    stripe_session_id = Column(String, unique=True, nullable=False, index=True)
    status = Column(String, nullable=False, default="pending")  # pending | completed
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User")
