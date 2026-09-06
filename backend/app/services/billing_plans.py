"""
The platform's own PCN-credit pricing — a fixed, in-code catalog rather
than a DB-configurable pricing engine, which is the right amount of
complexity for a one-month project (a proper admin-editable pricing table
is real future work, not needed to prove the concept).

Why this is safe to build for real, unlike the earlier declined "platform
pays your real PCN for you" feature: this is the platform selling ITS OWN
service (a bundle of processing credits) to its own users, paid for
through a licensed payment processor (Stripe). No money is ever held on
behalf of, or forwarded to, a third party (a council or parking
operator) — that's what made the earlier idea a regulated payment
service. Selling your own product via Stripe Checkout is completely
standard SaaS billing, with essentially every card-handling/PCI-DSS
concern already offloaded to Stripe's own hosted Checkout page (see
app/routers/billing.py — the backend never sees a raw card number).

Pricing here is illustrative for the demo/report, not a researched
commercial pricing strategy — reasonable, round numbers with a bulk
discount per credit (a standard SaaS pattern), nothing more rigorous than
that.
"""
from typing import Optional

FREE_TIER_CREDITS = 5  # granted once at signup — see app/models/user.py's FREE_SIGNUP_CREDITS

PLAN_CATALOG = {
    "starter_10": {
        "name": "Starter Pack",
        "credits": 10,
        "price_gbp": 4.99,
    },
    "growth_20": {
        "name": "Growth Pack",
        "credits": 20,
        "price_gbp": 8.99,
    },
    "bulk_50": {
        "name": "Bulk Pack",
        "credits": 50,
        "price_gbp": 17.99,
    },
}


def get_plan(plan_id: str) -> Optional[dict]:
    return PLAN_CATALOG.get(plan_id)


def price_in_pence(price_gbp: float) -> int:
    """Stripe wants amounts in the smallest currency unit (pence for GBP),
    as an integer — never a float, to avoid floating-point rounding
    landing a charge a penny off from what was displayed."""
    return round(price_gbp * 100)


# Real Stripe charge for handling several PCNs through the platform in one
# go (see POST /billing/checkout-pcns in app/routers/billing.py) —
# deliberately a small FLAT fee per notice for the platform's own
# facilitation/documentation service, NOT the parking charge amount
# itself. Charging the actual PCN total here would make this
# indistinguishable in substance from "the platform pays your real fine
# for you" — the exact regulated-payment-service scenario
# app/routers/payments.py explains was deliberately kept simulated-only.
# Keeping this fee flat, small, and clearly unrelated to each notice's own
# amount_gbp is what keeps it in the same "selling our own service" bucket
# as the credit packs above, rather than third-party payment/remittance.
PCN_FACILITATION_FEE_GBP_PER_NOTICE = 1.50
