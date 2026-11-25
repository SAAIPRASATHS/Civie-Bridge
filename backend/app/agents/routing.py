"""Agent 5 - Department Routing (Phase 5).

Routes a complaint to a municipal department using **deterministic rules over
the prototype reference data** - never a model. Three rules shape the design:

* **No invented department.** The agent may only select a department from
  :data:`app.datasets.reference_data.DEPARTMENT_DIRECTORY`, and only on an exact
  category match. It never routes a complaint to an unrelated department, and an
  unmapped category produces *no* recommendation rather than a guess.
* **Location handled conservatively.** A missing location is reported as missing
  information and flagged for review instead of guessed, and a location that
  names more than one ward/zone is reported as ambiguous.
* **The agent recommends; it never assigns.** ``complaints.current_department_id``
  is written only by the explicit officer decision endpoint in
  :mod:`app.api.routing`. ``requires_human_review`` is set whenever the complaint
  could not be tied cleanly to a jurisdiction.

Every mapping read here is prototype reference data, so the result carries
:data:`DEPARTMENT_PROVENANCE` and ``is_prototype_data=True``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.datasets.reference_data import DEPARTMENT_DIRECTORY, DEPARTMENT_PROVENANCE
from app.models.enums import ComplaintCategory
from app.schemas.agent import RoutingRecommendation

#: category -> prototype department record. Indexed once at import time; read as
#: a module global so tests can substitute a directory with a gap in it.
_DEPARTMENT_BY_CATEGORY: dict[ComplaintCategory, dict] = {
    department["category"]: department for department in DEPARTMENT_DIRECTORY
}

#: Matches "ward 5", "zone-3", "block no. 12" so a location naming two different
#: wards can be detected as ambiguous.
_WARD_REFERENCE = re.compile(
    r"\b(?:ward|zone|block|division)\s*[-#]?\s*(?:no\.?\s*)?([0-9]{1,3}|[a-z]{1,12})\b",
    re.IGNORECASE,
)

#: Rule-match confidence constants. The score expresses how completely the rule
#: inputs are specified, not how likely the citizen is telling the truth.
_BASE_CONFIDENCE = 0.55
_WARD_BONUS = 0.15
_COORDINATE_BONUS = 0.10
_AMBIGUITY_PENALTY = 0.30
_MAX_CONFIDENCE = 0.90


@dataclass(frozen=True, slots=True)
class RoutingInput:
    """Everything the deterministic routing rules may read about one complaint."""

    text: str | None = None
    category: ComplaintCategory | None = None
    ward: str | None = None
    location_text: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


def _clean(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def referenced_wards(text: str | None) -> set[str]:
    """Ward/zone identifiers named in a location string (lower-cased)."""
    if not text:
        return set()
    return {match.group(1).lower() for match in _WARD_REFERENCE.finditer(text)}


class RoutingAgent:
    """Deterministic, explainable department routing over prototype data."""

    def run(self, payload: RoutingInput) -> RoutingRecommendation:
        ward = _clean(payload.ward)
        location_text = _clean(payload.location_text)

        matched_rules: list[str] = []
        missing_information: list[str] = []
        review_reasons: list[str] = []

        # -- category -> department (the only routing rule) -----------------
        department = _DEPARTMENT_BY_CATEGORY.get(payload.category) if payload.category else None
        if payload.category is None:
            missing_information.append("category")
            review_reasons.append(
                "the complaint category is undetermined, so no department is recommended"
            )
        elif department is None:
            missing_information.append("department_mapping")
            review_reasons.append(
                f"no prototype department is mapped to the '{payload.category.value}' category"
            )
        else:
            matched_rules.append(
                f"category '{payload.category.value}' is owned by prototype department "
                f"'{department['code']}'"
            )

        # -- location -------------------------------------------------------
        ward_refs = referenced_wards(location_text or payload.text)
        ward_conflicts_with_text = (
            ward is not None and bool(ward_refs) and ward.lower() not in ward_refs
        )
        ambiguous = len(ward_refs) > 1 or ward_conflicts_with_text

        if not (ward or location_text or payload.has_coordinates):
            missing_information.append("ward_or_location")
            review_reasons.append(
                "no ward, location description or coordinates were supplied, so the "
                "complaint cannot be tied to a jurisdiction"
            )
        elif ward is None:
            missing_information.append("ward_or_zone")

        if payload.has_coordinates:
            matched_rules.append("coordinates were supplied for the reported location")
        if ward:
            matched_rules.append(f"ward '{ward}' was supplied for the reported location")
        if ambiguous:
            review_reasons.append(
                "the location references more than one ward or zone, so the routing must be "
                "confirmed by a human"
            )

        # -- confidence and human review ------------------------------------
        if department is None:
            confidence = 0.0
        else:
            confidence = _BASE_CONFIDENCE
            if ward:
                confidence += _WARD_BONUS
            if payload.has_coordinates:
                confidence += _COORDINATE_BONUS
            if ambiguous:
                confidence -= _AMBIGUITY_PENALTY
            confidence = round(max(0.0, min(_MAX_CONFIDENCE, confidence)), 3)

        requires_human_review = bool(review_reasons)

        # -- explanation ----------------------------------------------------
        if department is not None:
            rationale = (
                f"Recommended '{department['name']}' ({department['code']}) because the "
                f"complaint category '{payload.category.value}' is owned by that prototype "
                "department."
            )
        else:
            rationale = (
                "No department could be recommended from the prototype directory, so the "
                "complaint is left unassigned."
            )
        if review_reasons:
            rationale += " Flagged for human review: " + "; ".join(review_reasons) + "."

        return RoutingRecommendation(
            subject_text=payload.text,
            category=payload.category,
            recommended_department_code=department["code"] if department else None,
            recommended_department_name=department["name"] if department else None,
            routing_source="prototype_department_directory",
            is_prototype_data=True,
            confidence=confidence,
            matched_rules=matched_rules,
            missing_information=missing_information,
            ward=ward,
            location_text=location_text,
            has_coordinates=payload.has_coordinates,
            requires_human_review=requires_human_review,
            review_reason="; ".join(review_reasons) if review_reasons else None,
            rationale=rationale,
            provenance=DEPARTMENT_PROVENANCE,
        )


__all__ = ["RoutingAgent", "RoutingInput", "referenced_wards"]
