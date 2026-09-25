"""Readability pauses and holds (spec Q-60, Q-61, Q-62). Deterministic: the same inputs give the same pause.

The loop calls `readability_pause(...)` before each action with what is newly on screen, subtracts the time the model
already spent deciding (`remaining_pause`), and marks every pause longer than 1.2 s on the timeline as a hold (Q-51).
"""

# Q-62 canonical hold defaults (seconds)
HOLDS = {
    "landing": 1.0,   # the first view
    "step": 1.0,      # the end of every step
    "dialog": 3.0,
    "final": 12.0,    # the last shot; shorter needs a written reason in the spec
}
HOLD_TOLERANCE_S = 0.25          # Q-51
TIMELINE_HOLD_THRESHOLD_S = 1.2  # Q-51/Q-60: pauses longer than this are timeline holds


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def reading_time_text(words):
    """Q-60/Q-62: text to read, words / 3.3 per s, clamped to 1.5-6 s."""
    return _clamp(words / 3.3, 1.5, 6.0)


def reading_time_card(chars, before_write=False):
    """Q-60: a card or dialog, max(1.2 s, chars / 16 per s), capped at 6 s; 1.6 s floor before a write."""
    t = min(6.0, max(1.2, chars / 16.0))
    return max(t, 1.6) if before_write else t


def readability_pause(*, card_chars=None, text_words=None, same_control=False, new_view=False, before_write=False):
    """Q-60, first match wins. Returns (seconds, rule).

    card_chars:   characters of a card or dialog the viewer must read before the action
    text_words:   words of other text to read
    same_control: the action repeats on the control just used
    new_view:     the URL changed or >= 3 new elements appeared (measured from when the page went quiet;
                  views that render up to 0.7 s late count from when they arrive)
    """
    if card_chars is not None:
        return reading_time_card(card_chars, before_write), "card"
    if text_words is not None:
        return reading_time_text(text_words), "text"
    if same_control:
        return 0.2, "same_control"
    if new_view:
        return 1.0, "new_view"
    return 0.3, "other"


def remaining_pause(required_s, elapsed_s):
    """Q-60: time already spent (the model deciding) counts toward the pause; it is never added on top."""
    return max(0.0, required_s - max(0.0, elapsed_s))


def is_timeline_hold(seconds):
    return seconds > TIMELINE_HOLD_THRESHOLD_S
