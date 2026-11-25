"""Agent 4 - Evidence Analysis.

Looks at the photo a citizen attached and reports what the pixels actually
show - and, just as importantly, what they do not.

Three rules shape the design:

* **Observable facts and assumptions are separate objects.** A measurement
  ("Laplacian variance 12.4, so the image is soft") is an
  :class:`~app.schemas.agent.ObservableFinding`. A reading of the scene ("this
  could be an unlit street") is an
  :class:`~app.schemas.agent.AssumptionFinding`, is always flagged as an
  interpretation, and never carries more than 0.30 confidence. A reviewer can
  tell at a glance which is which.
* **An image alone is never proof.** ``is_proof_of_validity`` is always
  ``False`` and ``verification_status`` is capped at ``uncertain``: the agent
  cannot mark evidence ``verified_relevant`` or ``not_relevant``, because
  neither conclusion can be drawn from features without a field visit.
  ``manual_review_required`` is used when the image cannot be judged at all.
* **The pipeline is OpenCV, and it degrades honestly.** Blur, exposure,
  contrast, dark-pixel ratio, edge density, saturation and dominant hue are
  computed locally with OpenCV - no downloads, no weights. If a vision-capable
  model is *later* installed and configured via ``OLLAMA_VISION_MODEL``, its
  description is recorded as a model opinion (source ``vision_model``), never
  as evidence.

Blurry, tiny, corrupt and unsupported files are all handled without raising:
the result says what happened and routes the file to a human.
"""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.agents.errors import AgentLlmError
from app.agents.parsing import extract_json_object
from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.logging import get_logger
from app.models.enums import ComplaintCategory, EvidenceVerificationStatus
from app.schemas.agent import AssumptionFinding, EvidenceAnalysisResult, ObservableFinding

logger = get_logger(__name__)

#: Shorter than this on either side and the image is too small to judge.
MIN_USABLE_DIMENSION = 200
#: Laplacian variance below this reads as a soft or out-of-focus image.
BLUR_VARIANCE_MIN = 60.0
#: Mean grey value at or below this reads as a low-light scene.
DARK_BRIGHTNESS_MAX = 70.0
#: Mean grey value at or above this reads as an over-exposed image.
BRIGHT_BRIGHTNESS_MIN = 185.0
#: Canny edge fraction at or above this reads as a visually busy scene.
BUSY_EDGE_DENSITY = 0.18
#: Mean HSV saturation at or below this reads as a grey, washed-out surface.
LOW_SATURATION_MAX = 60.0
#: Dark-pixel fraction that counts as "a lot of dark area".
DARK_PIXEL_RATIO_HIGH = 0.15
#: Bright-pixel fraction that suggests a reflective or wet surface.
BRIGHT_PIXEL_RATIO_HIGH = 0.08

#: Relevance the agent reports for a usable, analysed image before any cue.
BASE_RELEVANCE = 0.20
#: Added per category cue, and the number of cues that can add anything.
CUE_RELEVANCE_STEP = 0.15
MAX_CUES_FOR_SCORE = 3
#: Hard ceiling on the relevance the agent may report on its own.
MAX_AUTO_RELEVANCE = 0.60

#: Confidence attached to a vision-model description, because it is an opinion.
VISION_OPINION_CONFIDENCE = 0.25

#: Hue-bin names for the dominant-colour finding (OpenCV hue is 0-179).
_HUE_BIN_NAMES = ("red", "yellow-orange", "green", "cyan-blue", "blue-purple", "magenta")
_HUE_BIN_EDGES = (0, 30, 60, 90, 120, 150, 180)

#: Language that would turn a model description into an unsupported claim.
_CERTAINTY_PHRASES = (
    "proof",
    "proves",
    "proven",
    "definitely",
    "definitively",
    "confirmed that",
    "is valid",
    "valid complaint",
)

