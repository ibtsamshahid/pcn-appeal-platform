"""
Appeal generation service.

Uses an LLM (Claude) to draft an appeal letter from a classified notice
AND the driver's own stated reason for appealing. Design choice per the
report: this ALWAYS produces a draft for human review — it never
auto-submits. That human-in-the-loop step happens in the router.
"""
import os
from typing import Optional

from anthropic import Anthropic

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

APPEAL_PROMPT_TEMPLATE = """You are drafting a formal appeal letter for a UK vehicle penalty notice.

Notice details:
- Penalty type: {penalty_type}
- Vehicle registration: {vehicle_registration}
- Amount: £{amount_gbp}
- Issuing authority: {issuing_authority}
- Notice text: {raw_text}
- Driver's stated reason for appealing: {reason}

Write a polite, formal appeal letter a UK driver could send to contest this
penalty. Build the letter around the driver's stated reason above if one
was given — that is the actual grounds for the appeal. If no reason was
given, infer the most plausible grounds from the notice text (e.g. signage
ambiguity, valid permit, loading exception) and phrase the letter to
prompt the driver to confirm or add details before sending. Do not invent
specific facts not supported by the notice text or the driver's stated
reason. Keep it under 250 words."""


def _build_prompt(notice, user_reason: Optional[str]) -> str:
    """Pure prompt-building logic, kept separate from the API call so it
    can be unit-tested without hitting the network."""
    return APPEAL_PROMPT_TEMPLATE.format(
        penalty_type=notice.penalty_type,
        vehicle_registration=notice.vehicle_registration or "[not detected]",
        amount_gbp=notice.amount_gbp or "[not detected]",
        issuing_authority=notice.issuing_authority or "[not detected]",
        raw_text=notice.raw_text,
        reason=user_reason.strip() if user_reason and user_reason.strip() else "[not provided]",
    )


def generate_appeal_draft(notice, user_reason: Optional[str] = None) -> str:
    prompt = _build_prompt(notice, user_reason)

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )

    return "".join(
        block.text for block in response.content if block.type == "text"
    )
