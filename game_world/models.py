"""Mutable world objects and the written rules for discoveries and obstacles."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import NamedTuple, Optional

from .gear import GearError, default_capabilities
from .names import find_object, noun

DIRECTIONS = ("north", "south", "east", "west", "up", "down")
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east", "up": "down", "down": "up"}


@dataclass(frozen=True)
class HiddenPassage:
    """An exit a player can discover by using a carried tool on a landmark."""

    tool: str
    landmark: str
    direction: str
    destination: str
    reveal: str
    visible: str


@dataclass(frozen=True)
class Discovery:
    """A detail that a deliberate search or a suitable carried tool reveals."""

    tools: tuple[str, ...]
    response: str
    detail: str


class ChallengeSolution(NamedTuple):
    """A tool, its intended use, and the resulting change narrated to the player."""

    tool: str
    approach: str
    outcome: str


@dataclass
class Challenge:
    """An obstacle that bars one direction until the player finds a way past it."""

    obstacle: str
    blocks: str
    barrier: str
    solutions: tuple[ChallengeSolution, ...]
    room_after: str
    obstacle_after: str
    resolved: bool = False
    checkpoint: str = ""
    warned: bool = False

    def __post_init__(self):
        """Accept the plain triples used by earlier callers."""
        self.solutions = tuple(ChallengeSolution(*solution) for solution in self.solutions)

    def __setstate__(self, state):
        """Restore named solutions when resuming a snapshot written with plain triples."""
        self.__dict__.update(state)
        self.__post_init__()

    @property
    def warning(self):
        """Recall the consequence of crossing an obstacle the player has already tested."""
        if self.warned and not self.resolved:
            return f"Your shadow ends at the crossing. One more step {self.blocks} will cost you this life."
        return ""

    def solution_for(self, tool: str) -> Optional[ChallengeSolution]:
        """Find the planned use of this tool at the crossing."""
        return next((solution for solution in self.solutions if solution.tool == tool), None)

    def outcome(self, tool: str) -> Optional[str]:
        """Return the planned result of using a tool on the obstacle, if the plan includes it."""
        solution = self.solution_for(tool)
        return solution.outcome if solution else None


class Item:
    """A specific object the player can see, carry, or use."""

    # Class defaults also cover items loaded from saves that predate containers and generated arcs.
    puzzle_arc: int = 0
    contents: Optional[list["Item"]] = None
    attachments: Optional[list["Item"]] = None
    number: Optional[int] = None
    worn: str = ""

    def __init__(
        self, name: str, description: str, usable: bool = False, *, aliases: tuple[str, ...] = (),
        room_text: str = "", discovery: Optional[Discovery] = None,
        storage: Optional[bool] = None, attachment_points: Optional[bool] = None,
    ):
        """Describe the object, the names it answers to, and any written discovery."""
        self.name = name
        self.description = description
        self.usable = usable
        self.aliases = aliases
        self.room_text = room_text
        self.discovery = discovery
        inside, outside = default_capabilities(name)
        self.contents = [] if (inside if storage is None else storage) else None
        self.attachments = [] if (outside if attachment_points is None else attachment_points) else None

    @property
    def reference(self):
        """Return an unambiguous command reference for an item encountered in play."""
        return f"{self.name} #{self.number}" if self.number is not None else self.name

    def check_form(self, storage, attachment_points):
        """Check that changing capabilities would leave every child with a valid location."""
        if self.contents and not storage:
            raise GearError(f"Empty the {self.name} before changing its shape.")
        if self.attachments and not attachment_points:
            raise GearError(f"Unfasten the attachments from the {self.name} first.")

    def refashion(self, name, *, storage, attachment_points):
        """Apply a crafted form in place, preserving identity, children, and earlier names."""
        self.check_form(storage, attachment_points)
        if name != self.name:
            self.aliases = tuple(dict.fromkeys((*self.aliases, self.name)))
            self.name = name
        if storage and self.contents is None:
            self.contents = []
        if attachment_points and self.attachments is None:
            self.attachments = []
        if not storage:
            self.contents = None
        if not attachment_points:
            self.attachments = None

    def make_tapestry_bag(self):
        """Keep the same object while turning a tapestry into a usable container."""
        self.aliases = tuple(dict.fromkeys((*self.aliases, self.name, "tapestry")))
        self.refashion("tapestry bag", storage=True, attachment_points=True)


class Room:
    """A named location with its visible features, possible exits, and discoveries."""

    def __init__(
        self, name: str, description: str, items: Optional[list[Item]] = None, exits: Optional[dict[str, str]] = None,
        *, scenery: Iterable[Item] = (), passage: Optional[HiddenPassage] = None,
        challenge: Optional[Challenge] = None, trial=None,
    ):
        """Initialize the location before a player has visited or altered it."""
        self.name = name
        self.description = description
        self.items = items if items else []
        self.exits = exits if exits else {}
        self.scenery = list(scenery)
        self.passage = passage
        self.challenge = challenge
        self.trial = trial
        self.generated = False
        self.arc = 0
        self.discoveries: dict[str, str] = {}
        self.folded: set[str] = set()
        self.seen_details: list[str] = []
        self.departed: list[Item] = []

    def remember(self, text):
        """Keep descriptive details the player has actually been shown."""
        if text and text not in self.visible_text():
            # Recent narration is enough to ground references without growing every prompt.
            self.seen_details = [*self.seen_details[-5:], text]

    def visible_text(self):
        """Include observations and generated narration when resolving references."""
        return " ".join([self.prose(), *(obj.description for obj in (*self.scenery, *self.items)), *self.seen_details])

    def fixed_text(self):
        """Describe the room's own features, excluding loose items and opened routes."""
        return " ".join([self.description, *(d for d in self.discoveries.values() if d not in self.folded)])

    def rewrite(self, description):
        """Replace the room's own features after a change, absorbing discoveries already described."""
        self.description = description
        self.folded.update(self.discoveries.values())
        self.seen_details.clear()

    def engine_sentences(self) -> list[str]:
        """Describe the current objects, encounter, and passages that supply their own prose."""
        sentences = [item.room_text for item in self.items if item.room_text]
        if self.trial:
            sentences.append(self.trial.prose())
        if self.challenge and self.challenge.warning:
            sentences.append(self.challenge.warning)
        if self.passage_open:
            sentences.append(self.passage.visible)
        return sentences

    def prose(self):
        """Describe the room as it currently stands."""
        extras = self.engine_sentences()
        extras.extend(d for d in self.discoveries.values() if d not in self.folded)
        return " ".join([self.description, *(text for text in extras if text and text not in self.description)])

    def landmark(self, reference):
        """Find authored scenery or ground a phrase in a sentence of visible room prose."""
        fixture = find_object(reference, self.scenery)
        if fixture:
            return fixture
        if find_object(reference, self.departed):
            return None
        name = noun(reference)
        if len(name) < 2 or len(name) > 80:
            return None
        # A singular or plural reference can point to a detail with no special game rule.
        pattern = r"\b" + re.escape(name.removesuffix("s")) + r"s?\b"
        for sentence in re.split(r"(?<=[.!?])\s+", self.visible_text()):
            if re.search(pattern, sentence, re.IGNORECASE):
                return Item(name, sentence)
        return None

    @property
    def visible_exits(self):
        """Include an obstructed crossing among the ways the player can try."""
        exits = list(self.exits)
        if self.challenge and not self.challenge.resolved and self.challenge.blocks not in exits:
            exits.append(self.challenge.blocks)
        return exits

    @property
    def passage_open(self):
        """Whether the hidden route is already part of this room's exits."""
        return self.passage is not None and self.exits.get(self.passage.direction) == self.passage.destination
