"""On-camera actions and visual style (spec Q-40..Q-79): cursor, glide, press, ripple, typing, paste, pacing."""

from .controller import Overlay, OverlayError
from .timing import glide_ms, typing_schedule
from .pacing import HOLDS, readability_pause, remaining_pause

__all__ = ["Overlay", "OverlayError", "glide_ms", "typing_schedule", "HOLDS", "readability_pause", "remaining_pause"]
