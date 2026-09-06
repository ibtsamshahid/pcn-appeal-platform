"""
Pydantic schemas: define the shape of API request/response data.
Kept separate from SQLAlchemy models so the API contract can differ from storage.
"""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class NoticeCreate(BaseModel):
    raw_text: str


class AppealabilitySignalOut(BaseModel):
    code: str
    severity: str  # "high" | "medium" | "low" | "info"
    message: str


class AppealabilityOut(BaseModel):
    # Computed fresh on every read by app/services/appealability_checker.py
    # from the notice's own extracted fields/raw text (plus this user's
    # other notices, for duplicate detection) — never from the driver's own
    # stated reason for appealing, which is a deliberately separate signal
    # (see that module's docstring). Not persisted as a database column, so
    # it always reflects the current checker logic even for old notices.
    verdict: str  # not_applicable | deadline_likely_passed | worth_appealing | review_recommended | no_procedural_issue_detected
    signals: list[AppealabilitySignalOut]
    deadline_days_remaining: Optional[int] = None
    disclaimer: str


class NoticeOut(BaseModel):
    id: uuid.UUID
    raw_text: str
    penalty_type: str
    classification_confidence: Optional[float] = None
    vehicle_registration: Optional[str] = None
    reference_number: Optional[str] = None
    amount_gbp: Optional[float] = None
    issue_date: Optional[datetime] = None
    deadline_date: Optional[datetime] = None
    issuing_authority: Optional[str] = None
    # Helper links for submitting a REAL appeal yourself on the actual
    # provider's website — this platform's bot only ever submits to the
    # sandbox site (see submission_bot.py), so these are how a user acts
    # on a real notice safely. `issuing_authority_website` is a direct
    # link only when we're confident it's correct (currently just TfL);
    # `issuing_authority_appeal_url` is always a search-engine query for
    # the right action, since a guessed direct "appeal" sub-page URL is
    # more likely to be stale/wrong than a search is to be unhelpful.
    # `issuing_authority_verify_url` is the same kind of search link, aimed
    # instead at letting the user manually confirm THIS notice's extracted
    # details (reference number, vehicle reg where known) against the
    # authority's own real records — never an automated lookup. All three
    # are None if no issuing authority was detected at all. See
    # app/services/authority_links.py for the full reasoning.
    issuing_authority_website: Optional[str] = None
    issuing_authority_appeal_url: Optional[str] = None
    issuing_authority_verify_url: Optional[str] = None
    status: str
    appealability: AppealabilityOut

    class Config:
        from_attributes = True


class NoticeUploadOut(NoticeOut):
    # Extra field only present on the /notices/upload response — tells the
    # frontend/demo whether the text came straight out of the PDF or needed
    # OCR (useful to show off in the demo: "this was a scanned PCN").
    extraction_method: str


class BatchUploadItemError(BaseModel):
    # One entry per file in a batch upload that didn't classify — kept
    # separate from a hard failure of the whole request, since one corrupt
    # or non-PDF file in a batch of 10 shouldn't block the other 9.
    filename: str
    error: str


class BatchUploadResult(BaseModel):
    # POST /notices/upload-batch's response shape: every file is processed
    # independently, so the result is always a partition into what worked
    # and what didn't, rather than an all-or-nothing success/failure.
    succeeded: list[NoticeUploadOut]
    failed: list[BatchUploadItemError]


class AppealOut(BaseModel):
    id: uuid.UUID
    notice_id: uuid.UUID
    draft_text: str
    final_text: Optional[str] = None
    approved: str
    submission_reference: Optional[str] = None

    class Config:
        from_attributes = True


class AppealApprove(BaseModel):
    final_text: str


class AppealDraftRequest(BaseModel):
    # The user's own words on why they're appealing — this is what actually
    # makes the letter theirs rather than a generic template. Optional so
    # the endpoint still works if someone skips straight to drafting.
    reason: Optional[str] = None


class AppealDraftBatchRequest(BaseModel):
    notice_ids: list[uuid.UUID]
    # Per-notice reason, keyed by notice_id (as a string, since dict keys
    # arrive as strings over JSON) — each selected notice gets its own
    # grounds for appeal, since a permit/signage/not-the-driver reason for
    # one PCN is very unlikely to apply to another. Falls back to `reason`
    # below for any notice_id not present here, so older clients that only
    # ever sent one shared reason keep working unchanged.
    reasons: Optional[dict[str, str]] = None
    # Legacy single shared reason applied to every notice in the batch that
    # isn't covered by `reasons` above. Kept for backwards compatibility;
    # the UI now always sends `reasons` instead.
    reason: Optional[str] = None


class AppealBatchItemError(BaseModel):
    notice_id: uuid.UUID
    error: str


class AppealDraftBatchResult(BaseModel):
    succeeded: list[AppealOut]
    failed: list[AppealBatchItemError]


class AppealFinalText(BaseModel):
    appeal_id: uuid.UUID
    final_text: str


class AppealSubmitBatchRequest(BaseModel):
    appeals: list[AppealFinalText]


class AppealSubmitBatchItemError(BaseModel):
    appeal_id: uuid.UUID
    error: str


class AppealSubmitBatchResult(BaseModel):
    succeeded: list[AppealOut]
    failed: list[AppealSubmitBatchItemError]
