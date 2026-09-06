from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
import uuid

from app.database import get_db
from app.models.notice import PenaltyNotice, NoticeStatus
from app.models.user import User
from app.schemas.notice import (
    BatchUploadItemError,
    BatchUploadResult,
    NoticeCreate,
    NoticeOut,
    NoticeUploadOut,
)
from app.services.appealability_checker import check_appealability
from app.services.auth import get_current_user
from app.services.classifier import classify_notice
from app.services.pdf_extractor import PDFExtractionError, extract_text_from_pdf

router = APIRouter(prefix="/notices", tags=["notices"])

MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_BATCH_SIZE = 20  # arbitrary but generous — a bad-actor guard, not a real expected ceiling


class InsufficientCreditsError(Exception):
    """Raised when a user has no PCN-processing credits left. Every new
    account gets FREE_SIGNUP_CREDITS (see app/models/user.py) once; after
    that, more come from a credit-pack purchase via /billing/checkout
    (see app/routers/billing.py)."""


def _classify_and_store(raw_text: str, db: Session, current_user: User) -> PenaltyNotice:
    """Shared by the pasted-text and PDF-upload paths: run the classifier
    and persist the resulting notice against the logged-in user. Spends
    exactly one credit per successfully-classified notice — checked
    before doing any work (so a request that will fail doesn't waste
    classification effort) and decremented only after the notice is
    actually stored (so a failure partway through never costs a credit)."""
    if current_user.credits_remaining <= 0:
        raise InsufficientCreditsError(
            "You're out of PCN credits. Every account starts with 5 free credits; "
            "buy more via GET /billing/plans + POST /billing/checkout."
        )

    result = classify_notice(raw_text)

    notice = PenaltyNotice(
        owner_id=current_user.id,
        raw_text=raw_text,
        penalty_type=result["penalty_type"],
        classification_confidence=result["confidence"],
        vehicle_registration=result["vehicle_registration"],
        reference_number=result["reference_number"],
        amount_gbp=result["amount_gbp"],
        issuing_authority=result["issuing_authority"],
        issue_date=result["issue_date"],
        deadline_date=result["deadline_date"],
        status=NoticeStatus.CLASSIFIED,
    )
    db.add(notice)
    current_user.credits_remaining -= 1
    db.add(current_user)
    db.commit()
    db.refresh(notice)
    return notice


def _attach_appealability(notice: PenaltyNotice, db: Session, current_user: User) -> PenaltyNotice:
    """Computes the rule-based appealability pre-check (see
    app/services/appealability_checker.py) and sets it as a transient
    attribute on `notice` — not a database column, so every read reflects
    the checker's current logic, even for notices classified before this
    feature existed. Needs one extra query for the user's *other* notices
    (duplicate detection compares `notice` against them); callers that
    already have the full list in hand (see list_notices below) should use
    `_attach_appealability_batch` instead to avoid the N+1 query pattern."""
    other_notices = (
        db.query(PenaltyNotice)
        .filter(PenaltyNotice.owner_id == current_user.id, PenaltyNotice.id != notice.id)
        .all()
    )
    notice.appealability = check_appealability(notice, other_notices=other_notices)
    return notice


def _attach_appealability_batch(notices: list[PenaltyNotice]) -> list[PenaltyNotice]:
    """Same as _attach_appealability, but for a list already fetched in one
    query (e.g. the logged-in user's full notice list) — each notice is
    checked against every *other* notice already in hand, with no further
    DB queries needed."""
    for notice in notices:
        others = [n for n in notices if n.id != notice.id]
        notice.appealability = check_appealability(notice, other_notices=others)
    return notices


