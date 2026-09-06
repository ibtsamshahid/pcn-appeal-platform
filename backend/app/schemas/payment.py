import uuid
from typing import Optional

from pydantic import BaseModel


class PaymentOut(BaseModel):
    notice_id: uuid.UUID
    status: str
    message: str
    reference: str


class BatchPaymentRequest(BaseModel):
    notice_ids: list[uuid.UUID]
    # Purely cosmetic "mock card" fields so the demo UI has a form to post
    # to — see payments.py's module docstring. The backend never reads,
    # stores, logs, or otherwise acts on these; they exist only so the
    # payment screen looks and feels like a real checkout, while the whole
    # point of this stub is that no real card data is ever collected.
    mock_card_number: Optional[str] = None
    mock_card_expiry: Optional[str] = None
    mock_card_cvc: Optional[str] = None


class BatchPaymentItemError(BaseModel):
    notice_id: uuid.UUID
    error: str


class BatchPaymentResult(BaseModel):
    succeeded: list[PaymentOut]
    failed: list[BatchPaymentItemError]
    total_amount_gbp: Optional[float] = None
