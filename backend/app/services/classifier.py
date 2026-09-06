"""
Classification service.

Starts with a simple rule-based/keyword classifier so you have a working
end-to-end pipeline in week 1. This also gives you a defensible *baseline*
to compare a transformer-based classifier against later in Chapter Six
(technical evaluation: accuracy/precision/recall/F1 of baseline vs ML model).
"""
import re
from datetime import datetime, timedelta
from typing import Optional

from app.models.notice import PenaltyType

KEYWORDS = {
    PenaltyType.PARKING: ["parking", "pcn", "penalty charge notice", "yellow line", "car park"],
    PenaltyType.CONGESTION_CHARGE: ["congestion charge", "ulez", "clean air zone", "emissions"],
    PenaltyType.BUS_LANE: ["bus lane", "bus gate"],
}

# Current-format UK plates are 2 letters, 2 digits, (optional space), 3
# letters (e.g. "AB12 CDE"). The digit slot is deliberately widened to also
# accept O/S/I/Z/B here, because OCR on scanned/photographed PCNs routinely
# misreads digits as visually-similar letters (5<->S, 0<->O, 1<->I, 2<->Z,
# 8<->B) — a real notice tested during development had "55" OCR'd as "SS",
# which silently failed to extract before this fix. Matched digit-slot
# characters are normalized back to digits below.
#
# The `\s?` is now also allowed between the first two groups (not just
# before the suffix) — a UKPC "Notice to Keeper" tested later on had OCR
# occasionally introduce a stray space at a glyph boundary inside the
# first four characters (kerning in the scanned table made "FY55" look
# like two separate words to Tesseract on some renders); the original
# pattern only tolerated a gap right before the 3-letter suffix, so a plate
# that split earlier silently failed to match at all.
REG_PLATE_RE = re.compile(r"\b([A-Z]{2})\s?([0-9OSIZB]{2})\s?([A-Z]{3})\b", re.IGNORECASE)
_OCR_DIGIT_FIXUPS = {"O": "0", "S": "5", "I": "1", "Z": "2", "B": "8"}
# Reverse of the above: inside a label-anchored window (see
# LABELLED_REG_RE/_fixup_plate_candidate below) a digit sometimes shows up
# where OCR should have produced a similar-looking letter instead. Only
# used in that narrow, labelled context — applying it to the free-floating
# REG_PLATE_RE search over the whole document would raise the false-match
# rate too much.
_OCR_LETTER_FIXUPS = {v: k for k, v in _OCR_DIGIT_FIXUPS.items()}

# Tried first, before the free-floating REG_PLATE_RE search below — same
# "labelled beats generic" strategy as LABELLED_DATE_RE/DATE_RE further
# down. Anchoring on the actual "Vehicle Registration:"-style label lets a
# messier candidate token (one that wouldn't cleanly match REG_PLATE_RE
# out in the wild text) still be recovered and fixed up, since we know
# *where* to look rather than searching blind.
# "vehicle\s*registration" alone (without also trying to consume a
# trailing "number"/"mark"/"no." right after it) used to be a real bug on
# clean, non-OCR pasted text: for a label like "Vehicle Registration
# Number:", that shorter alternative matched first and stopped at
# "Registration", leaving "Number" itself sitting in the gap the value
# regex then tried to search from. Since the gap pattern `[^A-Z0-9]{0,15}`
# can't skip over alphanumeric characters, it immediately hit "Number"'s
# own letters and the value-capture regex ended up greedily matching
# "Number" as if IT were the plate (6 letters — rejected by
# _fixup_plate_candidate's exact-7-characters check, but only after
# consuming this match position). That sent extraction down to the
# free-floating REG_PLATE_RE fallback, which then matched the unrelated
# phrase "of Issue" a few words earlier in the document (its OCR-tolerant
# digit slot treats "I"/"S" as read as digits, so "of" + "Is" + "sue"
# satisfied the 2-letter/2-digit/3-letter shape) and returned "OF15 SUE"
# instead of the notice's real, later, clearly-labelled plate "LD70 GHK".
# Fixed by letting "vehicle\s*registration" also consume that trailing
# word when present, so the label match — and therefore the gap the value
# is searched from — lands in the right place.
LABELLED_REG_RE = re.compile(
    r"(?:vehicle\s*registration\s*(?:number|mark|no\.?)?|registration\s*(?:number|mark)|"
    r"reg(?:istration)?\.?\s*no\.?|\bvrm\b)"
    r"[^A-Z0-9]{0,15}([A-Z0-9]{2,4}\s?[A-Z0-9]{3,4})",
    re.IGNORECASE,
)