VISION_SYSTEM_PROMPT = """\
You describe municipal complaint evidence photos for a grievance system.
Say only what is visible: the objects, surfaces, damage or conditions you can see.
Do not judge whether the complaint is true, valid or a duplicate - a human officer does that.
Return ONE JSON object with exactly these keys: description, confidence.
"description" is at most two short sentences. "confidence" is 0.0 to 1.0.
"""


@dataclass(frozen=True, slots=True)
class EvidenceImage:
    """One uploaded file, held either as bytes or as a path.

    Bytes are what the API receives (base64 in, decoded here); a path is what
    the CLI and the tests use.
    """

    file_name: str
    data: bytes | None = None
    path: Path | None = None

    def __post_init__(self) -> None:
        if self.data is None and self.path is None:
            raise ValueError("EvidenceImage needs either 'data' or 'path'")

    @classmethod
    def from_path(cls, path: str | Path, *, file_name: str | None = None) -> EvidenceImage:
        resolved = Path(path)
        return cls(file_name=file_name or resolved.name, path=resolved)

    @classmethod
    def from_bytes(cls, file_name: str, data: bytes) -> EvidenceImage:
        return cls(file_name=file_name, data=bytes(data))

    def buffer(self) -> bytes | None:
        """The file bytes, or ``None`` when the path does not exist."""
        if self.data is not None:
            return self.data
        if self.path is not None and self.path.is_file():
            return self.path.read_bytes()
        return None

    @property
    def digest(self) -> str | None:
        buffer = self.buffer()
        return hashlib.sha256(buffer).hexdigest() if buffer is not None else None


@dataclass(frozen=True, slots=True)
class ImageFeatures:
    """Measurements taken directly from the pixels."""

    width: int
    height: int
    blur_score: float
    brightness: float
    contrast: float
    dark_pixel_ratio: float
    bright_pixel_ratio: float
    edge_density: float
    saturation_mean: float
    dominant_hue: str | None


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def decode_image(buffer: bytes | None) -> np.ndarray | None:
    """Decode encoded image bytes; ``None`` when the file is corrupt or unsupported."""
    if not buffer:
        return None
    array = np.frombuffer(buffer, dtype=np.uint8)
    if array.size == 0:
        return None
    return cv2.imdecode(array, cv2.IMREAD_COLOR)


def _dominant_hue(hsv: np.ndarray) -> str | None:
    """Name the most common saturated hue, or ``None`` for a near-greyscale image."""
    hue = hsv[:, :, 0]
    saturated = hsv[:, :, 1] > 60
    if saturated.mean() < 0.05:
        return None
    histogram = np.bincount(hue[saturated].ravel(), minlength=180)
    peak = int(np.argmax(histogram))
    for index in range(len(_HUE_BIN_NAMES)):
        if _HUE_BIN_EDGES[index] <= peak < _HUE_BIN_EDGES[index + 1]:
            return _HUE_BIN_NAMES[index]
    return None


def extract_features(bgr: np.ndarray) -> ImageFeatures:
    """Compute the OpenCV feature set for one colour image."""
    grey = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    height, width = grey.shape[:2]
    edges = cv2.Canny(grey, 100, 200)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    return ImageFeatures(
        width=int(width),
        height=int(height),
        blur_score=round(float(cv2.Laplacian(grey, cv2.CV_64F).var()), 2),
        brightness=round(float(grey.mean()), 2),
        contrast=round(float(grey.std()), 2),
        dark_pixel_ratio=round(float((grey < 50).mean()), 4),
        bright_pixel_ratio=round(float((grey > 205).mean()), 4),
        edge_density=round(float((edges > 0).mean()), 4),
        saturation_mean=round(float(hsv[:, :, 1].mean()), 2),
        dominant_hue=_dominant_hue(hsv),
    )


def image_is_usable(features: ImageFeatures) -> bool:
    """Whether the image is sharp and large enough to read anything from."""
    return (
        min(features.width, features.height) >= MIN_USABLE_DIMENSION
        and features.blur_score >= BLUR_VARIANCE_MIN
    )


