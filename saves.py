"""Autosave games, resume them later, and keep a readable record of every playthrough.

Each game gets a folder under saves/:
  game.json   the journal and a summary. Plain data, safe to share and to render as a playthrough.
  state.bin   a pickled snapshot of the whole world, signed with this installation's saves/.key.

Unpickling can run code, so a snapshot is only loaded when its signature matches the local key.
Saves copied from another computer can still be viewed and exported, but not resumed.
"""

import hashlib
import hmac
import json
import os
import pickle
import re
import secrets
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

FORMAT = 1


class SaveError(Exception):
    """A save that cannot be resumed, with a player-readable reason."""


def write_atomic(path, data):
    """Replace a file atomically so a crash never leaves half a save."""
    with NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(data)
    os.replace(tmp.name, path)


def _key(root, *, create):
    path = Path(root) / ".key"
    try:
        return path.read_bytes()
    except FileNotFoundError:
        if not create:
            raise SaveError("This save was made on another computer. You can view its playthrough, but not resume it.")
    Path(root).mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    with open(path, "xb") as handle:
        os.chmod(path, 0o600)
        handle.write(key)
    return key


def _sign(key, data):
    return hmac.new(key, data, hashlib.sha256).digest()


def _fill_missing(obj, fresh):
    """Give an object from an older save any attributes added to its class since."""
    for name, value in vars(fresh).items():
        if not hasattr(obj, name):
            setattr(obj, name, value)


def _upgrade(session):
    from adaptation_ai import AdaptationAI
    from behavior_analyzer import BehaviorAnalyzer
    from content_generator import ContentGenerator
    from game_session import GameSession
    from game_world import Room
    from saga import Saga
    from simulation_engine import GameState

    _fill_missing(session, GameSession())
    for part, fresh in (("game", GameState()), ("behavior", BehaviorAnalyzer()), ("adaptation", AdaptationAI()),
                        ("content", ContentGenerator()), ("saga", Saga())):
        _fill_missing(getattr(session, part), fresh)
    for room in session.rooms.values():
        _fill_missing(room, Room(room.name, ""))
    # Older adjudication could describe a completed bag while leaving the object as a tapestry.
    for items in (session.game.inventory, *(room.items for room in session.rooms.values())):
        for item in items:
            if (item.name in ("tapestry", "tapestries") and item.contents is None
                    and re.search(
                        r"\b(?:(?:into|forms?|forming|becomes?)\b[^.!?]{0,70}\b(?:bag|sack|pouch)"
                        r"|(?:bag|sack|pouch) (?:made|fashioned|stitched|knotted) from (?:the |a )?tapestr(?:y|ies))\b",
                        item.description, re.IGNORECASE
                    )):
                session.game.convert_tapestry(item)


class SaveSlot:
    """One game's folder."""

    def __init__(self, root, ident):
        """Point at saves/<ident> without touching the disk."""
        self.root = Path(root)
        self.ident = ident
        self.path = self.root / ident

    @classmethod
    def new(cls, root):
        """Name a new game after the moment it started."""
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        ident, n = stamp, 1
        while (Path(root) / ident).exists():
            n += 1
            ident = f"{stamp}_{n}"
        return cls(root, ident)

    @classmethod
    def all(cls, root):
        """Return every saved game, most recently played first."""
        slots = [cls(root, path.parent.name) for path in Path(root).glob("*/game.json")]
        return sorted(slots, key=lambda slot: (slot.path / "game.json").stat().st_mtime_ns, reverse=True)

    @classmethod
    def find(cls, root, name=None):
        """Return a named game, or the most recently played one."""
        if name is None:
            slots = [slot for slot in cls.all(root) if (slot.path / "state.bin").is_file()]
            if not slots:
                raise SaveError("There are no saved games to continue yet.")
            return slots[0]
        slot = cls(root, name)
        if Path(name).name != name or not (slot.path / "game.json").is_file():
            raise SaveError(f"There is no saved game called {name}. Run with --saves to list them.")
        return slot

    def record(self):
        """Read the shareable summary and journal."""
        try:
            return json.loads((self.path / "game.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save(self, session, journal):
        """Write the world snapshot and the readable journal for the turns played so far."""
        self.path.mkdir(parents=True, exist_ok=True)
        snapshot = pickle.dumps({"format": FORMAT, "session": session, "journal": journal},
                                protocol=pickle.HIGHEST_PROTOCOL)
        write_atomic(self.path / "state.bin", _sign(_key(self.root, create=True), snapshot) + snapshot)
        self.write_record(session.public_state(), journal, tokens=session.meter.snapshot())

    def write_record(self, room, journal, *, tokens=None):
        """Write game.json from a public room state, the journal and the game's token totals."""
        previous = self.record()
        now = datetime.now().isoformat(timespec="seconds")
        record = {
            "format": FORMAT, "id": self.ident, "started": previous.get("started", now), "updated": now,
            "turns": len(journal), "place": room["name"], "chapter": room["chapter"], "life": room["life"],
            "lessons": room["lessons"], "wearing": room.get("worn", []), "tokens": tokens, "journal": journal,
        }
        self.path.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path / "game.json", json.dumps(record, ensure_ascii=False, indent=1).encode("utf-8"))

    def load(self):
        """Return the saved session and journal, ready to continue."""
        try:
            blob = (self.path / "state.bin").read_bytes()
        except OSError:
            raise SaveError("This save has a playthrough but no world snapshot, so it can only be viewed.") from None
        signature, snapshot = blob[:32], blob[32:]
        if not hmac.compare_digest(signature, _sign(_key(self.root, create=False), snapshot)):
            raise SaveError("This save was changed or made on another computer. You can view its playthrough, "
                            "but not resume it.")
        try:
            data = pickle.loads(snapshot)  # noqa: S301 - signed by this installation's key
        except Exception as exc:
            raise SaveError("This save comes from a version of the game that can no longer read it.") from exc
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise SaveError("This save comes from a different version of the game's save format.")
        session = data["session"]
        _upgrade(session)
        session.ended = False  # Quitting stops play; loading the game picks it up again.
        return session, data["journal"]

    def summary(self):
        """Describe the save in one line for --saves."""
        record = self.record()
        state = "" if (self.path / "state.bin").is_file() else "  (view only)"
        tokens = (record.get("tokens") or {}).get("total")
        spent = f", {tokens:,} AI tokens" if isinstance(tokens, int) and tokens else ""
        return (f"{self.ident}  {record.get('turns', 0)} turns, life {record.get('life', 1)}, "
                f"{record.get('place', '?')} in {record.get('chapter', '?')}{spent}{state}")
