"""Upload validation for evidence images (Phase 7).

An upload is checked three ways before it reaches the evidence agent, because
each check catches a different failure:

1. **Size** - the decoded byte length is compared against
   ``SETTINGS.max_upload_bytes`` so an oversized payload is rejected before it is
   copied around.
2. **Declared type** - when the caller states a MIME type it must be one the
   prototype accepts, and it must agree with what the bytes actually are. A file
   named ``.png`` that is really a PDF is refused.
3. **Actual content** - the leading bytes are matched against known image magic
   numbers, and OpenCV is asked to decode the image. A file that passes the
   signature check but cannot be decoded (a truncated or corrupt image) is
   refused rather than silently analysed.

Everything is detected from the bytes; the file name and the browser-supplied
``Content-Type`` are treated as untrusted hints only.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.core.config import settings

#: Magic-number prefixes for the image formats the prototype accepts. The value
#: is the canonical MIME type reported back to the caller.
_JPEG = "image/jpeg"
_PNG = "image/png"
_WEBP = "image/webp"

#: File extensions mapped to the MIME type they are expected to carry. Used only
#: to reject an obvious mismatch (``.png`` holding a JPEG); the sniffed bytes
#: remain authoritative.
EXTENSION_TYPES: dict[str, str] = {
    ".jpg": _JPEG,
    ".jpeg": _JPEG,
    ".png": _PNG,
    ".webp": _WEBP,
}


class UploadValidationError(ValueError):
    """Raised when an upload fails a size, type or content check.

    The message is safe to return to a caller: it names the rule that was
    broken, never a path, a stack trace or an internal identifier.
    """


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    """The outcome of a successful validation."""

    file_name: str
    mime_type: str
    content_type_declared: str | None
    size_bytes: int
    width: int
    height: int
    content: bytes


def sniff_mime_type(content: bytes) -> str | None:
    """Return the image MIME type implied by ``content``'s leading bytes."""
    if content.startswith(b"\xff\xd8\xff"):
        return _JPEG
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return _PNG
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return _WEBP
    return None


def _extension_of(file_name: str) -> str:
    lowered = file_name.lower()
    dot = lowered.rfind(".")
    return lowered[dot:] if dot != -1 else ""


def validate_image_upload(
    file_name: str,
    content: bytes,
    *,
    declared_type: str | None = None,
) -> ValidatedUpload:
    """Validate one uploaded image and return its trusted properties.

    Raises :class:`UploadValidationError` with a caller-safe message when the
    upload is too large, of an unsupported type, or not a decodable image.
    """
    name = (file_name or "").strip()
    if not name:
        raise UploadValidationError("a file name is required")

    if not content:
        raise UploadValidationError("the uploaded file is empty")

    max_bytes = settings.max_upload_bytes
    if len(content) > max_bytes:
        raise UploadValidationError(
            f"the uploaded file is {len(content)} bytes, which exceeds the {max_bytes}-byte limit"
        )

    allowed = set(settings.allowed_upload_mime_types)
    detected = sniff_mime_type(content)
    if detected is None:
        raise UploadValidationError(
            "the uploaded file is not a recognised image (expected JPEG, PNG or WebP)"
        )
    if detected not in allowed:
        raise UploadValidationError(f"the image type {detected} is not accepted")

    declared = (declared_type or "").strip().lower() or None
    if declared is not None:
        if declared not in allowed:
            raise UploadValidationError(f"the declared type {declared} is not accepted")
        if declared != detected:
            raise UploadValidationError(
                f"the declared type {declared} does not match the file's actual type {detected}"
            )

    extension = _extension_of(name)
    expected = EXTENSION_TYPES.get(extension)
    if expected is not None and expected != detected:
        raise UploadValidationError(
            f"the file extension {extension} does not match the file's actual type {detected}"
        )

    decoded = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None or decoded.size == 0:
        raise UploadValidationError("the uploaded file could not be decoded as an image")

    height, width = decoded.shape[:2]
    return ValidatedUpload(
        file_name=name,
        mime_type=detected,
        content_type_declared=declared,
        size_bytes=len(content),
        width=int(width),
        height=int(height),
        content=content,
    )


__all__ = [
    "EXTENSION_TYPES",
    "UploadValidationError",
    "ValidatedUpload",
    "sniff_mime_type",
    "validate_image_upload",
]
