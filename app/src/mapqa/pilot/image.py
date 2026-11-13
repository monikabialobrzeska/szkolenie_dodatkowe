"""Image preparation before sending (ADR-005 §4.2a). The provider rejects small images ("Invalid image dimensions"
for 155x192 px; 311x308 px was accepted), so every model gets the same scaled copy of the crop. Only the size
changes: no crop, no annotation, nothing else added (AC-29)."""

from __future__ import annotations

import io

from PIL import Image

MIN_SIDE = 320  # above the 308 px proven in the S3-1 capture
MAX_SIDE = 1600  # longest side of the full frames in signs/photos
JPEG_QUALITY = 92  # same as the crops in signs/
SETTINGS = {"min_side": MIN_SIDE, "max_side": MAX_SIDE, "resample": "LANCZOS", "jpeg_quality": JPEG_QUALITY,
            "pillow": Image.__version__}


def target_size(w: int, h: int) -> tuple[int, int]:
    scale = 1.0
    if min(w, h) < MIN_SIDE:
        scale = MIN_SIDE / min(w, h)
    if max(w, h) * scale > MAX_SIDE:
        scale = MAX_SIDE / max(w, h)  # the cap wins for extreme aspect ratios
    return round(w * scale), round(h * scale)


def prepare(raw: bytes) -> bytes:
    """Return JPEG bytes within [MIN_SIDE, MAX_SIDE]; an image already in range is returned unchanged."""
    with Image.open(io.BytesIO(raw)) as img:
        size = target_size(*img.size)
        if size == img.size:
            return raw
        out = io.BytesIO()
        img.convert("RGB").resize(size, Image.Resampling.LANCZOS).save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def size_of(raw: bytes) -> str:
    with Image.open(io.BytesIO(raw)) as img:
        return f"{img.size[0]}x{img.size[1]}"
