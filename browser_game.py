"""Single-player loopback HTTP interface for the shared game session."""

import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from copy import deepcopy
from http.server import ThreadingHTTPServer

from characters import MAX_DESCRIPTION, CharacterError, make, presets_public
from playthrough import render
from room_companion import DEFAULT_IMAGE_MODEL, HERE, Avatar, RoomCompanion, ViewerHandler, launch_tab
from story_catalog import StoryError, bundle_bytes

logger = logging.getLogger(__name__)


class BrowserGame:
    """Serialize commands and publish completed turns separately from in-flight activity."""

    def __init__(self, session, viewer, *, avatar=None, journal=None, store=None, choose_character=False):
        """Start the command worker and publish the opening scene, continuing a saved journal if given.

        avatar, when given, is a second illustrator that keeps the player's character card current.
        """
        self.session = session
        self.viewer = viewer
        self.avatar = avatar
        self.store = store
        self.saved = {"id": store.ident, "turn": len(journal or [])} if store and journal else None
        self.condition = threading.Condition()
        self.closed = False
        self.pending = None
        self.journal = list(journal or [])
        self.receipts = {}
        self.room = session.public_state()
        self.choosing = None
        if choose_character and not (self.journal or session.game.history or session.ended):
            self.choosing = {"presets": presets_public(), "limit": MAX_DESCRIPTION}
            self.room["character"] = None
        session.show(self.viewer)
        if self.choosing is None:
            self._dress(session)
        self.worker = threading.Thread(target=self._work, name="game-turns", daemon=True)
        self.worker.start()

    def submit(self, payload):
        """Validate and accept a command once against its expected committed turn."""
        if not isinstance(payload, dict):
            return 400, {"error": "Expected a command object."}
        ident, text, after = (payload.get(field) for field in ("id", "command", "after"))
        if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", ident):
            return 400, {"error": "Invalid request ID."}
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 2000 or type(after) is not int or after < 0:
            return 400, {"error": "Enter a command of up to 2,000 characters and a valid turn number."}
        text = text.strip()
        with self.condition:
            if self.choosing is not None:
                return 409, {"error": "Choose your character first."}
            if ident in self.receipts:
                turn = self.receipts[ident]
                original = self.pending if turn > len(self.journal) else self.journal[turn - 1]
                if turn != after + 1 or original["command"] != text:
                    return 409, {"error": "This request ID belongs to a different command."}
                return 202, {"id": ident, "turn": turn}
            if self.closed or self.room["ended"]:
                return 409, {"error": "This game has ended."}
            if after != len(self.journal):
                return 409, {"error": "The room has changed. Read the latest turn and try again."}
            if self.pending:
                return 409, {"error": "Another turn is still running."}
            self.receipts[ident] = turn = after + 1
            self.pending = {"id": ident, "turn": turn, "command": text, "place": self.room["name"],
                            "chapter": self.room["chapter"], "events": [], "activity": "Starting the turn…"}
            self.condition.notify()
            return 202, {"id": ident, "turn": turn}

    def choose_character(self, payload):
        """Set the starting character once, before any commands can run."""
        with self.condition:
            if self.choosing is None or self.closed:
                return 409, {"error": "The starting character cannot be changed."}
            if not isinstance(payload, dict):
                return 400, {"error": "Expected a character object."}
            description = payload.get("description")
            try:
                character = make(payload.get("preset"), "" if description is None else description)
            except CharacterError as exc:
                return 400, {"error": str(exc)}
            self.session.game.character = character
            self.room = self.session.public_state()
            self.choosing = None
            self._dress(self.session)
            return 200, self.snapshot()

    def snapshot(self):
        """Return a consistent public view without reading the worker's mutable world."""
        with self.condition:
            return {
                "turn": len(self.journal), "room": self.room, "art": self.viewer.snapshot(),
                "latest": self.journal[-1] if self.journal else None, "pending": self.pending,
                "save": self.saved, "avatar": self.avatar.snapshot() if self.avatar and self.choosing is None else None,
                "character": self.room["character"], "choosing": self.choosing,
                "cutscene": self.viewer.cutscenes.snapshot() if self.viewer.cutscenes else None,
                "tokens": self.session.meter.snapshot(),
            }

    def _dress(self, session):
        if self.avatar:
            self.avatar.present(Avatar.of(session.game))

    def playthrough(self):
        """Render every turn so far as a shareable page."""
        with self.condition:
            record = {"journal": list(self.journal), "lessons": self.room["lessons"], "life": self.room["life"],
                      "tokens": self.session.meter.snapshot(), "character": self.room["character"]}
        if self.store:
            record["started"] = self.store.record().get("started", "")
        return render(record, self.viewer.cache_dir)

    def entries(self, before=None):
        """Return up to fifty earlier exchanges, most recent first."""
        with self.condition:
            end = len(self.journal) if before is None else min(before - 1, len(self.journal))
            start = max(0, end - 50)
            return {"entries": self.journal[start:end][::-1], "more": start > 0}

    def nomination_preview(self):
        """Read a nomination from the committed session while the worker stages any changes."""
        with self.condition:
            return bundle_bytes(self.session.story_nomination())

    def _publish_event(self, event):
        with self.condition:
            if event.kind == "activity":
                # Validator diagnostics can name rooms and puzzle solutions still ahead.
                message = event.text
                if message.startswith("[Procedural] Rejected the arc plan"):
                    message = "The next chapter is taking shape…"
                self.pending = dict(self.pending, activity=message)
            else:
                events = [*self.pending["events"], {"kind": event.kind, "text": event.text}]
                self.pending = dict(self.pending, events=events)

    def _work(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                command = self.pending["command"]
            try:
                # Stage changes so an unexpected failure cannot leave half a turn in play.
                session = deepcopy(self.session)
                session.turn(command, self._publish_event)
                room = session.public_state()
            except Exception:
                logger.exception("Could not complete game turn")
                session = None
            with self.condition:
                if self.closed:
                    return
                entry = dict(self.pending)
                del entry["activity"]
                if session is None:
                    entry["events"] = [{"kind": "error", "text": "The turn was interrupted. You can try that action again."}]
                    entry["art"] = self.viewer.journal_art()
                else:
                    self.session, self.room = session, room
                    session.show(self.viewer)
                    self._dress(session)
                    entry["art"] = session.turn_art(self.viewer)
                self.journal.append(entry)
                self.pending = None
                session, journal = self.session, list(self.journal)
                self.condition.notify_all()
            self._save(session, journal)

    def _save(self, session, journal):
        """Write the committed turn to disk; a failure leaves the game playable."""
        if not self.store:
            return
        try:
            self.store.save(session, journal)
            saved = {"id": self.store.ident, "turn": len(journal)}
        except Exception as exc:
            logger.exception("Could not save the game")
            saved = {"id": self.store.ident, "turn": len(journal), "error": f"Could not save this turn: {exc}"}
        with self.condition:
            self.saved = saved
            self.condition.notify_all()

    def close(self):
        """Detach the server immediately even if a model call is finishing, saving the last committed turn."""
        with self.condition:
            self.closed = True
            session, journal = self.session, list(self.journal)
            self.condition.notify_all()
        self.viewer.close()
        if self.avatar:
            self.avatar.close()
        if journal:
            self._save(session, journal)  # Keep tokens spent on paintings that finished after the last turn.


class PlayHandler(ViewerHandler):
    """Serve the game page and accept same-origin commands on the loopback listener."""

    def _valid_host(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def _json(self, status, obj):
        self._send(status, "application/json; charset=utf-8", json.dumps(obj).encode())

    def do_GET(self):
        """Serve a public snapshot, journal page, browser assets or the current illustration."""
        if not self._valid_host():
            self.send_error(403)
        elif self.path == "/state":
            self._json(200, self.server.game.snapshot())
        elif self.path == "/playthrough":
            self._send(200, "text/html; charset=utf-8", self.server.game.playthrough().encode("utf-8"))
        elif self.path == "/nomination":
            try:
                body = self.server.game.nomination_preview()
            except StoryError as exc:
                self._json(409, {"error": str(exc)})
            else:
                self._send(200, "application/json; charset=utf-8", body)
        elif self.path in self.server.assets:
            self._send(200, *self.server.assets[self.path])
        elif self.path.startswith("/journal"):
            match = re.fullmatch(r"/journal(?:\?before=([1-9][0-9]{0,14}))?", self.path)
            if match:
                self._json(200, self.server.game.entries(int(match[1]) if match[1] else None))
            else:
                self._json(400, {"error": "Invalid journal page."})
        else:
            super().do_GET()

    def do_POST(self):
        """Accept an illustration retry or bounded JSON from this game page."""
        if not self._valid_host() or not self._same_origin():
            self.send_error(403)
            return
        if self.path not in ("/commands", "/character"):
            super().do_POST()
            return
        command = self.path == "/commands"
        kind = "command" if command else "character"
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "Expected application/json."})
            return
        size = self.headers.get("Content-Length", "")
        if not re.fullmatch(r"[0-9]{1,5}", size) or not 0 < int(size) <= 16384:
            self._json(413, {"error": f"Invalid {kind} body size."})
            return
        try:
            self.connection.settimeout(5)
            payload = json.loads(self.rfile.read(int(size)).decode("utf-8"))
        except (ValueError, OSError, RecursionError):
            self._json(400, {"error": f"Could not read the {kind}."})
            return
        handler = self.server.game.submit if command else self.server.game.choose_character
        self._json(*handler(payload))


