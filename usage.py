"""Count the AI tokens one game spends, including requests made by background illustrators.

A game owns one Meter. Code that runs on the game's behalf wraps its work in ``metering(meter)``.
The OpenAI calls then report usage with ``record_text`` or ``record_image``. Cached
narration and cached illustrations make no request, so they add nothing.
"""

import threading
from contextlib import contextmanager
from contextvars import ContextVar

_current = ContextVar("usage_meter", default=None)


class Meter:
    """Thread-safe token totals that survive saves and are shared by staged copies of a game."""

    FIELDS = ("text_in", "text_out", "reasoning", "text_calls", "image_in", "image_out", "images")

    def __init__(self):
        """Start a game with nothing spent."""
        self._lock = threading.Lock()
        self.counts = dict.fromkeys(self.FIELDS, 0)

    def add(self, **amounts):
        """Add positive integer amounts to the named totals."""
        with self._lock:
            for name, amount in amounts.items():
                if name in self.counts and isinstance(amount, int) and amount > 0:
                    self.counts[name] += amount

    def snapshot(self):
        """Return the totals plus text, image and overall sums."""
        with self._lock:
            counts = dict(self.counts)
        counts["text"] = counts["text_in"] + counts["text_out"]
        counts["image"] = counts["image_in"] + counts["image_out"]
        counts["total"] = counts["text"] + counts["image"]
        return counts

    def __deepcopy__(self, memo):
        """Share one meter with staged copies: tokens a turn spends are spent even if the turn is rolled back."""
        return self

    def __getstate__(self):
        """Save the totals without the lock."""
        with self._lock:
            return {"counts": dict(self.counts)}

    def __setstate__(self, state):
        """Restore saved totals, starting any newer counters at zero."""
        self._lock = threading.Lock()
        self.counts = {**dict.fromkeys(self.FIELDS, 0), **state.get("counts", {})}


@contextmanager
def metering(meter):
    """Attribute AI requests made in this block (on this thread) to a game's meter."""
    token = _current.set(meter)
    try:
        yield
    finally:
        _current.reset(token)


def _number(mapping, key):
    value = mapping.get(key) if isinstance(mapping, dict) else None
    return value if isinstance(value, int) else 0


def record_text(usage):
    """Record a chat completion's usage block. Reasoning tokens are part of the output count."""
    meter = _current.get()
    if meter:
        meter.add(text_in=_number(usage, "prompt_tokens"), text_out=_number(usage, "completion_tokens"),
                  reasoning=_number(usage.get("completion_tokens_details") if isinstance(usage, dict) else None,
                                    "reasoning_tokens"),
                  text_calls=1)


def record_image(usage):
    """Record an image generation's usage block."""
    meter = _current.get()
    if meter:
        meter.add(image_in=_number(usage, "input_tokens"), image_out=_number(usage, "output_tokens"), images=1)
