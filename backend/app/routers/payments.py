"""
Payment router — SIMULATED ONLY.

Real payment processing (taking card details, moving money) is out of
scope: it pulls in PCI-DSS compliance obligations that aren't realistic to
build safely for a one-month student project, and it isn't needed to prove
the concept. This stub exists so the end-to-end demo flow (upload -> review
-> pay OR appeal) is fully clickable. No real transaction, and no card data
of any kind, ever passes through this endpoint.

A stronger version of "pay through our portal" was considered and
deliberately rejected: having the platform take a user's real money and
then actually pay the real PCN on their behalf would make it a regulated
payment service under the UK's Payment Services Regulations 2017 (money
remittance / payment initiation), needing FCA authorisation as a Payment
Institution or e-money issuer — operating that without authorisation
carries real legal exposure (potentially criminal, under FSMA) for
whoever runs it. Not something to build as a "working prototype," because
a working prototype of this IS an unauthorised payment service the moment
real money moves through it. See the project's status doc / FPR notes for
the fuller reasoning. This module stays a pure simulation instead.

The "mock card" fields accepted by /pay-batch below exist purely so the
demo checkout screen looks and feels real — they are never read, stored,
logged, or used to affect the outcome in any way. Payment always
"succeeds" here regardless of what's typed, because nothing about this is
real.

Future work (post-demo, and only within the simulated/no-real-money
design above): swap this for the same sandbox-site Playwright bot used
for appeal submission, pointed at a mock "pay your PCN" form.
"""
import random
import string
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.notice import NoticeStatus, PenaltyNotice
from app.models.user import User
from app.schemas.payment import (
    BatchPaymentItemError,
    BatchPaymentRequest,
    BatchPaymentResult,
    PaymentOut,
)
from app.services.auth import get_current_user

router = APIRouter(prefix="/payments", tags=["payments"])


def _generate_reference() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"SIM-{suffix}"


@router.post("/{notice_id}/pay", response_model=PaymentOut)
def simulate_payment(
    notice_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notice = (
        db.query(PenaltyNotice)
        .filter(PenaltyNotice.id == notice_id, PenaltyNotice.owner_id == current_user.id)
        .first()
    )
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")

    notice.status = NoticeStatus.PAID
    db.commit()

    return PaymentOut(
        notice_id=notice.id,
        status="paid",
        message=(
            "Payment simulated successfully — this is a demo/sandbox "
            "transaction, no real money moved and no card details were "
            "collected."
        ),
        reference=_generate_reference(),
    )


@router.post("/pay-batch", response_model=BatchPaymentResult)
def simulate_batch_payment(
    payload: BatchPaymentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Simulates paying several notices in one action — e.g. after a batch
    upload, pay everything owed in one composite checkout instead of one
    notice at a time. Same no-real-money guarantee as the single-notice
    endpoint above: notice_ids are validated as belonging to the current
    user, but the mock_card_* fields on the request are deliberately never
    inspected — see this module's docstring for why. One notice_id that
    doesn't exist or isn't yours doesn't block the rest of the batch from
    being marked paid.
    """
    succeeded: list[PaymentOut] = []
    failed: list[BatchPaymentItemError] = []
    total = 0.0
    counted_amounts = 0  # tracks whether any notice actually had a known amount, distinct from "total happens to be 0"

    for notice_id in payload.notice_ids:
        notice = (
            db.query(PenaltyNotice)
            .filter(PenaltyNotice.id == notice_id, PenaltyNotice.owner_id == current_user.id)
            .first()
        )
        if not notice:
            failed.append(BatchPaymentItemError(notice_id=notice_id, error="Notice not found"))
            continue

        notice.status = NoticeStatus.PAID
        if notice.amount_gbp:
            total += notice.amount_gbp
            counted_amounts += 1
        succeeded.append(
            PaymentOut(
                notice_id=notice.id,
                status="paid",
                message="Payment simulated successfully — demo/sandbox transaction only.",
                reference=_generate_reference(),
            )
        )

    db.commit()

    return BatchPaymentResult(
        succeeded=succeeded,
        failed=failed,
        total_amount_gbp=round(total, 2) if counted_amounts else None,
    )
