"""
Lookup of external links for a recognised issuing authority — used to
help a user go actually submit or manage their appeal on the REAL
provider's own website. This platform never submits a real appeal itself
(see submission_bot.py's docstring for the reasoning: real portals block
automated browsers, and a failed automated submission on an actual
deadline-bound legal matter is a genuine risk to the user, not just a
technical inconvenience). This module is the safe alternative: point the
user at the right place to do it themselves.

Design note: the exact "appeal" sub-page on a parking operator's own
website changes fairly often (portal rebuilds, rebrands, URL
restructuring), and this was written without live web-search access
available to verify current URLs. Rather than risk sending someone
chasing a stale or simply wrong link for something as consequential as a
real penalty appeal, every entry's `appeal_url` routes through a
search-engine query built from the authority's own name instead of a
guessed direct URL — that's always current and always lands somewhere
relevant, which a hardcoded guess can't promise. The one exception is
Transport for London's root domain (tfl.gov.uk), which is stable,
well-established public knowledge rather than a guess — even that entry
still uses a search link for the *appeal* action specifically, since the
exact appeal sub-page path is exactly the kind of detail that goes stale.

Bottom line for anyone relying on this in the report or the demo: treat
these as a convenient starting point, not a guarantee. Always confirm
you're on the operator's genuine site (check the domain, look for HTTPS)
before entering any personal or payment details — the same caution
you'd want for any link received about a penalty notice.
"""
from typing import Optional
from urllib.parse import quote_plus

_SEARCH_ENGINE_URL = "https://www.google.com/search?q={query}"


def _search_link(query: str) -> str:
    return _SEARCH_ENGINE_URL.format(query=quote_plus(query))


# canonical issuing_authority name (as produced by
# classifier._extract_issuing_authority) -> known-stable root website.
# Deliberately small — only entries we're confident are correct without
# having been able to verify them live. Everything else falls back to a
# search link in get_authority_links() below.
_KNOWN_WEBSITES = {
    "Transport for London": "https://tfl.gov.uk/",
}


def get_authority_links(
    issuing_authority: Optional[str],
    reference_number: Optional[str] = None,
    vehicle_registration: Optional[str] = None,
) -> dict:
    """Returns {"website": str|None, "appeal_url": str|None,
    "verify_url": str|None} for a given canonical issuing-authority name.
    `website` is a direct link only for the small set of authorities in
    _KNOWN_WEBSITES above; otherwise None. `appeal_url` is always a
    search-engine query for "<authority> appeal a parking charge notice" —
    see module docstring for why a search link is used instead of a
    guessed direct URL.

    `verify_url` exists for the same reason and with the same caveat, but
    for a different job: letting the user manually double-check what THIS
    platform extracted (which, per the notice-detail page, may be OCR'd
    from a photo and can occasionally misread a field) against the
    authority's own real records for this specific notice. Deliberately
    NOT an automated lookup — this platform never submits a user's PCN
    details into a third-party site's form or attempts to get past
    whatever bot-detection that site has (see submission_bot.py's
    docstring for the same reasoning applied to actually submitting an
    appeal). Including the reference number and vehicle registration in
    the search query (when known) just gets the user's own click closer to
    the right page than a bare authority-name search would.

    Returns {"website": None, "appeal_url": None, "verify_url": None} if
    no authority was detected at all (nothing to link to).
    """
    if not issuing_authority:
        return {"website": None, "appeal_url": None, "verify_url": None}

    verify_terms = [issuing_authority, "penalty charge notice check pay"]
    if reference_number:
        verify_terms.append(reference_number)
    if vehicle_registration:
        verify_terms.append(vehicle_registration)

    return {
        "website": _KNOWN_WEBSITES.get(issuing_authority),
        "appeal_url": _search_link(f"{issuing_authority} appeal a parking charge notice"),
        "verify_url": _search_link(" ".join(verify_terms)),
    }
