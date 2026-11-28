"""Shrink photos before sending them to the AI.

A vision model turns every ~28x28 pixel patch of an image into a token, so a
full-size phone photo (e.g. 1126x2000) costs ~2,900 tokens and two of them
overflow an 8k context. 1024 px on the long side is plenty to see print
defects and costs about a third as much.
"""

from __future__ import annotations

import io

MAX_SIDE = 1024


def shrink(data: bytes, max_side: int = MAX_SIDE) -> bytes:
    """Return a JPEG no bigger than max_side on its long side (original if it can't be read)."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return data
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)  # phone photos are often stored sideways
        img = img.convert("RGB")
    except Exception:
        return data
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)
    return out.getvalue()


def token_estimate(data: bytes) -> int:
    try:
        from PIL import Image
        w, h = Image.open(io.BytesIO(data)).size
    except Exception:
        return 0
    return (w // 28 + 1) * (h // 28 + 1)
