"""
Appealability pre-check service.

Added at the project supervisor's explicit request: after a notice is
uploaded and classified, but *before* the driver has typed a single word
of their own reason for appealing, the platform should give an initial
read on whether the notice looks appealable — based only on what the
issuing authority itself printed on it (the extracted/OCR'd fields and raw
text, plus this user's own other notices for duplicate detection), never
on the driver's own account of events. That account still matters — it's
what `app/services/appeal_generator.py` builds the actual letter around —
but it is deliberately not an input to this check, so the two signals stay
independent: "does the notice itself look procedurally sound?" versus
"does the driver have a good personal story?".

This is a rule-based checklist, consistent with app/services/classifier.py's
own rule-based design philosophy (explainable, testable against specific
real documents, easy to correct line-by-line) rather than an LLM judgement
call on a legally-sensitive question. It is deliberately conservative: it
never asserts that a notice IS invalid or that an appeal WILL succeed, only
that a specific, named, checkable signal was or wasn't found — mirroring
the human-in-the-loop philosophy used everywhere else in this project (see
the approval gate in app/routers/appeals.py). Every result carries a
disclaimer for exactly this reason.

None of these checks are a substitute for reading the actual notice. A
field reported here as "missing" may really be missing from the document
(itself sometimes a genuine procedural point — UK PCNs are required by the
relevant regulations to carry specific prescribed information), or it may
simply be something our OCR/extraction pipeline failed to read — this
module cannot always tell the two apart, and says so in its wording rather
than picking one interpretation confidently.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from app.models.notice import NoticeStatus, PenaltyNotice
from app.services.classifier import is_private_operator

# How many days out from the computed deadline counts as "approaching" —
# close enough that surfacing it (even though the window technically
# hasn't closed) is more useful than staying silent until it's too late.
DEADLINE_APPROACHING_WINDOW_DAYS = 3

# Broad, deliberately loose sanity envelopes for "does this amount look
# like a normal PCN/parking-charge figure for this kind of issuer" — NOT a
# claim about any specific council's or operator's actual current tariff
# (those vary by area and by contravention severity, and change over time).
# Statutory PCNs commonly range from a discounted ~£35 up to a full,
# higher-tier ~£160 (e.g. London bus-lane/moving-traffic contraventions);
# private parking-charge notices, capped by the BPA/IPC Codes of Practice,
# commonly range from a discounted ~£30 up to a full ~£100. An amount
# outside these envelopes isn't proof of anything — it's a prompt to
# double-check the figure against the original notice, nothing stronger.
STATUTORY_PCN_AMOUNT_RANGE_GBP = (25.0, 170.0)
PRIVATE_PCN_AMOUNT_RANGE_GBP = (20.0, 110.0)

# Statuses for which "should I appeal this?" no longer applies — the
# notice has already moved past the point a fresh appeal would act on.
_ALREADY_ACTIONED_STATUSES = {
    NoticeStatus.APPEAL_SUBMITTED: "an appeal has already been submitted for this notice",
    NoticeStatus.PAID: "this notice has already been paid",
    NoticeStatus.RESOLVED: "this notice has already been resolved",
}

DISCLAIMER = (
    "This is an automated first read of what's printed on the notice itself, "
    "not legal advice and not a guarantee of anything — it doesn't know your "
    "side of the story yet, and it can miss things the same way any "
    "automated tool can. Always check the original notice yourself, and if "
    "you're unsure, the relevant adjudicator (London Tribunals or the "
    "Traffic Penalty Tribunal) or a free advice service can help."
)


@dataclass
class AppealabilitySignal:
    code: str
    severity: str  # "high" | "medium" | "low" | "info"
    message: str


@dataclass
class AppealabilityResult:
    verdict: str
    signals: list[AppealabilitySignal] = field(default_factory=list)
    deadline_days_remaining: Optional[int] = None
    disclaimer: str = DISCLAIMER

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "signals": [
                {"code": s.code, "severity": s.severity, "message": s.message}
                for s in self.signals
            ],
            "deadline_days_remaining": self.deadline_days_remaining,
            "disclaimer": self.disclaimer,
        }


def _check_duplicate(notice: PenaltyNotice, other_notices: list[PenaltyNotice]) -> Optional[AppealabilitySignal]:
    if not notice.vehicle_registration or not notice.issue_date:
        return None
    for other in other_notices:
        if other.id == notice.id:
            continue
        if other.vehicle_registration != notice.vehicle_registration:
            continue
        if other.issuing_authority != notice.issuing_authority:
            continue
        if not other.issue_date:
            continue
        if abs((other.issue_date - notice.issue_date).days) <= 1:
            return AppealabilitySignal(
                code="POSSIBLE_DUPLICATE",
                severity="high",
                message=(
                    "This looks like it could be a duplicate of another notice "
                    "already on your account for the same vehicle, issuer, and "
                    "date — being charged twice for what appears to be the same "
                    "event is a common, strong ground for appeal if confirmed."
                ),
            )
    return None


def _check_missing_fields(notice: PenaltyNotice) -> list[AppealabilitySignal]:
    signals = []
    core_fields = [
        ("vehicle_registration", "MISSING_VEHICLE_REGISTRATION", "a vehicle registration"),
        ("issuing_authority", "MISSING_ISSUING_AUTHORITY", "an issuing authority"),
        ("amount_gbp", "MISSING_AMOUNT", "an amount due"),
        ("issue_date", "MISSING_ISSUE_DATE", "a notice/issue date"),
    ]
    for attr, code, label in core_fields:
        if getattr(notice, attr) in (None, ""):
            signals.append(
                AppealabilitySignal(
                    code=code,
                    severity="medium",
                    message=(
                        f"We couldn't find {label} on this notice. UK PCNs are "
                        f"required to carry specific prescribed information — if "
                        f"it's genuinely missing from your actual notice (rather "
                        f"than just missed by our text extraction), that can "
                        f"itself be worth raising. Please check the original "
                        f"document to confirm which it is."
                    ),
                )
            )
    if notice.reference_number in (None, ""):
        signals.append(
            AppealabilitySignal(
                code="MISSING_REFERENCE_NUMBER",
                severity="low",
                message=(
                    "We couldn't find the notice's own reference number. Less "
                    "central than the fields above, but worth a quick check "
                    "against the original document too."
                ),
            )
        )
    return signals


def _check_unusual_amount(notice: PenaltyNotice) -> Optional[AppealabilitySignal]:
    if notice.amount_gbp is None:
        return None
    is_private = is_private_operator(notice.issuing_authority)
    low, high = PRIVATE_PCN_AMOUNT_RANGE_GBP if is_private else STATUTORY_PCN_AMOUNT_RANGE_GBP
    if low <= notice.amount_gbp <= high:
        return None
    kind = "private parking-charge notice" if is_private else "statutory PCN"
    return AppealabilitySignal(
        code="UNUSUAL_AMOUNT",
        severity="low",
        message=(
            f"The amount detected (£{notice.amount_gbp:.2f}) falls outside the "
            f"range we'd normally expect for a {kind} (roughly £{low:.0f}–£{high:.0f}). "
            f"This isn't proof of an error — tariffs do vary — but it's worth "
            f"double-checking the figure against the original notice."
        ),
    )


def _check_deadline(notice: PenaltyNotice, now: datetime) -> tuple[Optional[AppealabilitySignal], Optional[int]]:
    if notice.deadline_date is None:
        return None, None
    days_remaining = (notice.deadline_date - now).days
    if days_remaining < 0:
        return (
            AppealabilitySignal(
                code="DEADLINE_PASSED",
                severity="high",
                message=(
                    "Our estimated appeal/response deadline for this notice has "
                    "already passed. Some issuing authorities will still "
                    "consider a late representation in genuinely exceptional "
                    "circumstances, but don't rely on that — if this is still "
                    "actionable for you, treat it as urgent."
                ),
            ),
            days_remaining,
        )
    if days_remaining <= DEADLINE_APPROACHING_WINDOW_DAYS:
        return (
            AppealabilitySignal(
                code="DEADLINE_APPROACHING",
                severity="medium",
                message=(
                    f"Our estimated appeal/response deadline is only "
                    f"{days_remaining} day(s) away. Whatever you decide, don't "
                    f"let the window close before acting."
                ),
            ),
            days_remaining,
        )
    return None, days_remaining


def check_appealability(
    notice: PenaltyNotice,
    other_notices: Optional[list[PenaltyNotice]] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Run every rule-based check against `notice`'s own printed/extracted
    information (and, for duplicate detection, `other_notices` belonging to
    the same user) and return a single verdict plus the individual signals
    that produced it. `now` is injectable for testing; defaults to the
    current time."""
    now = now or datetime.utcnow()
    other_notices = other_notices or []

    if notice.status in _ALREADY_ACTIONED_STATUSES:
        return AppealabilityResult(
            verdict="not_applicable",
            signals=[
                AppealabilitySignal(
                    code="ALREADY_ACTIONED",
                    severity="info",
                    message=(
                        f"Not applicable — {_ALREADY_ACTIONED_STATUSES[notice.status]}."
                    ),
                )
            ],
        ).as_dict()

    signals: list[AppealabilitySignal] = []

    deadline_signal, days_remaining = _check_deadline(notice, now)
    if deadline_signal:
        signals.append(deadline_signal)

    duplicate_signal = _check_duplicate(notice, other_notices)
    if duplicate_signal:
        signals.append(duplicate_signal)

    signals.extend(_check_missing_fields(notice))

    unusual_amount_signal = _check_unusual_amount(notice)
    if unusual_amount_signal:
        signals.append(unusual_amount_signal)

    if any(s.code == "DEADLINE_PASSED" for s in signals):
        verdict = "deadline_likely_passed"
    elif any(s.severity == "high" for s in signals):
        verdict = "worth_appealing"
    elif signals:
        verdict = "review_recommended"
    else:
        verdict = "no_procedural_issue_detected"

    return AppealabilityResult(
        verdict=verdict,
        signals=signals,
        deadline_days_remaining=days_remaining,
    ).as_dict()
