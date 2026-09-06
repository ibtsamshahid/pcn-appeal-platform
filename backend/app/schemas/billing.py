import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PlanOut(BaseModel):
    plan_id: str
    name: str
    credits: int
    price_gbp: float


class CreditsOut(BaseModel):
    credits_remaining: int


class CheckoutRequest(BaseModel):
    plan_id: str


class CheckoutResponse(BaseModel):
    checkout_url: str
    stripe_session_id: str


class PurchaseOut(BaseModel):
    id: uuid.UUID
    plan_id: str
    credits_granted: int
    amount_gbp: float
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PCNCheckoutRequest(BaseModel):
    notice_ids: list[uuid.UUID]


class PCNPaymentOut(BaseModel):
    id: uuid.UUID
    notice_count: int
    amount_gbp: float
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True
