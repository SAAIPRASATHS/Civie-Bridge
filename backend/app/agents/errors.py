"""Typed errors raised by the CivicBridge agents.

The distinction matters: an LLM transport failure must trigger the graceful
deterministic fallback, while a programming error must not be silently
swallowed. Callers therefore catch :class:`AgentLlmError` and
:class:`AgentOutputError` deliberately rather than catching ``Exception``.
"""

from __future__ import annotations


class AgentError(RuntimeError):
    """Base class for every agent failure."""


class AgentLlmError(AgentError):
    """The model could not be reached or did not return a usable response."""


class AgentOutputError(AgentError):
    """The model responded, but its content could not be parsed or validated."""


class AgentTimeoutError(AgentError):
    """An agent step exceeded its configured timeout.

    Raised by the Phase 6 orchestration layer, never by an agent itself: a
    timeout is a workflow-level outcome that triggers a bounded retry and, if
    the retry also fails, an honest degraded result instead of a silent hang.
    """
