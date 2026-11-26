"""Structured outputs of the Phase 3 intake and classification agents.

These models are the *contract* between the agents, the API and the database.
Two properties are worth calling out:

* Every value an agent returns is either produced by deterministic Python or
  validated against a controlled vocabulary (``ComplaintCategory``,
  ``ComplaintPriority``, ``ComplaintLanguage``). Nothing the model invents can
  reach a caller unvalidated.
* ``department_code``/``guideline_code``/``sla_hours`` are **derived from the
  prototype department directory**, never from model output. The routing
  authority is data a human authored, so the agent cannot hallucinate a
  department.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ComplaintCategory,
    ComplaintLanguage,
    ComplaintPriority,
    EvidenceVerificationStatus,
)

#: Repeated wherever agent output is surfaced, so no reader mistakes an
#: illustrative analysis for a verified government decision.
ANALYSIS_PROVENANCE = (
    "Prototype agent output from CivicBridge AI. Department assignments come from "
    "a prototype directory and SLA durations are configurable placeholders - these "
    "are not verified government decisions."
)


class IntakeResult(BaseModel):
    """Normalised, structured understanding of a citizen complaint."""

    model_config = ConfigDict(extra="forbid")

    original_text: str = Field(description="The citizen's text, preserved verbatim.")
    language: ComplaintLanguage
    language_confidence: float = Field(ge=0.0, le=1.0)
    language_reason: str = Field(description="Evidence behind the language decision.")

    normalized_description: str = Field(
        description="Clear English restatement. Falls back to the original text."
    )
    problem_type: str | None = Field(
        default=None, description="Short machine-readable label, e.g. 'garbage_not_collected'."
    )
    duration_days: float | None = Field(default=None, ge=0.0)
    duration_text: str | None = None
    location_text: str | None = None
    ward: str | None = None
    landmarks: list[str] = Field(default_factory=list)

    missing_information: list[str] = Field(
        default_factory=list, description="Canonical keys, e.g. 'exact_location'."
    )
    clarification_questions: list[str] = Field(default_factory=list)
    needs_clarification: bool = False

    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    unsupported_details_dropped: list[str] = Field(
        default_factory=list,
        description="Model claims that were not traceable to the citizen's words and were discarded.",
    )

    llm_used: bool
    degraded: bool = Field(
        default=False, description="True when deterministic fallback replaced a model call."
    )
    latency_ms: float | None = None
    model_name: str | None = None


class ClassificationResult(BaseModel):
    """Category, priority and department routing for a complaint.

    ``category`` is optional on purpose: when neither the deterministic
    keyword classifier nor the model finds any category evidence, the honest
    answer is "not determined" plus ``needs_human_review=True``, not a forced
    guess. The ``complaints.category`` column is nullable for the same reason.
    """

    model_config = ConfigDict(extra="forbid")

    category: ComplaintCategory | None = Field(
        default=None, description="One of the four supported categories, or null when undetermined."
    )
    subcategory: str | None = None
    priority: ComplaintPriority = Field(
        description="The applied priority band. May exceed the deterministic band (see escalation)."
    )
    urgency_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    recommended_next_action: str

    # -- Priority audit trail ------------------------------------------------
    # The priority decision is the most consequential output of this agent, so
    # every input to it is recorded: what the evidence alone concluded, what the
    # model recommended, what was applied, and why they differ.
    deterministic_priority: ComplaintPriority = Field(
        description="The band the keyword evidence alone supports (the model's floor)."
    )
    model_priority: ComplaintPriority | None = Field(
        default=None,
        description="The band the model recommended, or null when no model answered.",
    )
    escalation_reason: str | None = Field(
        default=None,
        description="Why the applied priority differs from the deterministic one.",
    )

    # -- Routing: derived from the prototype directory, never from the model --
    department_code: str | None = None
    department_name: str | None = None
    guideline_code: str | None = None
    sla_hours: int | None = None
    routing_source: str = Field(
        default="prototype_department_directory",
        description="Where the department assignment came from.",
    )

    matched_signals: list[str] = Field(
        default_factory=list, description="Keyword evidence from the deterministic classifier."
    )
    needs_human_review: bool = False
    review_reason: str | None = None

    llm_used: bool
    degraded: bool = False
    latency_ms: float | None = None
    model_name: str | None = None


class DuplicateCandidate(BaseModel):
    """One previously submitted complaint compared against the subject.

    ``is_likely_duplicate`` is a *recommendation for review*, never an action:
    the agent has no way to merge, link or reject - ``duplicate_of_id`` on the
    complaint row is only ever written by a human officer.
    """

    model_config = ConfigDict(extra="forbid")

    # -- the candidate being compared against -------------------------------
    reference_id: str
    text: str
    category: ComplaintCategory | None = None
    ward: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    #: The stored complaint this candidate *is*, when the candidate came from the
    #: database. The detection agent never sets it - the officer-facing duplicate
    #: lookup fills it in so an officer's approve/reject decision can name the
    #: row. It stays ``None`` everywhere else, including the citizen submission
    #: response, which must not disclose another citizen's internal identifier.
    complaint_id: uuid.UUID | None = None

    # -- comparison results -------------------------------------------------
    text_similarity: float = Field(ge=0.0, le=1.0)
    geographic_distance_m: float | None = Field(default=None, ge=0.0)
    geographic_score: float | None = Field(default=None, ge=0.0, le=1.0)
    category_match: bool | None = None
    match_score: float = Field(ge=0.0, le=1.0)
    is_likely_duplicate: bool = False
    shared_terms: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    model_adjudication: str | None = Field(
        default=None, description="What the model said about a borderline pair, if it was asked."
    )


class DuplicateDetectionResult(BaseModel):
    """Ranked duplicate candidates for one complaint.

    The agent detects and explains; it never merges. ``best_match`` is the
    candidate a human should look at first, and ``linking_requires_human_approval``
    is a constant reminder of the constraint.
    """

    model_config = ConfigDict(extra="forbid")

    subject_reference_id: str | None = None
    subject_text: str
    candidates_considered: int = 0
    matches: list[DuplicateCandidate] = Field(default_factory=list)
    best_match: DuplicateCandidate | None = None
    likely_duplicate_found: bool = False

    threshold: float = Field(ge=0.0, le=1.0)
    text_only_threshold: float = Field(ge=0.0, le=1.0)
    geographic_radius_m: float = Field(ge=0.0)
    weights: dict[str, float] = Field(default_factory=dict)

    needs_human_review: bool = False
    review_reason: str | None = None
    linking_requires_human_approval: bool = True

    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    llm_used: bool = False
    degraded: bool = False
    latency_ms: float | None = None
    model_name: str | None = None


class ObservableFinding(BaseModel):
    """A fact measured directly from the image pixels."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(description="Machine-readable label, e.g. 'blur', 'low_light'.")
    statement: str = Field(description="What was measured, in words.")
    measured_value: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class AssumptionFinding(BaseModel):
    """An interpretation that goes beyond what the pixels prove."""

    model_config = ConfigDict(extra="forbid")

    statement: str
    basis: str = Field(description="The observable finding this interpretation leans on.")
    source: str = Field(default="heuristic", description="'heuristic' or 'vision_model'.")
    confidence: float = Field(ge=0.0, le=1.0)


