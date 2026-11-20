"""Single-player loopback HTTP interface for the shared game session."""

import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from copy import deepcopy
from http.server import ThreadingHTTPServer

from playthrough import render
from room_companion import DEFAULT_IMAGE_MODEL, HERE, RoomCompanion, ViewerHandler, launch_tab

logger = logging.getLogger(__name__)


class BrowserGame:
    """Serialize commands and publish completed turns separately from in-flight activity."""

    def __init__(self, session, viewer, *, journal=None, store=None):
        """Start the command worker and publish the opening scene, continuing a saved journal if given."""
        self.session = session
        self.viewer = viewer
        self.store = store
        self.saved = {"id": store.ident, "turn": len(journal or [])} if store and journal else None
        self.condition = threading.Condition()
        self.closed = False
        self.pending = None
        self.journal = list(journal or [])
        self.receipts = {}
        self.room = session.public_state()
        session.show(self.viewer)
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

    def snapshot(self):
        """Return a consistent public view without reading the worker's mutable world."""
        with self.condition:
            return {
                "turn": len(self.journal), "room": self.room, "art": self.viewer.snapshot(),
                "latest": self.journal[-1] if self.journal else None, "pending": self.pending,
                "save": self.saved,
            }

    def playthrough(self):
        """Render every turn so far as a shareable page."""
        with self.condition:
            record = {"journal": list(self.journal), "lessons": self.room["lessons"], "life": self.room["life"]}
        if self.store:
            record["started"] = self.store.record().get("started", "")
        return render(record, self.viewer.cache_dir)

    def entries(self, before=None):
        """Return up to fifty earlier exchanges, most recent first."""
        with self.condition:
            end = len(self.journal) if before is None else min(before - 1, len(self.journal))
            start = max(0, end - 50)
            return {"entries": self.journal[start:end][::-1], "more": start > 0}

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
                else:
                    self.session, self.room = session, room
                    session.show(self.viewer)
                art = self.viewer.snapshot()
                entry["art"] = {"key": art["key"], "kind": art["kind"]} if art.get("key") else None
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
        """Detach the server immediately even if a model call is finishing."""
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.viewer.close()


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
        """Accept an illustration retry or a bounded JSON command from this game page."""
        if not self._valid_host() or not self._same_origin():
            self.send_error(403)
            return
        if self.path != "/commands":
            super().do_POST()
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "Expected application/json."})
            return
        size = self.headers.get("Content-Length", "")
        if not re.fullmatch(r"[0-9]{1,5}", size) or not 0 < int(size) <= 16384:
            self._json(413, {"error": "Invalid command body size."})
            return
        try:
            self.connection.settimeout(5)
            payload = json.loads(self.rfile.read(int(size)).decode("utf-8"))
        except (ValueError, OSError, RecursionError):
            self._json(400, {"error": "Could not read the command."})
            return
        self._json(*self.server.game.submit(payload))


@contextmanager
def play_server(session, cache_dir, model, *, journal=None, store=None):
    """Bind an ephemeral local port and own the game and illustration workers."""
    page = (HERE / "play.html").read_bytes()
    assets = {"/play.js": ("text/javascript; charset=utf-8", (HERE / "play.js").read_bytes()),
              "/play.css": ("text/css; charset=utf-8", (HERE / "play.css").read_bytes())}
    with ThreadingHTTPServer(("127.0.0.1", 0), PlayHandler) as server:
        server.viewer = RoomCompanion(cache_dir, model)
        server.game = BrowserGame(session, server.viewer, journal=journal, store=store)
        server.page, server.assets = page, assets
        try:
            yield server
        finally:
            server.game.close()


def play_in_browser(session, *, journal=None, store=None):
    """Open the local game and keep it available across tab closes and refreshes."""
    model = os.getenv("OPENAI_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL
    try:
        with play_server(session, HERE / ".room-art", model, journal=journal, store=store) as server:
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