AMOUNT_RE = re.compile(r"£\s?(\d+(?:\.\d{2})?)")

# UK PCNs and private parking-charge notices each have their own unique
# reference for the notice itself (distinct from the vehicle's plate) —
# councils/TfL usually call it a "Penalty Charge Notice Number" or "PCN
# Number", private operators (UKPC, ParkingEye, APCOA, etc.) more often
# "Parking Charge Reference Number". Only one real example has been seen
# so far (UKPC's "Parking Charge Reference Number: 3171662150918"), so
# this list of labels is a reasonable, deliberately non-exhaustive first
# pass covering the label wording actually documented across these
# formats — expect to extend it, same as ISSUING_AUTHORITY_PATTERNS,
# as more real notices turn up.
#
# Two real gaps found testing against an actual photographed Redbridge PCN
# (PCN No: AF16369001):
#   1. The label list included "no.?" as an accepted abbreviation after
#      "pcn" and "ticket" but not after "notice" — so "Notice No.:
#      AF16369001" (the payment-slip half of the same notice) silently
#      failed to match at all, even though "PCN No:" earlier in the same
#      document did match the label.
#   2. Where the label DID match ("PCN No: AF 16369001"), OCR had
#      introduced a stray space inside the reference itself — the old
#      single capture group `[A-Z0-9]{6,15}` stopped at that space and
#      grabbed only "AF" (too short to satisfy the {6,15} minimum), so the
#      whole match failed at that position too.
# Fixed by adding "no.?" after "notice" below, and by matching the value
# as either one contiguous alnum run (group 1, the common case — kept
# separate so a normal reference isn't truncated by an unrelated later
# group needing its own minimum length) OR, only if that fails, two
# shorter runs either side of exactly one stray space (groups 2+3) — same
# "tolerate one OCR-introduced gap" approach already used for the vehicle
# plate and date patterns above.
REFERENCE_NUMBER_RE = re.compile(
    r"(?:parking\s+charge\s+reference\s+number|penalty\s+charge\s+notice\s+number|"
    r"penalty\s+charge\s+number|pcn\s*(?:number|no\.?|ref(?:erence)?\.?)|"
    r"notice\s*(?:number|no\.?|ref(?:erence)?\.?)|"
    r"charge\s+reference(?:\s+number)?|ticket\s*(?:number|no\.?))"
    r"[^A-Z0-9]{0,15}(?:([A-Z0-9]{6,15})|([A-Z0-9]{2,6})\s([A-Z0-9]{4,10}))",
    re.IGNORECASE,
)

# Prefer an explicitly-labelled notice date over just grabbing the first
# date anywhere in the text (a PCN usually has several: notice date,
# contravention date, sometimes a payment-by date). Falls back to the
# generic pattern if no labelled one is found.
#
# The date fragment itself tolerates stray whitespace around the slashes
# (`\s?`) and the gap between the label and the date tolerates a run of
# non-digit characters (not just ":"/"-") — both were needed after testing
# against real notices. Two real failures drove the current shape:
#   1. A council PCN where OCR produced "Date of this notice: |20/05/ 2026"
#      (a stray "|" and an extra space before the year) — without the
#      tolerant gap/fragment, this silently fell back to matching the
#      *contravention* date instead, a different date entirely.
#   2. A private parking-operator notice (APCOA) labelled it "Date of
#      Issue of this Charge: 05/05/2026" — a longer label phrase than
#      "issued" alone, AND the document had a scanning-app watermark
#      ("CamScanner 04-06-2026") right at the top that the old fallback
#      grabbed instead, silently producing a wrong date (and therefore a
#      wrong computed deadline — the kind of bug worth catching, since a
#      user could rely on an appeal deadline that's simply incorrect).
# The gap tolerance below (25 chars) covers "of this Charge: " between the
# "date of issue" label and the actual date.
_DATE_FRAGMENT = r"\d{1,2}\s?[/-]\s?\d{1,2}\s?[/-]\s?\d{2,4}"
LABELLED_DATE_RE = re.compile(
    rf"(?:date of this notice|date of issue|issued)[^\d]{{0,25}}({_DATE_FRAGMENT})",
    re.IGNORECASE,
)
DATE_RE = re.compile(rf"\b({_DATE_FRAGMENT})\b")

