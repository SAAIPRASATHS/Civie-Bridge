"""CivicBridge AI agents (Phases 3-5).

Six agents are implemented here:

* :class:`~app.agents.intake.IntakeAgent` - understands an English, Tamil or
  Tamil-transliterated complaint and returns validated structured JSON.
* :class:`~app.agents.classification.ClassificationAgent` - assigns one of the
  four supported categories, a priority and a department, where the department
  is read from the prototype directory rather than produced by the model.
* :class:`~app.agents.duplicate.DuplicateDetectionAgent` - ranks *potential*
  duplicates by offline TF-IDF similarity plus Haversine proximity. It never
  merges or links; a human officer approves or rejects each proposal.
* :class:`~app.agents.evidence.EvidenceAnalysisAgent` - measures an attached
  image with OpenCV and reports what it can and cannot establish. An image is
  never treated as proof of validity.
* :class:`~app.agents.routing.RoutingAgent` - deterministic, explainable
  department routing over the prototype directory. It recommends; it never
  assigns.
* :class:`~app.agents.resolution.ResolutionAgent` - advisory resolution steps
  quoted from the prototype guidelines. It never resolves, rejects or closes.

They compose into :class:`~app.agents.pipeline.ComplaintPipeline`, which runs
the intake, classification, duplicate and evidence agents in order, records one
``agent_execution_logs`` row per step and optionally persists the result against
the existing ``complaints`` table. The routing and resolution agents are
Phase 5 additions consumed directly by :mod:`app.api.routing`.

The agents are LangGraph-compatible: each exposes a pure ``run`` method over
Pydantic models, so a node can call it directly without any adapter.
"""

from app.agents.classification import ClassificationAgent
from app.agents.duplicate import ComplaintSnapshot, DuplicateDetectionAgent
from app.agents.errors import AgentError, AgentLlmError, AgentOutputError
from app.agents.evidence import EvidenceAnalysisAgent, EvidenceImage
from app.agents.intake import IntakeAgent
from app.agents.language import LanguageDetection, detect_language
from app.agents.llm_client import (
    LlmCallResult,
    LlmClient,
    OllamaLlmClient,
    clear_llm_cache,
    get_default_llm_client,
)
from app.agents.pipeline import ComplaintPipeline, analyze_complaint
from app.agents.resolution import ResolutionAgent, ResolutionInput
from app.agents.routing import RoutingAgent, RoutingInput

__all__ = [
    "AgentError",
    "AgentLlmError",
    "AgentOutputError",
    "ClassificationAgent",
    "ComplaintPipeline",
    "ComplaintSnapshot",
    "DuplicateDetectionAgent",
    "EvidenceAnalysisAgent",
    "EvidenceImage",
    "IntakeAgent",
    "LanguageDetection",
    "LlmCallResult",
    "LlmClient",
    "OllamaLlmClient",
    "ResolutionAgent",
    "ResolutionInput",
    "RoutingAgent",
    "RoutingInput",
    "analyze_complaint",
    "clear_llm_cache",
    "detect_language",
    "get_default_llm_client",
]