@contextmanager
def play_server(session, cache_dir, model, *, journal=None, store=None, choose_character=False):
    """Bind an ephemeral local port and own the game and illustration workers."""
    page = (HERE / "play.html").read_bytes()
    assets = {"/play.js": ("text/javascript; charset=utf-8", (HERE / "play.js").read_bytes()),
              "/play.css": ("text/css; charset=utf-8", (HERE / "play.css").read_bytes())}
    with ThreadingHTTPServer(("127.0.0.1", 0), PlayHandler) as server:
        server.viewer = RoomCompanion(cache_dir, model, meter=session.meter, cutscenes=True)
        server.avatar = RoomCompanion(cache_dir, model, meter=session.meter)
        server.game = BrowserGame(session, server.viewer, avatar=server.avatar, journal=journal, store=store,
                                  choose_character=choose_character)
        server.page, server.assets = page, assets
        try:
            yield server
        finally:
            server.game.close()


def play_in_browser(session, *, journal=None, store=None, choose_character=False):
    """Open the local game and keep it available across tab closes and refreshes."""
    model = os.getenv("OPENAI_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL
    try:
        with play_server(session, HERE / ".room-art", model, journal=journal, store=store,
                         choose_character=choose_character) as server:
            url = f"http://127.0.0.1:{server.server_port}/"
            if store:
                print(f"Autosaving as {store.ident}. Resume later with --continue or --load {store.ident}.")
            print(f"Play The Giant's Drink at {url}\nPress Ctrl+C here to stop the server.", flush=True)
            launch_tab(url)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        raise SystemExit(f"Could not start the browser game: {exc}") from exc
