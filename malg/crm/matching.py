"""Pure conservative matching decisions for existing Twenty records."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def choose_company_match(
    domain_matches: Iterable[Mapping[str, Any]],
    linkedin_matches: Iterable[Mapping[str, Any]],
) -> tuple[str | None, str | None]:
    """Return one reusable company or a review reason; never merge ambiguity."""
    domains = {str(row["id"]) for row in domain_matches if row.get("id")}
    linkedins = {str(row["id"]) for row in linkedin_matches if row.get("id")}
    if len(domains) > 1 or len(linkedins) > 1:
        return None, "multiple_identity_matches"
    if domains and linkedins and domains != linkedins:
        return None, "identity_sources_conflict"
    return next(iter(domains or linkedins), None), None


def choose_person_match(
    linkedin_matches: Iterable[Mapping[str, Any]],
    email_matches: Iterable[Mapping[str, Any]],
    name_matches: Iterable[Mapping[str, Any]],
) -> tuple[str | None, str | None]:
    """Prefer strong observed identities, rejecting ambiguity or contradictory sources."""
    groups = [
        {str(row["id"]) for row in matches if row.get("id")}
        for matches in (linkedin_matches, email_matches, name_matches)
    ]
    if len(groups[0]) > 1 or len(groups[1]) > 1:
        return None, "multiple_identity_matches"
    if groups[0] and groups[1] and groups[0] != groups[1]:
        return None, "identity_sources_conflict"
    strong = groups[0] or groups[1]
    if strong:
        return next(iter(strong)), None
    if len(groups[2]) > 1:
        return None, "ambiguous_name"
    return next(iter(groups[2]), None), None
