"""Phase 5 agent tests: deterministic routing and advisory resolution.

Run from the ``backend`` directory:

    python -m pytest ../tests/test_phase5_routing_agent.py -v

These exercise the two Phase 5 agents directly. Both are deterministic and touch
no database, so no capability gating is needed. The tests assert the two
structural properties of the phase: routing never invents or mis-assigns a
department, and resolution never resolves, rejects or closes anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.resolution import ResolutionAgent, ResolutionInput
from app.agents.routing import RoutingAgent, RoutingInput
from app.datasets.reference_data import (
    DEPARTMENT_PROVENANCE,
    GUIDELINE_PROVENANCE,
    SERVICE_GUIDELINES,
)
from app.models.enums import ComplaintCategory

CATEGORIES = (
    ComplaintCategory.WASTE_MANAGEMENT,
    ComplaintCategory.ROAD_MAINTENANCE,
    ComplaintCategory.WATER_SUPPLY,
    ComplaintCategory.STREET_LIGHTING,
)


# ---------------------------------------------------------------------------
# Routing agent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", CATEGORIES)
def test_routing_selects_the_directory_department_for_a_known_category(
    category: ComplaintCategory,
) -> None:
    result = RoutingAgent().run(RoutingInput(category=category, ward="5"))

    assert result.recommended_department_code is not None
    assert result.recommended_department_name is not None
    assert result.recommended_department_code in {"SWM", "RIM", "WSS", "SLE"}
    assert result.matched_rules
    # Prototype data is always labelled, and a fully specified complaint is not
    # flagged for review.
    assert result.is_prototype_data is True
    assert result.provenance == DEPARTMENT_PROVENANCE
    assert result.requires_human_review is False
    assert result.confidence > 0.0


def test_routing_never_selects_an_unrelated_department() -> None:
    """The selected department must own exactly the complaint's category."""
    from app.datasets.reference_data import DEPARTMENT_DIRECTORY

    by_category = {record["category"]: record for record in DEPARTMENT_DIRECTORY}
    for category in CATEGORIES:
        result = RoutingAgent().run(RoutingInput(category=category, ward="5"))
        assert result.recommended_department_code == by_category[category]["code"]


def test_routing_unknown_category_is_not_guessed() -> None:
    result = RoutingAgent().run(RoutingInput(text="Something is wrong near the market."))

    assert result.recommended_department_code is None
    assert result.recommended_department_name is None
    assert "category" in result.missing_information
    assert result.requires_human_review is True
    assert result.review_reason
    assert result.confidence == 0.0


def test_routing_reports_a_missing_department_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agents.routing as routing

    trimmed = {
        key: value
        for key, value in routing._DEPARTMENT_BY_CATEGORY.items()
        if key is not ComplaintCategory.ROAD_MAINTENANCE
    }
    monkeypatch.setattr(routing, "_DEPARTMENT_BY_CATEGORY", trimmed)

    result = RoutingAgent().run(RoutingInput(category=ComplaintCategory.ROAD_MAINTENANCE, ward="4"))

    assert result.recommended_department_code is None
    assert "department_mapping" in result.missing_information
    assert result.requires_human_review is True


def test_routing_flags_a_missing_location() -> None:
    result = RoutingAgent().run(RoutingInput(category=ComplaintCategory.WATER_SUPPLY))

    assert result.recommended_department_code == "WSS"
    assert "ward_or_location" in result.missing_information
    assert result.requires_human_review is True
    assert result.has_coordinates is False


def test_routing_treats_a_ward_only_location_as_sufficient() -> None:
    result = RoutingAgent().run(RoutingInput(category=ComplaintCategory.STREET_LIGHTING, ward="9"))

    assert result.matched_rules
    assert "ward_or_location" not in result.missing_information
    assert result.requires_human_review is False


def test_routing_flags_an_ambiguous_location_naming_two_wards() -> None:
    result = RoutingAgent().run(
        RoutingInput(
            category=ComplaintCategory.WASTE_MANAGEMENT,
            location_text="the bin between ward 3 and ward 7 is overflowing",
        )
    )

    assert "more than one ward" in (result.review_reason or "")
    assert result.requires_human_review is True


