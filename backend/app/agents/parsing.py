"""Recovering a usable payload from raw model output.

Even with Ollama's JSON mode and a temperature of 0, a local 8B model
occasionally wraps its answer in a markdown fence, adds a sentence before the
braces, or answers a list field with an object. The first two are recovered by
:func:`extract_json_object`, the third by :func:`coerce_string_list`. Only a
genuinely unusable response raises :class:`AgentOutputError`, which each agent
converts into its documented fallback.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.agents.errors import AgentOutputError

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _strip_fences(text: str) -> str:
    match = _FENCE_RE.search(text)
    return match.group(1) if match else text


def _outermost_object(text: str) -> str | None:
    """Return the substring from the first ``{`` to its matching ``}``.

    A brace counter (string-literal aware) is used instead of ``rfind('}')`` so
    trailing commentary after the object cannot corrupt the slice.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def coerce_string_list(value: Any) -> list[str] | None:
    """Normalise the shapes a local model uses for a "list of strings" field.

    JSON mode constrains the syntax, not the schema: ``llama3.1`` was observed
    answering ``missing_information`` with an object
    (``{"exact_location": "...", "description_detail": null}``) where an array
    was requested. Rejecting that costs a second ~1-minute inference and, if the
    model repeats itself, the entire model answer - so the shape is repaired
    here, before validation.

    * ``list``/``tuple`` -> the string items, stripped.
    * ``str`` -> a single-item list.
    * ``dict`` -> the values when every value is a non-empty string (an
      index-keyed answer such as ``{"1": "Where exactly?"}``), otherwise the
      keys (``{"exact_location": true}`` names its items in the keys).
    * anything else -> ``None``, meaning "not repairable": the caller keeps the
      documented repair-and-fallback behaviour.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        values = [item.strip() for item in value.values() if isinstance(item, str) and item.strip()]
        if value and len(values) == len(value):
            return values
        return [str(key).strip() for key in value if str(key).strip()]
    return None


def extract_json_object(raw: str) -> dict[str, Any]:
    """Parse the first JSON object found in ``raw``.

    Raises :class:`AgentOutputError` when no parseable object exists or when the
    payload is not a JSON object.
    """
    if not raw or not raw.strip():
        raise AgentOutputError("model returned an empty response")

    candidate = _outermost_object(_strip_fences(raw))
    if candidate is None:
        preview = raw.strip().replace("\n", " ")[:200]
        raise AgentOutputError(f"no JSON object found in model output: {preview!r}")

    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise AgentOutputError(f"model output is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise AgentOutputError(f"expected a JSON object, received {type(payload).__name__}")
    return payload