# Small, deliberately non-exhaustive lookup of UK issuing authorities —
# both local-authority/TfL enforcement (statutory PCNs) and the private
# parking operators that issue "Parking Charge Notices" on private land
# (contractual, not statutory, but visually near-identical and just as
# often what a user is trying to appeal). This is a rule-based baseline
# (see module docstring) — it will only recognise authorities it's told
# about, which is a real limitation worth naming in the report rather than
# a bug: it's exactly the kind of gap a trained classifier (Chapter Six
# comparison) would generalise past. The private-operator list below is
# deliberately not exhaustive — added APCOA after it showed up as a real
# "not detected" case; expect to extend this list if more operators turn
# up during further testing.
ISSUING_AUTHORITY_PATTERNS = [
    (re.compile(r"transport for london", re.IGNORECASE), "Transport for London"),
    (re.compile(r"\btfl\b", re.IGNORECASE), "Transport for London"),
    # Tolerates OCR misreading the "L" of "London" as a stray "|" (seen on
    # a real photographed Redbridge PCN: "| ondon Borough of Redbridge",
    # with the space between "|" and "ondon" also OCR noise) — same
    # "tolerate the documented real OCR confusion" approach used elsewhere
    # in this module. Without this, that notice's authority silently fell
    # through every pattern below to the "civil enforcement" one further
    # down (see its comment) and was misattributed to a private operator.
    (re.compile(r"[l|]\s?ondon\s*borough\s*of\s*([a-z]+)", re.IGNORECASE), None),
    (re.compile(r"\b([a-z]+ (?:city|borough|county) council)\b", re.IGNORECASE), None),
    (re.compile(r"\bapcoa\b", re.IGNORECASE), "APCOA Parking"),
    (re.compile(r"\bparkingeye\b", re.IGNORECASE), "ParkingEye"),
    (re.compile(r"\beuro car parks?\b", re.IGNORECASE), "Euro Car Parks"),
    (re.compile(r"\bexcel parking\b", re.IGNORECASE), "Excel Parking Services"),
    (re.compile(r"\bukpc\b|\buk parking control\b", re.IGNORECASE), "UK Parking Control (UKPC)"),
    (re.compile(r"\bsmart parking\b", re.IGNORECASE), "Smart Parking"),
    (re.compile(r"\bhighview parking\b", re.IGNORECASE), "Highview Parking"),
    # Requires "limited"/"ltd" — the real private operator's actual trading
    # name — rather than the old bare "civil enforcement" match. That
    # looser pattern was matching the generic job title "Civil Enforcement
    # Officer", which appears on the vast majority of real UK PCNs
    # regardless of who issued them (it's the standard TMA 2004 term for a
    # parking warden, council or private), so it was silently misattributing
    # council notices — like the real Redbridge one above — to this one
    # specific private company just because their officer had that title.
    (re.compile(r"\bcivil enforcement (?:ltd\.?|limited)\b", re.IGNORECASE), "Civil Enforcement Limited"),
    (re.compile(r"\bhorizon parking\b", re.IGNORECASE), "Horizon Parking"),
    (re.compile(r"\bvcs\b|\bvehicle control services\b", re.IGNORECASE), "Vehicle Control Services (VCS)"),
    (re.compile(r"\bmet parking\b", re.IGNORECASE), "MET Parking Services"),
    (re.compile(r"\bparking control management\b|\bpcm\b", re.IGNORECASE), "Parking Control Management (PCM)"),
]

# Standard full payment period for a UK PCN, per the "28 days beginning
# with the date of this notice" wording these notices consistently use.
# This is a reasonable default, not a guarantee — some notice types or
# issuers may vary it, so treat the computed deadline as indicative, not
# authoritative (worth a caveat in the report/UI, not something to present
# as legal advice).
STANDARD_PAYMENT_PERIOD_DAYS = 28


def _normalize_plate(match: "re.Match") -> str:
    prefix, digit_slot, suffix = match.groups()
    digits = "".join(_OCR_DIGIT_FIXUPS.get(ch.upper(), ch) for ch in digit_slot)
    return f"{prefix.upper()}{digits} {suffix.upper()}"


def _fixup_plate_candidate(raw: str) -> Optional[str]:
    """Given a loose alphanumeric token grabbed from right after a
    "vehicle registration"-style label, try to coerce it into the current
    UK plate shape (2 letters, 2 digits, 3 letters) by undoing common OCR
    letter<->digit confusions in whichever direction each position calls
    for, then validate the result actually fits the shape. Returns None
    if it can't be made to fit — this is a last-resort recovery path for
    a labelled match, not a licence to accept anything as a plate."""
    cleaned = re.sub(r"\s+", "", raw).upper()
    if len(cleaned) != 7:
        # A character was likely dropped (or an extra one picked up) by
        # OCR right next to the label — not confident enough to guess
        # which, so bail out and let the free-floating regex (or a human
        # reading the raw text) have the final word instead.
        return None

    prefix, digit_slot, suffix = cleaned[:2], cleaned[2:4], cleaned[4:]
    prefix = "".join(_OCR_LETTER_FIXUPS.get(ch, ch) for ch in prefix)
    digits = "".join(_OCR_DIGIT_FIXUPS.get(ch, ch) for ch in digit_slot)
    suffix = "".join(_OCR_LETTER_FIXUPS.get(ch, ch) for ch in suffix)

    if not (prefix.isalpha() and digits.isdigit() and suffix.isalpha()):
        return None
    return f"{prefix}{digits} {suffix}"