@router.post("", response_model=NoticeOut)
def create_notice(
    payload: NoticeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Accepts raw notice text, classifies it, and stores the result
    against the logged-in user."""
    try:
        notice = _classify_and_store(payload.raw_text, db, current_user)
        return _attach_appealability(notice, db, current_user)
    except InsufficientCreditsError as exc:
        raise HTTPException(status_code=402, detail=str(exc))


def _extract_and_classify_upload(
    file: UploadFile, db: Session, current_user: User
) -> NoticeUploadOut:
    """Shared by the single-file and batch upload endpoints: validates one
    uploaded PDF, extracts its text, classifies it, and stores the result.
    Raises ValueError for anything wrong with the file itself (bad
    content-type, empty, too large) and PDFExtractionError if extraction
    fails — both are handled differently by the two callers (single-file
    turns them into an HTTP error response; batch records them per-file
    and keeps going)."""
    if file.content_type != "application/pdf":
        raise ValueError("Only PDF files are supported")

    pdf_bytes = file.file.read()
    if not pdf_bytes:
        raise ValueError("Uploaded file is empty")
    if len(pdf_bytes) > MAX_UPLOAD_SIZE_BYTES:
        raise ValueError("File too large (max 10MB)")

    extraction = extract_text_from_pdf(pdf_bytes)  # may raise PDFExtractionError
    notice = _classify_and_store(extraction["text"], db, current_user)
    notice = _attach_appealability(notice, db, current_user)

    return NoticeUploadOut(
        **NoticeOut.model_validate(notice).model_dump(),
        extraction_method=extraction["method"],
    )


@router.post("/upload", response_model=NoticeUploadOut)
def upload_notice(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Accepts a PCN as a PDF, extracts its text (direct extraction, with
    an OCR fallback for scanned/photographed notices), classifies it, and
    stores the result exactly as POST /notices does."""
    try:
        return _extract_and_classify_upload(file, db, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PDFExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except InsufficientCreditsError as exc:
        raise HTTPException(status_code=402, detail=str(exc))


@router.post("/upload-batch", response_model=BatchUploadResult)
def upload_notices_batch(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Accepts several PCN PDFs in one request and classifies each
    independently — one bad, corrupt, or non-PDF file in the batch doesn't
    block the rest from being processed. Same extraction/classification
    pipeline as POST /notices/upload, just looped, with per-file success/
    failure tracked separately rather than the whole request failing on
    the first bad file.

    Useful both for real use (uploading a backlog of real PCNs at once)
    and for testing the classifier against many real notices in one go —
    each upload here is exactly as independent and safe as uploading them
    one at a time through the single-file endpoint; there's no shared
    "training" step, since the classifier is a rule-based baseline (see
    app/services/classifier.py's module docstring) rather than a model
    that learns from what's uploaded.
    """
    if len(files) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files in one batch (max {MAX_BATCH_SIZE}) — split into smaller batches",
        )

    succeeded: list[NoticeUploadOut] = []
    failed: list[BatchUploadItemError] = []

    for file in files:
        filename = file.filename or "unnamed file"

        # Checked here too (not just inside _classify_and_store) so a
        # user who runs out of credits partway through a batch gets an
        # immediate, cheap "insufficient credits" result for every
        # remaining file instead of paying the cost of OCR/extraction on
        # each one only to fail at the last step anyway.
        if current_user.credits_remaining <= 0:
            failed.append(
                BatchUploadItemError(
                    filename=filename,
                    error="Insufficient credits — buy more via /billing/plans before this file can be processed.",
                )
            )
            continue

        try:
            succeeded.append(_extract_and_classify_upload(file, db, current_user))
        except ValueError as exc:
            failed.append(BatchUploadItemError(filename=filename, error=str(exc)))
        except PDFExtractionError as exc:
            failed.append(BatchUploadItemError(filename=filename, error=str(exc)))
        except InsufficientCreditsError as exc:
            failed.append(BatchUploadItemError(filename=filename, error=str(exc)))
        except Exception as exc:  # noqa: BLE001 - one bad file must never sink the whole batch
            failed.append(BatchUploadItemError(filename=filename, error=f"Unexpected error: {exc}"))

    return BatchUploadResult(succeeded=succeeded, failed=failed)


@router.get("", response_model=list[NoticeOut])
def list_notices(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns only the logged-in user's own notices."""
    notices = (
        db.query(PenaltyNotice)
        .filter(PenaltyNotice.owner_id == current_user.id)
        .order_by(PenaltyNotice.created_at.desc())
        .all()
    )
    return _attach_appealability_batch(notices)


@router.get("/{notice_id}", response_model=NoticeOut)
def get_notice(
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
    return _attach_appealability(notice, db, current_user)
