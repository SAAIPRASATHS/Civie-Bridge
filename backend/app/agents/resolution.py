"""Agent 6 - Resolution Recommendation (Phase 5).

Turns a complaint's category into **suggested next steps**, quoting the
prototype service guideline for that department. It is deterministic and
advisory only:

* **It cannot resolve, reject or close anything.** ``completed_actions`` is
  always empty, ``status_change_applied`` is always ``False`` and
  ``no_auto_resolution`` is always ``True``. A consequential status change needs
  the explicit officer decision in :mod:`app.api.routing`.
* **No invented service rule.** The steps, escalation ladder and required
  evidence are quoted verbatim from
  :data:`app.datasets.reference_data.SERVICE_GUIDELINES`, and the SLA duration
  is always surfaced as unverified prototype data
  (``sla_is_verified=False``) with an explicit disclaimer.
* **Say so when no guideline applies.** An undetermined category, or a category
  whose department has no guideline, produces no SLA and an explanation rather
  than a fabricated playbook.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.datasets.reference_data import (
    DEPARTMENT_DIRECTORY,
    GUIDELINE_PROVENANCE,
    guideline_by_department_code,
)
from app.models.enums import ComplaintCategory
from app.schemas.agent import ResolutionRecommendation

#: Indexed once at import time; both are read as module globals so tests can
#: substitute a directory or guideline set with a gap in it.
_DEPARTMENT_BY_CATEGORY: dict[ComplaintCategory, dict] = {
    department["category"]: department for department in DEPARTMENT_DIRECTORY
}
_GUIDELINE_BY_DEPARTMENT_CODE: dict[str, dict] = guideline_by_department_code()

#: Human-readable label for a recommendation that carries a checklist step.
_NO_AUTO_STEP = (
    "An authorised officer must approve the routing and the resolution plan; "
    "no step here is executed automatically."
)

_MISSING_GUIDELINE_NOTE = (
    "No applicable prototype service guideline was found for this complaint, so no "
    "SLA estimate is provided."
)


@dataclass(frozen=True, slots=True)
class ResolutionInput:
    """The inputs the resolution playbook may read about one complaint."""

    text: str | None = None
    category: ComplaintCategory | None = None
    ward: str | None = None
    available_evidence: tuple[str, ...] = ()


class ResolutionAgent:
    """Deterministic, advisory resolution recommendations over prototype guidelines."""

    def run(self, payload: ResolutionInput) -> ResolutionRecommendation:
        department = _DEPARTMENT_BY_CATEGORY.get(payload.category) if payload.category else None
        department_code = department["code"] if department else None
        guideline = _GUIDELINE_BY_DEPARTMENT_CODE.get(department_code) if department_code else None
        available = {
            item.strip().lower() for item in payload.available_evidence if item and item.strip()
        }

        if guideline is None:
            return self._without_guideline(payload)

        sla_hours = int(guideline["sla_hours"])
        missing_evidence = [
            str(item)
            for item in guideline["required_evidence"]
            if str(item).lower() not in available
        ]
        next_steps: list[str] = []
        if missing_evidence:
            next_steps.append(
                "Collect the outstanding evidence before dispatch: "
                + ", ".join(missing_evidence)
                + "."
            )
        next_steps.append(_NO_AUTO_STEP)

        template = guideline["citizen_response_template"]
        return ResolutionRecommendation(
            subject_text=payload.text,
            category=payload.category,
            guideline_code=guideline["code"],
            guideline_title=guideline["title"],
            department_code=department_code,
            sla_hours=sla_hours,
            sla_is_verified=False,
            sla_disclaimer=GUIDELINE_PROVENANCE,
            sla_note=(
                f"The {sla_hours}-hour target is a prototype estimate (is_verified=False); "
                "it is not an official government service commitment."
            ),
            resolution_steps=[str(step) for step in guideline["resolution_steps"]],
            suggested_next_steps=next_steps,
            completed_actions=[],
            escalation_matrix=dict(guideline["escalation_matrix"]),
            required_evidence=[str(item) for item in guideline["required_evidence"]],
            missing_evidence=missing_evidence,
            citizen_response_template=(
                template.replace("{sla_hours}", str(sla_hours)) if template else None
            ),
            is_prototype_data=True,
            recommendation_only=True,
            status_change_applied=False,
            requires_human_approval=True,
            no_auto_resolution=True,
            confidence=round(0.70 + (0.10 if not missing_evidence else 0.0), 3),
            requires_human_review=False,
            review_reason=None,
            rationale=(
                f"Quoted prototype guideline '{guideline['code']}' "
                f"({guideline['title']}) for the '{payload.category.value}' category. "
                "Every step is a recommendation, and the SLA is a prototype estimate."
            ),
            provenance=GUIDELINE_PROVENANCE,
        )

    @staticmethod
    def _without_guideline(payload: ResolutionInput) -> ResolutionRecommendation:
        reason = (
            f"no prototype service guideline exists for the '{payload.category.value}' category"
            if payload.category is not None
            else "the complaint category is undetermined"
        )
        return ResolutionRecommendation(
            subject_text=payload.text,
            category=payload.category,
            guideline_code=None,
            guideline_title=None,
            department_code=None,
            sla_hours=None,
            sla_is_verified=False,
            sla_disclaimer=GUIDELINE_PROVENANCE,
            sla_note=_MISSING_GUIDELINE_NOTE,
            resolution_steps=[],
            suggested_next_steps=[
                "Route the complaint to a human officer for manual triage: " + reason + "."
            ],
            completed_actions=[],
            escalation_matrix={},
            required_evidence=[],
            missing_evidence=[],
            citizen_response_template=None,
            is_prototype_data=True,
            recommendation_only=True,
            status_change_applied=False,
            requires_human_approval=True,
            no_auto_resolution=True,
            confidence=0.0,
            requires_human_review=True,
            review_reason=reason,
            rationale=(
                "No resolution playbook was recommended: "
                + reason
                + ". A human officer must triage the complaint."
            ),
            provenance=GUIDELINE_PROVENANCE,
        )


__all__ = ["ResolutionAgent", "ResolutionInput"]
