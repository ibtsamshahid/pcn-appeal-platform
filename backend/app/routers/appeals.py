from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import uuid

from app.database import get_db
from app.models.notice import PenaltyNotice, Appeal, NoticeStatus
from app.models.user import User
from app.schemas.notice import (
    AppealApprove,
    AppealBatchItemError,
    AppealDraftBatchRequest,
    AppealDraftBatchResult,
    AppealDraftRequest,
    AppealOut,
    AppealSubmitBatchItemError,
    AppealSubmitBatchRequest,
    AppealSubmitBatchResult,
)
from app.services.auth import get_current_user
from app.services.appeal_generator import generate_appeal_draft
from app.services.submission_bot import BOT_BATCH_DEMO_HOLD_SECONDS, BotSubmissionError, submit_appeal_via_bot

router = APIRouter(prefix="/appeals", tags=["appeals"])


def _get_owned_notice(notice_id: uuid.UUID, db: Session, current_user: User) -> PenaltyNotice:
    notice = (
        db.query(PenaltyNotice)
        .filter(PenaltyNotice.id == notice_id, PenaltyNotice.owner_id == current_user.id)
        .first()
    )
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    return notice


@router.post("/{notice_id}/draft", response_model=AppealOut)
def draft_appeal(
    notice_id: uuid.UUID,
    payload: Optional[AppealDraftRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generates an AI draft appeal for a given notice, optionally guided by
    the user's own stated reason for appealing (payload.reason). Does NOT
    submit it — that only happens after human approval via /approve."""
    notice = _get_owned_notice(notice_id, db, current_user)

    reason = payload.reason if payload else None
    draft_text = generate_appeal_draft(notice, user_reason=reason)

    appeal = Appeal(notice_id=notice.id, draft_text=draft_text, approved="pending")
    notice.status = NoticeStatus.APPEAL_DRAFTED
    db.add(appeal)
    db.commit()
    db.refresh(appeal)
    return appeal


@router.post("/draft-batch", response_model=AppealDraftBatchResult)
def draft_appeal_batch(
    payload: AppealDraftBatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Drafts an AI appeal for several notices in one request — same
    independent-per-item pattern as POST /notices/upload-batch and
    POST /payments/pay-batch, so one bad notice_id doesn't block the rest.
    Added at the supervisor's request to mirror the payment module's
    batch UX: tick several PCNs, act on all of them together. Each draft
    still needs individual review before it's actually sent — see
    /submit-batch below for that human-in-the-loop step, unchanged from
    the single-notice flow.

    Each notice gets its OWN reason (payload.reasons, keyed by notice_id)
    where the caller provided one — a permit/signage/not-the-driver reason
    given for one PCN should never be reused verbatim for a different,
    unrelated PCN in the same batch. payload.reason is only a fallback for
    any notice_id not present in payload.reasons, kept for older clients."""
    succeeded: list[AppealOut] = []
    failed: list[AppealBatchItemError] = []
    reasons_by_id = payload.reasons or {}

    for notice_id in payload.notice_ids:
        try:
            notice = _get_owned_notice(notice_id, db, current_user)
            notice_reason = reasons_by_id.get(str(notice_id), payload.reason)
            draft_text = generate_appeal_draft(notice, user_reason=notice_reason)
            appeal = Appeal(notice_id=notice.id, draft_text=draft_text, approved="pending")
            notice.status = NoticeStatus.APPEAL_DRAFTED
            db.add(appeal)
            db.commit()
            db.refresh(appeal)
            succeeded.append(appeal)
        except HTTPException as exc:
            db.rollback()
            failed.append(AppealBatchItemError(notice_id=notice_id, error=str(exc.detail)))
        except Exception as exc:  # noqa: BLE001 - one bad notice must never sink the whole batch
            db.rollback()
            failed.append(AppealBatchItemError(notice_id=notice_id, error=f"Unexpected error: {exc}"))

    return AppealDraftBatchResult(succeeded=succeeded, failed=failed)


@router.post("/{appeal_id}/approve", response_model=AppealOut)
def approve_appeal(
    appeal_id: uuid.UUID,
    payload: AppealApprove,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Human-in-the-loop step: user reviews/edits the draft, then approves it.
    This is the point at which the appeal becomes ready to actually submit."""
    appeal = (
        db.query(Appeal)
        .join(PenaltyNotice, Appeal.notice_id == PenaltyNotice.id)
        .filter(Appeal.id == appeal_id, PenaltyNotice.owner_id == current_user.id)
        .first()
    )
    if not appeal:
        raise HTTPException(status_code=404, detail="Appeal not found")

    appeal.final_text = payload.final_text
    appeal.approved = "approved"
    db.commit()
    db.refresh(appeal)
    return appeal


@router.post("/{appeal_id}/submit", response_model=AppealOut)
def submit_appeal(
    appeal_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Runs the sandbox bot (Playwright) to actually submit an APPROVED
    appeal onto the mock PCN-provider site. Refuses if the appeal hasn't
    been approved yet — this is the human-in-the-loop gate from the
    report, enforced here rather than just in the UI."""
    appeal = (
        db.query(Appeal)
        .join(PenaltyNotice, Appeal.notice_id == PenaltyNotice.id)
        .filter(Appeal.id == appeal_id, PenaltyNotice.owner_id == current_user.id)
        .first()
    )
    if not appeal:
        raise HTTPException(status_code=404, detail="Appeal not found")
    if appeal.approved != "approved":
        raise HTTPException(status_code=400, detail="Appeal must be approved before it can be submitted")

    notice = db.query(PenaltyNotice).filter(PenaltyNotice.id == appeal.notice_id).first()

    try:
        result = submit_appeal_via_bot(notice, appeal)
    except BotSubmissionError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    appeal.submission_reference = result["reference"]
    notice.status = NoticeStatus.APPEAL_SUBMITTED
    db.commit()
    db.refresh(appeal)
    return appeal


@router.post("/submit-batch", response_model=AppealSubmitBatchResult)
def submit_appeal_batch(
    payload: AppealSubmitBatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approves (with the caller-supplied final_text — i.e. reviewed/
    edited in the UI first, same human-in-the-loop gate as the
    single-notice flow) and submits several appeals in one request. The
    batch equivalent of calling /approve then /submit for each one
    individually, added at the supervisor's request to mirror the payment
    module's "select several, act together" UX.

    Runs the bot SEQUENTIALLY, not in parallel — several real, resource-
    heavy Playwright browser instances running at once isn't something to
    risk on an unknown demo machine — using BOT_BATCH_DEMO_HOLD_SECONDS
    (a few seconds, vs. the single-submit endpoint's default ~60s) so a
    batch of several notices doesn't take several minutes end to end.
    One failed submission doesn't block the rest of the batch."""
    succeeded: list[AppealOut] = []
    failed: list[AppealSubmitBatchItemError] = []

    for item in payload.appeals:
        appeal = (
            db.query(Appeal)
            .join(PenaltyNotice, Appeal.notice_id == PenaltyNotice.id)
            .filter(Appeal.id == item.appeal_id, PenaltyNotice.owner_id == current_user.id)
            .first()
        )
        if not appeal:
            failed.append(AppealSubmitBatchItemError(appeal_id=item.appeal_id, error="Appeal not found"))
            continue

        try:
            appeal.final_text = item.final_text
            appeal.approved = "approved"
            db.commit()

            notice = db.query(PenaltyNotice).filter(PenaltyNotice.id == appeal.notice_id).first()
            result = submit_appeal_via_bot(notice, appeal, demo_hold_seconds=BOT_BATCH_DEMO_HOLD_SECONDS)

            appeal.submission_reference = result["reference"]
            notice.status = NoticeStatus.APPEAL_SUBMITTED
            db.commit()
            db.refresh(appeal)
            succeeded.append(appeal)
        except BotSubmissionError as exc:
            db.rollback()
            failed.append(AppealSubmitBatchItemError(appeal_id=item.appeal_id, error=str(exc)))
        except Exception as exc:  # noqa: BLE001 - one bad appeal must never sink the whole batch
            db.rollback()
            failed.append(AppealSubmitBatchItemError(appeal_id=item.appeal_id, error=f"Unexpected error: {exc}"))

    return AppealSubmitBatchResult(succeeded=succeeded, failed=failed)