# ---------------------------------------------------------------------------
# Observations and interpretations
# ---------------------------------------------------------------------------


def _observable_findings(features: ImageFeatures) -> list[ObservableFinding]:
    findings = [
        ObservableFinding(
            kind="resolution",
            statement=f"image is {features.width} x {features.height} px",
            measured_value=f"{features.width}x{features.height}",
            confidence=1.0,
        )
    ]

    if features.blur_score < BLUR_VARIANCE_MIN:
        statement = (
            f"low sharpness (Laplacian variance {features.blur_score:.1f}, below "
            f"{BLUR_VARIANCE_MIN:.0f}) - the image looks soft or out of focus"
        )
    else:
        statement = f"adequate sharpness (Laplacian variance {features.blur_score:.1f})"
    findings.append(
        ObservableFinding(
            kind="blur",
            statement=statement,
            measured_value=f"{features.blur_score:.1f}",
            confidence=0.9,
        )
    )

    if features.brightness <= DARK_BRIGHTNESS_MAX:
        lighting = f"low-light scene (mean brightness {features.brightness:.0f}/255)"
        kind = "low_light"
    elif features.brightness >= BRIGHT_BRIGHTNESS_MIN:
        lighting = f"very bright, possibly over-exposed scene ({features.brightness:.0f}/255)"
        kind = "overexposed"
    else:
        lighting = f"moderate lighting (mean brightness {features.brightness:.0f}/255)"
        kind = "lighting"
    findings.append(
        ObservableFinding(
            kind=kind,
            statement=lighting,
            measured_value=f"{features.brightness:.0f}",
            confidence=0.9,
        )
    )

    findings.append(
        ObservableFinding(
            kind="contrast",
            statement=(
                f"contrast (grey standard deviation {features.contrast:.1f}); "
                f"{features.dark_pixel_ratio * 100:.0f}% of pixels are dark"
            ),
            measured_value=f"{features.contrast:.1f}",
            confidence=0.9,
        )
    )

    texture = (
        "a visually busy frame"
        if features.edge_density >= BUSY_EDGE_DENSITY
        else "a mostly smooth frame"
    )
    findings.append(
        ObservableFinding(
            kind="texture",
            statement=f"edge density {features.edge_density:.2f} - {texture}",
            measured_value=f"{features.edge_density:.2f}",
            confidence=0.8,
        )
    )

    colour = (
        f"dominant saturated hue: {features.dominant_hue}"
        if features.dominant_hue
        else "near-greyscale image (no dominant saturated hue)"
    )
    findings.append(
        ObservableFinding(
            kind="colour",
            statement=f"{colour}; mean saturation {features.saturation_mean:.0f}/255",
            measured_value=features.dominant_hue or "greyscale",
            confidence=0.8,
        )
    )
    return findings