class EvidenceAnalysisResult(BaseModel):
    """What the evidence agent could and could not establish for one image.

    Two properties are structural, not conventions:

    * ``is_proof_of_validity`` is always ``False``. An image alone never
      validates a complaint; the field exists so a caller can rely on it.
    * ``verification_status`` is capped at ``uncertain``. The agent may not
      mark evidence ``verified_relevant`` or ``not_relevant`` on its own, and
      ``manual_review_required`` is used when the image cannot be judged at all.
    """

    model_config = ConfigDict(extra="forbid")

    file_name: str | None = None
    content_digest: str | None = None
    category: ComplaintCategory | None = None

    image_analyzed: bool = False
    image_usable: bool = False
    width: int | None = None
    height: int | None = None

    blur_score: float | None = Field(default=None, ge=0.0, description="Laplacian variance.")
    brightness: float | None = Field(default=None, ge=0.0)
    contrast: float | None = Field(default=None, ge=0.0)
    dark_pixel_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    edge_density: float | None = Field(default=None, ge=0.0, le=1.0)

    relevance_score: float = Field(ge=0.0, le=1.0)
    relevance_ceiling: float = Field(
        ge=0.0, le=1.0, description="Highest relevance the agent may report; below 1.0 by design."
    )
    category_consistency: str = Field(
        description="'consistent', 'inconclusive' or 'unknown'. Never 'inconsistent' - "
        "OpenCV features cannot prove a mismatch."
    )

    verification_status: EvidenceVerificationStatus
    requires_manual_review: bool = True

    observable_findings: list[ObservableFinding] = Field(default_factory=list)
    assumptions: list[AssumptionFinding] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    unsupported_claims_dropped: list[str] = Field(default_factory=list)

    verification_notes: str
    reasoning: str
    recommendation: str
    is_proof_of_validity: bool = False

    confidence: float = Field(ge=0.0, le=1.0)
    llm_used: bool = False
    degraded: bool = False
    latency_ms: float | None = None
    model_name: str | None = None