def _find_vehicle_registration(text: str) -> Optional[str]:
    """Labelled-anchor first (tolerant of stray OCR noise near the actual
    "Vehicle Registration:"-style label), falling back to the
    free-floating REG_PLATE_RE search of the whole text if that fails —
    the same "labelled beats generic" strategy already used for dates
    (see LABELLED_DATE_RE/DATE_RE) applied here to the vehicle plate."""
    labelled_match = LABELLED_REG_RE.search(text)
    if labelled_match:
        fixed = _fixup_plate_candidate(labelled_match.group(1))
        if fixed:
            return fixed

    reg_match = REG_PLATE_RE.search(text)
    if reg_match:
        return _normalize_plate(reg_match)

    return None


def _extract_reference_number(text: str) -> Optional[str]:
    match = REFERENCE_NUMBER_RE.search(text)
    if not match:
        return None
    combined = match.group(1) or ((match.group(2) or "") + (match.group(3) or ""))
    return re.sub(r"\s+", "", combined).upper()


# The canonical private-operator names that ISSUING_AUTHORITY_PATTERNS can
# return (everything in that list except Transport for London and the two
# dynamic council/borough patterns, which resolve to a captured name rather
# than one of these fixed strings). Exported so other modules — currently
# app/services/appealability_checker.py, which needs to reason about
# "statutory PCN vs private parking-charge notice" without duplicating this
# list — have exactly one place to look this up rather than re-deriving it.
PRIVATE_OPERATOR_NAMES = frozenset(
    canonical_name
    for _pattern, canonical_name in ISSUING_AUTHORITY_PATTERNS
    if canonical_name and canonical_name != "Transport for London"
)


def is_private_operator(issuing_authority: Optional[str]) -> bool:
    """True if `issuing_authority` is one of the recognised private parking
    operators (a contractual parking-charge notice on private land) rather
    than a statutory PCN issuer (a council or Transport for London). Returns
    False for None/unrecognised input — callers should treat "unknown" as
    "don't assume either way", not as "assume statutory"."""
    return issuing_authority in PRIVATE_OPERATOR_NAMES


def _extract_issuing_authority(text: str) -> Optional[str]:
    for pattern, canonical_name in ISSUING_AUTHORITY_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if canonical_name:
            return canonical_name
        # Dynamic capture (council name) — title-case it and rebuild the
        # phrase depending on which alternative matched.
        captured = match.group(1)
        if "borough of" in match.group(0).lower():
            return f"London Borough of {captured.title()}"
        return captured.title()
    return None


def _parse_uk_date(raw: str) -> Optional[datetime]:
    raw = re.sub(r"\s+", "", raw)  # strip any OCR-introduced whitespace around slashes
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def classify_notice(text: str) -> dict:
    """Returns a dict with penalty_type, confidence, and extracted fields.

    Confidence here is a simple keyword-match ratio — replace with a real
    model's probability output once the ML classifier (week 2) is in place.
    """
    lowered = text.lower()
    best_type = PenaltyType.UNKNOWN
    best_score = 0.0

    for ptype, keywords in KEYWORDS.items():
        matches = sum(1 for kw in keywords if kw in lowered)
        score = matches / len(keywords)
        if score > best_score:
            best_score = score
            best_type = ptype

    amount_match = AMOUNT_RE.search(text)
    date_match = LABELLED_DATE_RE.search(text) or DATE_RE.search(text)

    issue_date = _parse_uk_date(date_match.group(1)) if date_match else None
    deadline_date = (
        issue_date + timedelta(days=STANDARD_PAYMENT_PERIOD_DAYS) if issue_date else None
    )

    return {
        "penalty_type": best_type,
        "confidence": round(best_score, 2),
        "vehicle_registration": _find_vehicle_registration(text),
        "amount_gbp": float(amount_match.group(1)) if amount_match else None,
        "issuing_authority": _extract_issuing_authority(text),
        "issue_date": issue_date,
        "deadline_date": deadline_date,
        "reference_number": _extract_reference_number(text),
    }