def _category_assumptions(
    features: ImageFeatures, category: ComplaintCategory | None
) -> list[AssumptionFinding]:
    """Heuristic readings of the scene, tied to the reported category.

    Every entry is an interpretation and is capped at low confidence: these
    cues say "consistent with", never "this is".
    """
    if category is None:
        return []

    assumptions: list[AssumptionFinding] = []
    base = f"low-light measurement (brightness {features.brightness:.0f}/255)"

    if category is ComplaintCategory.STREET_LIGHTING and features.brightness <= DARK_BRIGHTNESS_MAX:
        assumptions.append(
            AssumptionFinding(
                statement=(
                    "the dark scene is consistent with an unlit or poorly lit street, "
                    "which is the kind of condition a street-lighting complaint describes"
                ),
                basis=base,
                confidence=0.3,
            )
        )

    if category is ComplaintCategory.WATER_SUPPLY and (
        features.bright_pixel_ratio >= BRIGHT_PIXEL_RATIO_HIGH
        or (features.saturation_mean <= LOW_SATURATION_MAX and features.contrast < 45)
    ):
        assumptions.append(
            AssumptionFinding(
                statement=(
                    "bright, low-saturation, low-contrast surfaces could be standing or "
                    "flowing water or a wet surface"
                ),
                basis=(
                    f"bright-pixel ratio {features.bright_pixel_ratio:.2f}, mean saturation "
                    f"{features.saturation_mean:.0f}/255, contrast {features.contrast:.1f}"
                ),
                confidence=0.2,
            )
        )

    if (
        category is ComplaintCategory.WASTE_MANAGEMENT
        and features.edge_density >= BUSY_EDGE_DENSITY
        and features.dark_pixel_ratio >= DARK_PIXEL_RATIO_HIGH
    ):
        assumptions.append(
            AssumptionFinding(
                statement=(
                    "a visually busy, partly dark frame could be accumulated waste or debris"
                ),
                basis=(
                    f"edge density {features.edge_density:.2f}, dark-pixel ratio "
                    f"{features.dark_pixel_ratio:.2f}"
                ),
                confidence=0.2,
            )
        )

    if category is ComplaintCategory.ROAD_MAINTENANCE and (
        features.saturation_mean <= LOW_SATURATION_MAX and features.brightness > DARK_BRIGHTNESS_MAX
    ):
        assumptions.append(
            AssumptionFinding(
                statement=(
                    "a predominantly grey, low-saturation surface could be road, asphalt or "
                    "a damaged paved surface"
                ),
                basis=(
                    f"mean saturation {features.saturation_mean:.0f}/255, brightness "
                    f"{features.brightness:.0f}/255"
                ),
                confidence=0.2,
            )
        )

    return assumptions


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class EvidenceAnalysisAgent:
    """Analyse one attached image with OpenCV, and optionally a vision model."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        use_llm: bool | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._use_llm = self._settings.agent_use_llm if use_llm is None else use_llm

    # -- public API --------------------------------------------------------

    def run(
        self,
        image: EvidenceImage | str | Path,
        *,
        category: ComplaintCategory | None = None,
        complaint_text: str | None = None,
    ) -> EvidenceAnalysisResult:
        """Analyse ``image`` in the light of the complaint it belongs to."""
        source = image if isinstance(image, EvidenceImage) else EvidenceImage.from_path(image)
        started = time.perf_counter()

        buffer = source.buffer()
        digest = hashlib.sha256(buffer).hexdigest() if buffer is not None else None
        decoded = decode_image(buffer)

        limitations = [
            "computed with OpenCV image features only; no object or text recognition was performed",
            "an image on its own cannot establish that a complaint is valid; officer or field "
            "verification is still required",
            "the prototype does not check EXIF capture time or geolocation, so the image "
            "location and date are unverified",
        ]
        unsupported: list[str] = []
        model_name: str | None = None
        vision_assumption: AssumptionFinding | None = None

        if decoded is None:
            notes = (
                "the file could not be decoded as an image, so no evidence could be read; "
                "a human must review the original file"
            )
            limitations.append("the uploaded file was unreadable, corrupt or an unsupported format")
            return EvidenceAnalysisResult(
                file_name=source.file_name,
                content_digest=digest,
                category=category,
                image_analyzed=False,
                image_usable=False,
                relevance_score=0.0,
                relevance_ceiling=MAX_AUTO_RELEVANCE,
                category_consistency="unknown",
                verification_status=EvidenceVerificationStatus.MANUAL_REVIEW_REQUIRED,
                requires_manual_review=True,
                observable_findings=[],
                assumptions=[],
                limitations=limitations,
                unsupported_claims_dropped=[],
                verification_notes=notes,
                reasoning="the evidence agent could not read this file at all",
                recommendation=(
                    "Route the original file to an officer for manual review; do not treat it "
                    "as supporting or contradicting the complaint."
                ),
                is_proof_of_validity=False,
                confidence=0.85,
                llm_used=False,
                degraded=True,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                model_name=None,
            )

        features = extract_features(decoded)
        usable = image_is_usable(features)
        observable = _observable_findings(features)
        assumptions = _category_assumptions(features, category)

        if self._use_llm and buffer is not None:
            self._apply_vision(
                buffer=buffer,
                features=features,
                category=category,
                assumptions=assumptions,
                unsupported=unsupported,
                limitations=limitations,
            )
            vision_assumption = next(
                (item for item in assumptions if item.source == "vision_model"), None
            )
            model_name = self._settings.ollama_vision_model if vision_assumption else None

        if usable:
            assumptions.append(
                AssumptionFinding(
                    statement=(
                        "the image is assumed to show the reported problem, but nothing in the "
                        "pixels confirms which complaint it belongs to"
                    ),
                    basis="no provenance check was possible without a vision model or EXIF data",
                    confidence=0.15,
                )
            )

        if category is None:
            limitations.append(
                "the complaint category was not supplied, so category-specific cues could not "
                "be assessed"
            )
        elif complaint_text and vision_assumption is None:
            limitations.append(
                "the complaint text was not compared against the image content (that needs a "
                "vision model, which is not configured)"
            )
        if not usable:
            limitations.append(
                "image quality was too low to extract reliable evidence "
                f"(blur variance {features.blur_score:.1f}, "
                f"{features.width}x{features.height} px)"
            )

        cue_count = len([item for item in assumptions if item.source == "heuristic"])
        if usable:
            relevance = min(
                MAX_AUTO_RELEVANCE,
                BASE_RELEVANCE + CUE_RELEVANCE_STEP * min(cue_count, MAX_CUES_FOR_SCORE),
            )
        else:
            relevance = 0.0
        consistency = (
            "unknown" if category is None else ("consistent" if cue_count else "inconclusive")
        )

        # The verdict is capped at ``uncertain`` by design - see the module docstring.
        status = EvidenceVerificationStatus.UNCERTAIN

        return EvidenceAnalysisResult(
            file_name=source.file_name,
            content_digest=digest,
            category=category,
            image_analyzed=True,
            image_usable=usable,
            width=features.width,
            height=features.height,
            blur_score=features.blur_score,
            brightness=features.brightness,
            contrast=features.contrast,
            dark_pixel_ratio=features.dark_pixel_ratio,
            edge_density=features.edge_density,
            relevance_score=round(relevance, 3),
            relevance_ceiling=MAX_AUTO_RELEVANCE,
            category_consistency=consistency,
            verification_status=status,
            requires_manual_review=True,
            observable_findings=observable,
            assumptions=assumptions,
            limitations=limitations,
            unsupported_claims_dropped=unsupported,
            verification_notes=self._notes(usable, cue_count, consistency),
            reasoning=self._reasoning(observable, assumptions, consistency),
            recommendation=self._recommendation(usable),
            is_proof_of_validity=False,
            confidence=self._confidence(usable, vision_assumption),
            llm_used=vision_assumption is not None,
            degraded=not usable,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            model_name=model_name,
        )

    # -- vision model (inert until one is installed) ------------------------

    def _apply_vision(
        self,
        *,
        buffer: bytes,
        features: ImageFeatures,
        category: ComplaintCategory | None,
        assumptions: list[AssumptionFinding],
        unsupported: list[str],
        limitations: list[str],
    ) -> None:
        """Record a vision model's description as an opinion, never as evidence."""
        model = self._settings.ollama_vision_model
        if not model:
            return
        try:
            description, resolved = self._call_vision_model(buffer, model, category, features)
        except AgentLlmError as exc:
            logger.warning("Vision model unavailable (%s); OpenCV features only", exc)
            limitations.append(
                f"the configured vision model could not be reached ({type(exc).__name__})"
            )
            return

        if description is None:
            limitations.append(
                f"the configured vision model {resolved!r} is not available or does not "
                "support images, so no scene description was produced"
            )
            return

        lowered = description.lower()
        if any(phrase in lowered for phrase in _CERTAINTY_PHRASES):
            # A model may describe; it may not declare the complaint valid.
            unsupported.append(f"vision_model:{description[:160]}")
            return

        assumptions.append(
            AssumptionFinding(
                statement=description,
                basis=f"vision model {resolved!r} description (a model opinion, not verified)",
                source="vision_model",
                confidence=VISION_OPINION_CONFIDENCE,
            )
        )

    def _call_vision_model(
        self,
        buffer: bytes,
        model: str,
        category: ComplaintCategory | None,
        features: ImageFeatures,
    ) -> tuple[str | None, str]:
        """Invoke a configured vision model, or return ``(None, model)``."""
        from app.services.ollama_service import (
            check_ollama_service,
            extract_message_text,
            find_model,
            get_chat_model,
        )

        status = check_ollama_service(self._settings)
        if not status.reachable:
            return None, model
        entry = find_model(status, model)
        if entry is None or not entry.supports_vision:
            return None, model

        from langchain_core.messages import HumanMessage, SystemMessage

        prompt = (
            f"Describe the visible evidence in this photo for a {category.value.replace('_', ' ')} "
            "complaint. The image measures "
            f"{features.width}x{features.height} px with mean brightness {features.brightness:.0f}/255."
        )
        encoded = base64.b64encode(buffer).decode("ascii")
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
            ]
        )

        chat_model, _ = get_chat_model(
            model=entry.name, response_format=None, settings=self._settings
        )
        try:
            response = chat_model.invoke([SystemMessage(content=VISION_SYSTEM_PROMPT), message])
        except Exception as exc:  # normalised into a typed error
            raise AgentLlmError(
                f"vision model {entry.name!r} failed: {type(exc).__name__}: {exc}"
            ) from exc

        text = extract_message_text(response.content).strip()
        if not text:
            return None, entry.name
        try:
            payload = extract_json_object(text)
        except Exception:
            return text[:400], entry.name
        description = str(payload.get("description") or "").strip()
        return (description[:400] or None), entry.name

    # -- summaries ---------------------------------------------------------

    @staticmethod
    def _notes(usable: bool, cue_count: int, consistency: str) -> str:
        if not usable:
            return (
                "Image quality is too low to judge relevance. It was analysed, so the "
                "measurements are recorded, but the evidence stays uncertain and needs a human."
            )
        return (
            f"Image analysed; {cue_count} category cue(s) found ({consistency}). An image alone "
            "does not prove a complaint, so the evidence stays uncertain pending human review."
        )

    @staticmethod
    def _reasoning(
        observable: list[ObservableFinding],
        assumptions: list[AssumptionFinding],
        consistency: str,
    ) -> str:
        parts = [f"observable: {item.statement}" for item in observable]
        parts += [
            f"assumption ({item.source}, {item.confidence:.2f}): {item.statement}"
            for item in assumptions
        ]
        parts.append(f"category consistency: {consistency}")
        return " | ".join(parts)

    @staticmethod
    def _recommendation(usable: bool) -> str:
        if not usable:
            return (
                "Ask the citizen for a sharper, closer photo, and keep the complaint pending "
                "rather than rejected: poor photo quality is not evidence against it."
            )
        return (
            "Attach the image and its measurements to the officer review queue; confirm "
            "relevance by inspection before treating the evidence as supporting the complaint."
        )

    @staticmethod
    def _confidence(usable: bool, vision_assumption: AssumptionFinding | None) -> float:
        # This is confidence in the *quality assessment*, not in the complaint.
        base = 0.55 if usable else 0.75
        return round(min(0.8, base + (0.10 if vision_assumption else 0.0)), 3)


__all__ = [
    "BASE_RELEVANCE",
    "BLUR_VARIANCE_MIN",
    "MAX_AUTO_RELEVANCE",
    "MIN_USABLE_DIMENSION",
    "VISION_SYSTEM_PROMPT",
    "EvidenceAnalysisAgent",
    "EvidenceImage",
    "ImageFeatures",
    "decode_image",
    "extract_features",
    "image_is_usable",
]
