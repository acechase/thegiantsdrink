"""State kept for an arc and the types shared by its builders and validators."""

from dataclasses import dataclass
from typing import Optional

from game_world import Item, Room


class PlanError(ValueError):
    """A generated arc plan that cannot be played as written."""


@dataclass
class Arc:
    """A planned arc and the player's progress through it."""

    number: int
    title: str
    premise: str
    start: str
    horizon: str
    rooms: list[str]
    challenge_room: str
    entered: bool = False
    plan: Optional[dict] = None

    def banner(self):
        """Introduce the arc the first time the player arrives."""
        return f"── Arc {self.number}: {self.title} ──\n{self.premise}"


@dataclass(frozen=True)
class PlacedItem:
    """A loose item and the room that originally holds it in an arc plan."""

    room: Room
    item: Item