def test_routing_flags_a_ward_that_conflicts_with_the_location_text() -> None:
    result = RoutingAgent().run(
        RoutingInput(
            category=ComplaintCategory.WASTE_MANAGEMENT,
            ward="5",
            location_text="overflowing bin in zone 7",
        )
    )

    assert result.requires_human_review is True
    assert "more than one ward" in (result.review_reason or "")


def test_routing_records_coordinates_as_a_rule_match() -> None:
    result = RoutingAgent().run(
        RoutingInput(
            category=ComplaintCategory.ROAD_MAINTENANCE,
            latitude=13.08,
            longitude=80.21,
        )
    )

    assert result.has_coordinates is True
    assert any("coordinates" in rule for rule in result.matched_rules)


# ---------------------------------------------------------------------------
# Resolution agent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", CATEGORIES)
def test_resolution_quotes_the_prototype_guideline(category: ComplaintCategory) -> None:
    result = ResolutionAgent().run(ResolutionInput(category=category))

    expected = next(guide for guide in SERVICE_GUIDELINES if guide["category"] is category)
    assert result.guideline_code == expected["code"]
    assert result.sla_hours == expected["sla_hours"]
    assert result.resolution_steps == [str(step) for step in expected["resolution_steps"]]
    assert result.escalation_matrix == expected["escalation_matrix"]


def test_resolution_sla_is_always_disclosed_as_unverified() -> None:
    result = ResolutionAgent().run(ResolutionInput(category=ComplaintCategory.WATER_SUPPLY))

    assert result.sla_hours == 24
    assert result.sla_is_verified is False
    assert result.sla_disclaimer == GUIDELINE_PROVENANCE
    assert "prototype" in result.sla_note.lower()
    assert result.is_prototype_data is True


def test_resolution_is_recommendation_only() -> None:
    result = ResolutionAgent().run(ResolutionInput(category=ComplaintCategory.ROAD_MAINTENANCE))

    assert result.recommendation_only is True
    assert result.no_auto_resolution is True
    assert result.requires_human_approval is True
    assert result.status_change_applied is False
    assert result.completed_actions == []
    assert any("automatically" in step for step in result.suggested_next_steps)


def test_resolution_without_a_category_explains_no_guideline_applies() -> None:
    result = ResolutionAgent().run(ResolutionInput(text="Something is broken."))

    assert result.guideline_code is None
    assert result.sla_hours is None
    assert result.sla_is_verified is False
    assert result.requires_human_review is True
    assert "no applicable prototype service guideline" in result.sla_note.lower()
    # Still never a completed action.
    assert result.no_auto_resolution is True
    assert result.status_change_applied is False


def test_resolution_reports_a_missing_guideline(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agents.resolution as resolution

    monkeypatch.setattr(resolution, "_GUIDELINE_BY_DEPARTMENT_CODE", {})

    result = ResolutionAgent().run(ResolutionInput(category=ComplaintCategory.STREET_LIGHTING))

    assert result.guideline_code is None
    assert result.sla_hours is None
    assert result.requires_human_review is True
    assert result.review_reason
    assert result.no_auto_resolution is True


def test_resolution_lists_required_evidence_as_missing_when_absent() -> None:
    result = ResolutionAgent().run(ResolutionInput(category=ComplaintCategory.WASTE_MANAGEMENT))

    expected = next(
        g for g in SERVICE_GUIDELINES if g["category"] is ComplaintCategory.WASTE_MANAGEMENT
    )
    assert result.required_evidence == [str(item) for item in expected["required_evidence"]]
    assert result.missing_evidence == result.required_evidence


def test_resolution_clears_missing_evidence_when_supplied() -> None:
    expected = next(
        g for g in SERVICE_GUIDELINES if g["category"] is ComplaintCategory.WASTE_MANAGEMENT
    )
    supplied = tuple(str(item) for item in expected["required_evidence"])

    result = ResolutionAgent().run(
        ResolutionInput(category=ComplaintCategory.WASTE_MANAGEMENT, available_evidence=supplied)
    )

    assert result.missing_evidence == []
    assert result.confidence >= 0.7


def test_resolution_fills_the_citizen_response_template_sla() -> None:
    result = ResolutionAgent().run(ResolutionInput(category=ComplaintCategory.WATER_SUPPLY))

    assert result.citizen_response_template is not None
    assert result.sla_hours is not None
    assert str(result.sla_hours) in result.citizen_response_template
    assert "{sla_hours}" not in result.citizen_response_template
