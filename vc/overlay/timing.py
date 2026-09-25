"""Fixed on-camera timing of the demo cursor, clicks and typing (spec Q-74, Q-75, Q-76, Q-79, Q-55).

Pure functions and constants, standard library only. Nothing here is a knob: the values are the spec's.
"""

import math
import re

# Q-74 glide
GLIDE_MIN_MS = 180.0
GLIDE_MAX_MS = 1100.0
GLIDE_FACTOR = 0.67
TARGET_W_MIN = 16.0
TARGET_W_MAX = 64.0
TARGET_W_DEFAULT = 40.0
REST_BEFORE_PRESS_MS = 120.0

# Q-75 press spring, Q-76 ripple
PRESS_MS = 250.0
PRESS_MIN_SCALE_AT_MS = 110.0
RIPPLE_MS = 520.0
CLICK_AFTER_PRESS_MS = 120.0   # the real click follows the ripple/press start by 120 ms
RIPPLE_MAX_VISIBLE_S = 0.9     # Q-45 (divide by the speed factor inside a sped-up stretch)

# Q-79 typing, Q-55 paste
TYPE_MS_PER_CHAR = 37.0
ENTER_AFTER_LAST_MS = 200.0
PASTE_MS = 150.0

# Q-43 the cursor's resting place before the first action of a take (never the middle of the screen)
DEFAULT_START_FRACTION = (0.72, 0.78)


def target_width(w=None, h=None):
    """W of Q-74: the target's smaller side, clamped to 16-64 px; 40 px if unknown."""
    sides = [s for s in (w, h) if s is not None and s > 0]
    if not sides:
        return TARGET_W_DEFAULT
    return min(TARGET_W_MAX, max(TARGET_W_MIN, float(min(sides))))


def glide_ms(distance, w=None, h=None):
    """Q-74: T = 0.67 x (-385 + 250 x log2(D/W + 1)) ms, clamped to 180-1100 ms. 0 when there is nothing to move."""
    if distance < 0.5:
        return 0.0
    W = target_width(w, h)
    t = GLIDE_FACTOR * (-385.0 + 250.0 * math.log2(distance / W + 1.0))
    return min(GLIDE_MAX_MS, max(GLIDE_MIN_MS, t))


def sine_ease(p):
    """Q-74 profile: sine ease-in-out, velocity 0 at both ends."""
    p = min(1.0, max(0.0, p))
    return (1.0 - math.cos(math.pi * p)) / 2.0


def normalize_typed_text(text):
    """Q-79: runs of whitespace are typed as one space."""
    return re.sub(r"\s+", " ", text)


def typing_schedule(text, submit=False):
    """Q-79 plan: [(offset_ms, char)] at a flat 37 ms per character, plus ('\\n' marker) for Enter 200 ms later."""
    s = normalize_typed_text(text)
    plan = [(i * TYPE_MS_PER_CHAR, ch) for i, ch in enumerate(s)]
    if submit:
        last = plan[-1][0] if plan else 0.0
        plan.append((last + ENTER_AFTER_LAST_MS, "\n"))
    return plan


def typing_duration_ms(n_chars):
    """Q-79 Verify: first to last character = (N - 1) x 37 ms."""
    return max(0, n_chars - 1) * TYPE_MS_PER_CHAR
