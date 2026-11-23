"""Queue visible illustrations and cache completed artwork on disk."""

import hashlib
import json
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile

import room_companion
from usage import metering

from .scenes import Scene


class RoomCompanion:
    """Render illustrations in the background and reuse completed paintings."""

    def __init__(self, cache_dir, model, *, meter=None, cutscenes=False, keep_pending=False):
        """Prepare a disk cache and start an idle image worker that bills paintings to a game's meter."""
        self.cache_dir = Path(cache_dir)
        self.model = model
        self.meter = meter
        self._condition = threading.Condition()
        self._closed = False
        self._scene = None
        self._description = ""
        self._key = self._active = None
        self._keep_pending = keep_pending
        self._pending = {}
        self._status = "waiting"
        self._error = ""
        self._worker = threading.Thread(target=self._work, name="room-illustrator", daemon=True)
        self._worker.start()
        self.cutscenes = RoomCompanion(cache_dir, model, meter=meter, keep_pending=True) if cutscenes else None

    def show(self, room, *, description=None):
        """Publish a room immediately, coalescing duplicate and superseded requests."""
        self.present(Scene.from_room(room), description)

    def show_portrait(self, portrait):
        """Replace the room with the player's portrait until the next scene is shown."""
        self.present(portrait, portrait.description)

    def present(self, scene, description=""):
        """Present an illustration and queue it if it has yet to be painted."""
        with self._condition:
            if self._closed:
                return
            self._description = description or scene.description
            if scene == self._scene:
                return
            self._scene = scene
            payload = scene.request(self.model)
            self._key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            self._error = ""
            if not self._keep_pending:
                self._pending.clear()
            try:
                cached = self._file(self._key).is_file()
            except OSError:
                cached = False
            if cached:
                self._status = "cached"
            elif self._key == self._active:
                self._status = "generating"
            else:
                self._status = "queued"
                self._pending[self._key] = payload
            self._condition.notify_all()

    def journal_art(self, *, cutscene=False):
        """Return the key of the illustration to associate with this turn."""
        art = (self.cutscenes if cutscene and self.cutscenes else self).snapshot()
        return {"key": art["key"], "kind": art["kind"]} if art.get("key") else None

    def snapshot(self):
        """Return public display data without credentials or local paths."""
        with self._condition:
            scene = self._scene
            if scene is None:
                return {"status": "waiting"}
            ready = self._status in ("ready", "cached")
            return {
                "kind": scene.kind, "key": self._key, "name": scene.name, "description": self._description,
                "items": scene.items, "scenery": scene.scenery, "exits": scene.exits, "model": self.model,
                "status": self._status, "error": self._error, "image": f"/art/{self._key}.jpg" if ready else None,
            }

    def image_unavailable(self, key):
        """Offer a retry when the active illustration disappeared from disk."""
        with self._condition:
            if self._key == key and self._status in ("ready", "cached"):
                self._status = "error"
                self._error = "The cached illustration could not be read. Try again."

    def retry(self, key):
        """Retry a failed scene only if it is still on screen."""
        with self._condition:
            if self._key == key and self._status == "error" and not self._closed:
                self._error = ""
                self._status = "queued"
                self._pending[key] = self._scene.request(self.model)
                self._condition.notify_all()
                return True
            return False

    def close(self):
        """Discard queued work and let an in-flight request finish in the background."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        if self.cutscenes:
            self.cutscenes.close()

    def _file(self, key):
        return self.cache_dir / f"{key}.jpg"

    def _save(self, key, data):
        with NamedTemporaryFile(dir=self.cache_dir, delete=False) as tmp:
            path = Path(tmp.name)
            try:
                tmp.write(data)
            except OSError:
                tmp.close()
                path.unlink(missing_ok=True)
                raise
        try:
            path.replace(self._file(key))
        finally:
            path.unlink(missing_ok=True)

    def _work(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending)
                if self._closed:
                    return
                key = self._active = next(iter(self._pending))
                payload = self._pending.pop(key)
                if key == self._key:
                    self._status = "generating"
            status, error = "ready", ""
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                with metering(self.meter):
                    image = room_companion.generate_image(payload)
                self._save(key, image)
            except RuntimeError as exc:
                status, error = "error", str(exc)
            except OSError:
                status, error = "error", "The illustration could not be saved to the local cache."
            with self._condition:
                self._active = None
                if self._key == key:
                    self._status, self._error = status, error
                self._condition.notify_all()
