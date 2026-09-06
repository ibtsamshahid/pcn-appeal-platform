"""
Billing router — subscription/credit-pack purchases via Stripe Checkout.

Design notes for the report:
- This sells the PLATFORM'S OWN service (PCN-processing credits), not a
  payment made on a user's behalf to a third party — see
  app/services/billing_plans.py for why that distinction matters and why
  this, unlike the earlier declined real-PCN-payment idea, is fine to
  build for real.
- Card data never touches this backend at all: POST /billing/checkout
  creates a Stripe-hosted Checkout Session and returns its URL; the
  browser is redirected there, and Stripe's own page collects the card.
  This offloads essentially all PCI-DSS scope to Stripe, which is the
  standard, recommended way for a small project (or any project) to
  accept cards without becoming PCI-DSS scope itself.
- Credits are granted ONLY from the webhook handler below, never from the
  checkout-creation step or the success-page redirect — Stripe explicitly
  warns that the redirect back to your site is not reliable proof of
  payment (the user might close the tab before it fires), so the webhook
  is the single source of truth. The webhook handler is idempotent
  against Stripe's at-least-once delivery guarantee (keyed on
  stripe_session_id, never crediting the same completed purchase twice).
"""
import json
import os
from datetime import datetime

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.billing import CreditPurchase, PCNPayment
from app.models.notice import PenaltyNotice
from app.models.user import User
from app.schemas.billing import (
    CheckoutRequest,
    CheckoutResponse,
    CreditsOut,
    PCNCheckoutRequest,
    PCNPaymentOut,
    PlanOut,
    PurchaseOut,
)
from app.services.auth import get_current_user
from app.services.billing_plans import (
    PCN_FACILITATION_FEE_GBP_PER_NOTICE,
    PLAN_CATALOG,
    get_plan,
    price_in_pence,
)

router = APIRouter(prefix="/billing", tags=["billing"])

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
# Where Stripe Checkout sends the browser back after payment succeeds/is
# cancelled. Point this at wherever the frontend is actually served from;
# defaults to the bundled demo UI's own origin for local dev.
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:8000")

stripe.api_key = STRIPE_SECRET_KEY


@router.get("/plans", response_model=list[PlanOut])
def list_plans():
    """The fixed credit-pack catalog — see billing_plans.py for the
    pricing and why it's a fixed in-code catalog rather than a DB-driven
    pricing engine. Still requires login, like every other endpoint here,
    purely for consistency (there's no real reason this couldn't be
    public)."""
    return [
        PlanOut(plan_id=plan_id, name=p["name"], credits=p["credits"], price_gbp=p["price_gbp"])
        for plan_id, p in PLAN_CATALOG.items()
    ]


@router.get("/credits", response_model=CreditsOut)
def get_credits(current_user: User = Depends(get_current_user)):
    return CreditsOut(credits_remaining=current_user.credits_remaining)