class RoutingRecommendation(BaseModel):
    """A department-routing *proposal* for one complaint.

    The routing agent is deterministic and reads its mapping from the prototype
    department directory, so this model never carries an invented department.
    ``requires_human_review`` is set whenever the complaint cannot be tied to a
    jurisdiction, and ``is_prototype_data`` is a constant reminder that the
    directory is illustrative, not a verified government assignment.
    """

    model_config = ConfigDict(extra="forbid")

    subject_text: str | None = None
    category: ComplaintCategory | None = None

    recommended_department_code: str | None = None
    recommended_department_name: str | None = None
    routing_source: str = Field(default="prototype_department_directory")
    is_prototype_data: bool = True

    confidence: float = Field(ge=0.0, le=1.0)
    matched_rules: list[str] = Field(
        default_factory=list, description="The rule matches behind the recommendation."
    )
    missing_information: list[str] = Field(
        default_factory=list, description="Canonical keys, e.g. 'ward_or_zone'."
    )

    ward: str | None = None
    location_text: str | None = None
    has_coordinates: bool = False

    requires_human_review: bool = True
    review_reason: str | None = None

    rationale: str
    provenance: str = ANALYSIS_PROVENANCE

    llm_used: bool = False
    degraded: bool = False
    latency_ms: float | None = None
    model_name: str | None = None


class ResolutionRecommendation(BaseModel):
    """Suggested resolution steps for one complaint - never a completed action.

    Every step here is a *recommendation*. ``completed_actions`` is always empty,
    ``status_change_applied`` is always ``False`` and ``no_auto_resolution`` is
    always ``True``: the agent has no way to resolve, reject or close a
    complaint. SLA durations come from the prototype guidelines and are exposed
    with ``sla_is_verified=False`` plus an explicit disclaimer.
    """

    model_config = ConfigDict(extra="forbid")

    subject_text: str | None = None
    category: ComplaintCategory | None = None

    guideline_code: str | None = None
    guideline_title: str | None = None
    department_code: str | None = None

    sla_hours: int | None = None
    sla_is_verified: bool = False
    sla_disclaimer: str = Field(description="Explicit prototype disclaimer for the SLA value.")
    sla_note: str = Field(description="Human-readable statement of the SLA's unverified status.")

    resolution_steps: list[str] = Field(default_factory=list)
    suggested_next_steps: list[str] = Field(default_factory=list)
    completed_actions: list[str] = Field(default_factory=list)
    escalation_matrix: dict[str, Any] = Field(default_factory=dict)
    required_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    citizen_response_template: str | None = None

    is_prototype_data: bool = True
    recommendation_only: bool = True
    status_change_applied: bool = False
    requires_human_approval: bool = True
    no_auto_resolution: bool = True

    confidence: float = Field(ge=0.0, le=1.0)
    requires_human_review: bool = False
    review_reason: str | None = None

    rationale: str
    provenance: str = ANALYSIS_PROVENANCE

    llm_used: bool = False
    degraded: bool = False
    latency_ms: float | None = None
    model_name: str | None = None


class ComplaintAnalysis(BaseModel):
    """Every agent result plus pipeline-level metadata."""

    model_config = ConfigDict(extra="forbid")

    source_text: str
    intake: IntakeResult
    classification: ClassificationResult
    duplicate: DuplicateDetectionResult | None = None
    evidence: list[EvidenceAnalysisResult] = Field(default_factory=list)

    reference_id: str | None = None
    complaint_id: str | None = None
    persisted: bool = False

    llm_calls: int = 0
    total_latency_ms: float = 0.0
    steps: list[str] = Field(default_factory=list)
    generated_at: datetime
    provenance: str = ANALYSIS_PROVENANCE


#: Convenience alias for JSON payloads stored in ``agent_execution_logs``.
JsonPayload = dict[str, Any]
