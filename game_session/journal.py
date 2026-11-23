"""Turn events shared by the game, its interfaces, and the saved journal."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class TurnEvent:
    """A message emitted by the session while a command runs."""

    kind: str
    text: str


@dataclass
class JournalTurn:
    """A command and its transcript, anchored to the room where the player entered it."""

    turn: int
    command: str
    place: str
    chapter: str
    request_id: Optional[str] = None
    events: list[TurnEvent] = field(default_factory=list)

    @classmethod
    def start(cls, turn, command, room, *, request_id=None):
        """Capture the origin before the command can change the session."""
        return cls(turn, command, room["name"], room["chapter"], request_id)

    def _transcript(self):
        entry = {
            "turn": self.turn, "command": self.command, "place": self.place, "chapter": self.chapter,
            "events": [{"kind": event.kind, "text": event.text} for event in self.events if event.kind != "activity"],
        }
        if self.request_id is not None:
            entry["id"] = self.request_id
        return entry

    def preview(self, activity):
        """Publish a detached snapshot while the browser worker resolves the command."""
        return dict(self._transcript(), activity=activity)

    def record(self, *, art):
        """Keep the completed turn and its illustration reference in the journal."""
        return dict(self._transcript(), art=art)