@router.get("/purchases", response_model=list[PurchaseOut])
def list_purchases(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Purchase history including still-"pending" ones (e.g. a checkout
    that was started but abandoned before paying) — useful for a user to
    see why credits from a purchase haven't shown up yet."""
    return (
        db.query(CreditPurchase)
        .filter(CreditPurchase.user_id == current_user.id)
        .order_by(CreditPurchase.created_at.desc())
        .all()
    )


@router.get("/pcn-payments", response_model=list[PCNPaymentOut])
def list_pcn_payments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """History of real Stripe facilitation-fee charges for batch PCN
    handling (POST /billing/checkout-pcns below) — separate from
    GET /billing/purchases, which is credit-pack purchases."""
    return (
        db.query(PCNPayment)
        .filter(PCNPayment.user_id == current_user.id)
        .order_by(PCNPayment.created_at.desc())
        .all()
    )


@router.post("/checkout", response_model=CheckoutResponse)
def create_checkout(
    payload: CheckoutRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Creates a Stripe-hosted Checkout Session for the chosen credit pack
    and records a "pending" CreditPurchase row against it. Credits are NOT
    granted here — only once the webhook below confirms payment actually
    completed."""
    plan = get_plan(payload.plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Unknown plan_id: {payload.plan_id}")

    if not STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=500,
            detail=(
                "Stripe isn't configured on this server yet — set STRIPE_SECRET_KEY "
                "in .env (a Stripe TEST mode key is fine, and safe: no real money "
                "moves in test mode)."
            ),
        )

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "gbp",
                        "product_data": {
                            "name": f"{plan['name']} — {plan['credits']} PCN credits",
                        },
                        "unit_amount": price_in_pence(plan["price_gbp"]),
                    },
                    "quantity": 1,
                }
            ],
            success_url=f"{FRONTEND_BASE_URL}/?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_BASE_URL}/?checkout=cancel",
            metadata={"user_id": str(current_user.id), "plan_id": payload.plan_id},
        )
    except stripe.error.StripeError as exc:
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc}")

    purchase = CreditPurchase(
        user_id=current_user.id,
        plan_id=payload.plan_id,
        credits_granted=plan["credits"],
        amount_gbp=plan["price_gbp"],
        stripe_session_id=session.id,
        status="pending",
    )
    db.add(purchase)
    db.commit()

    return CheckoutResponse(checkout_url=session.url, stripe_session_id=session.id)


@router.post("/checkout-pcns", response_model=CheckoutResponse)
def create_pcn_checkout(
    payload: PCNCheckoutRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Creates a REAL Stripe Checkout Session (test mode, same as
    /checkout above) for handling several PCNs at once — but charges the
    platform's own small, flat facilitation/documentation fee
    (PCN_FACILITATION_FEE_GBP_PER_NOTICE), never the PCN amounts
    themselves. This deliberately does NOT mark any notice as paid — see
    PCNPayment's docstring in app/models/billing.py for why that stays a
    hard line: whether the real fine is settled with the actual issuing
    authority is tracked only via the simulated endpoints in
    app/routers/payments.py, never here.
    """
    if not payload.notice_ids:
        raise HTTPException(status_code=400, detail="Select at least one notice")

    notices = (
        db.query(PenaltyNotice)
        .filter(
            PenaltyNotice.id.in_(payload.notice_ids),
            PenaltyNotice.owner_id == current_user.id,
        )
        .all()
    )
    found_ids = {n.id for n in notices}
    missing = [str(nid) for nid in payload.notice_ids if nid not in found_ids]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Notice(s) not found or not yours: {', '.join(missing)}",
        )

    if not STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=500,
            detail=(
                "Stripe isn't configured on this server yet — set STRIPE_SECRET_KEY "
                "in .env (a Stripe TEST mode key is fine, and safe: no real money "
                "moves in test mode)."
            ),
        )

    fee_total = round(PCN_FACILITATION_FEE_GBP_PER_NOTICE * len(notices), 2)

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "gbp",
                        "product_data": {
                            "name": f"PCN handling & documentation fee — {len(notices)} notice(s)",
                            "description": (
                                "Covers this platform's own processing of the selected PCNs "
                                "(review, appeal drafting, tracking). This does NOT pay the "
                                "parking charge itself — that's still owed directly to the "
                                "issuing authority."
                            ),
                        },
                        "unit_amount": price_in_pence(PCN_FACILITATION_FEE_GBP_PER_NOTICE),
                    },
                    "quantity": len(notices),
                }
            ],
            success_url=f"{FRONTEND_BASE_URL}/?pcn_checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_BASE_URL}/?pcn_checkout=cancel",
            metadata={
                "user_id": str(current_user.id),
                "notice_ids": json.dumps([str(n.id) for n in notices]),
            },
        )
    except stripe.error.StripeError as exc:
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc}")

    payment = PCNPayment(
        user_id=current_user.id,
        notice_ids=json.dumps([str(n.id) for n in notices]),
        notice_count=len(notices),
        amount_gbp=fee_total,
        stripe_session_id=session.id,
        status="pending",
    )
    db.add(payment)
    db.commit()

    return CheckoutResponse(checkout_url=session.url, stripe_session_id=session.id)


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Stripe calls this directly (not via the browser, no Authorization
    header) after events happen on their side. Signature verification
    (using STRIPE_WEBHOOK_SECRET) is what proves a request genuinely came
    from Stripe rather than anyone who finds this URL — never skip it.

    For local testing, use the Stripe CLI: `stripe listen --forward-to
    localhost:8000/billing/webhook`, which also prints the webhook secret
    to put in .env as STRIPE_WEBHOOK_SECRET.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid webhook signature/payload: {exc}")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        stripe_session_id = session["id"]

        purchase = (
            db.query(CreditPurchase)
            .filter(CreditPurchase.stripe_session_id == stripe_session_id)
            .first()
        )
        if purchase and purchase.status != "completed":
            # Idempotent against Stripe's at-least-once delivery: only
            # credit the account the first time this session is seen as
            # completed, never on a redelivered/duplicate event.
            user = db.query(User).filter(User.id == purchase.user_id).first()
            if user:
                user.credits_remaining += purchase.credits_granted
            purchase.status = "completed"
            purchase.completed_at = datetime.utcnow()
            db.commit()

        # Same idempotent pattern for the PCN facilitation-fee charge —
        # deliberately does NOT touch any PenaltyNotice.status (see
        # PCNPayment's docstring for why real payment here stays scoped
        # to "the platform got paid for its own service", never "this PCN
        # got settled with the real issuer").
        pcn_payment = (
            db.query(PCNPayment)
            .filter(PCNPayment.stripe_session_id == stripe_session_id)
            .first()
        )
        if pcn_payment and pcn_payment.status != "completed":
            pcn_payment.status = "completed"
            pcn_payment.completed_at = datetime.utcnow()
            db.commit()

    return {"received": True}
