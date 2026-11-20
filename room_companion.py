"""A loopback browser viewer and background illustrator for the CLI."""

import base64
import binascii
import hashlib
import json
import os
import threading
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests

HERE = Path(__file__).resolve().parent
DEFAULT_IMAGE_MODEL = "gpt-image-2.6-flare"
STYLE = (
    "Landscape environmental painting for a surreal fairy-tale castle exploration game. "
    "Paint from the player's eye level with fine gouache brushwork, restrained slate blues, "
    "earthy umber and localized warm light. Give the architecture a clear silhouette and tangible scale. "
    "Keep the same illustrated field-guide style across rooms. Show only visible clues, scenery and "
    "objects described below. Players can try to interact with anything they see, so do not add furniture, "
    "containers, rugs, props, creatures or decorations that the text does not mention. Leave out unseen "
    "destinations, the player, lettering, icons and interface elements."
)


@dataclass(frozen=True)
class Scene:
    """A snapshot of the visible world, independent of subsequent game turns."""

    name: str
    description: str
    items: tuple
    scenery: tuple
    exits: tuple

    @classmethod
    def from_room(cls, room):
        """Capture what a player can currently see."""
        items, scenery = tuple(i.name for i in room.items), tuple(i.name for i in room.scenery)
        return cls(room.name, room.prose(), items, scenery, tuple(room.visible_exits))

    def request(self, model):
        """Build the image request used both for generation and cache identity."""
        prompt = (
            f"{STYLE}\nRoom: {self.name}\n{self.description}\n"
            f"Visible loose items: {', '.join(self.items) or 'none'}. "
            f"Landmarks: {', '.join(self.scenery) or 'none'}. Visible exits: {', '.join(self.exits) or 'none'}."
        )
        return {"model": model, "prompt": prompt, "size": "1536x1024", "quality": "low", "output_format": "jpeg", "n": 1}


def generate_image(payload):
    """Return a JPEG from OpenAI or raise a player-readable error."""
    try:
        response = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json=payload,
            timeout=(15, 240),
        )
        response.raise_for_status()
        data = base64.b64decode(response.json()["data"][0]["b64_json"], validate=True)
        if not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
            raise ValueError("Invalid JPEG")
        return data
    except requests.HTTPError as exc:
        code = exc.response.status_code
        hint = {
            400: "Check the requested image model and its supported settings.",
            401: "Check your OpenAI API key.",
            403: "Check your project's access to the image model.",
            404: "Check OPENAI_IMAGE_MODEL.",
            429: "Check your OpenAI quota or try again later.",
        }.get(code, "Try again in a moment.")
        raise RuntimeError(f"Image request failed (HTTP {code}). {hint}") from exc
    except requests.RequestException as exc:
        raise RuntimeError("The image service could not be reached. Try again in a moment.") from exc
    except (KeyError, IndexError, ValueError, TypeError, binascii.Error) as exc:
        raise RuntimeError("The image service returned an unreadable image.") from exc


class RoomCompanion:
    """Render one scene at a time, keeping only the latest waiting scene."""

    def __init__(self, cache_dir, model):
        """Prepare a disk cache and start an idle image worker."""
        self.cache_dir = Path(cache_dir)
        self.model = model
        self._condition = threading.Condition()
        self._closed = False
        self._scene = None
        self._description = ""
        self._key = self._active = None
        self._status = "waiting"
        self._error = ""
        self._worker = threading.Thread(target=self._work, name="room-illustrator", daemon=True)
        self._worker.start()

    def show(self, room, *, description=None):
        """Publish a scene immediately, coalescing duplicate and superseded requests."""
        scene = Scene.from_room(room)
        with self._condition:
            if self._closed:
                return
            self._description = description or scene.description
            if scene == self._scene:
                return
            self._scene = scene
            self._key = hashlib.sha256(json.dumps(scene.request(self.model), sort_keys=True).encode()).hexdigest()
            self._error = ""
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
            self._condition.notify_all()

    def snapshot(self):
        """Return public display data without credentials or local paths."""
        with self._condition:
            scene = self._scene
            if scene is None:
                return {"status": "waiting"}
            ready = self._status in ("ready", "cached")
            return {
                "key": self._key, "name": scene.name, "description": self._description,
                "items": scene.items, "scenery": scene.scenery, "exits": scene.exits, "model": self.model,
                "status": self._status, "error": self._error, "image": f"/art/{self._key}.jpg" if ready else None,
            }

    def image_unavailable(self, key):
        """Offer a retry when the active illustration disappeared from disk."""
        with self._condition:
            if self._key == key and self._status in ("ready", "cached"):
                self._status = "error"
                self._error = "The cached illustration could not be read. Try illustrating the room again."

    def retry(self, key):
        """Retry a failed scene only if it is still on screen."""
        with self._condition:
            if self._key == key and self._status == "error" and not self._closed:
                self._error = ""
                self._status = "queued"
                self._condition.notify_all()
                return True
            return False

    def close(self):
        """Discard queued work and let an in-flight request finish in the background."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()

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
                self._condition.wait_for(lambda: self._closed or self._status == "queued")
                if self._closed:
                    return
                key = self._active = self._key
                self._status = "generating"
                payload = self._scene.request(self.model)
            status, error = "ready", ""
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self._save(key, generate_image(payload))
            except RuntimeError as exc:
                status, error = "error", str(exc)
            except OSError:
                status, error = "error", "The illustration could not be saved to the local cache."
            with self._condition:
                self._active = None
                if self._key == key:
                    self._status, self._error = status, error
                self._condition.notify_all()


class ViewerHandler(BaseHTTPRequestHandler):
    """Serve the viewer, its current scene and the corresponding illustration."""

    def _send(self, status, media_type, body):
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        """Expose only the viewer and the active scene."""
        viewer = self.server.viewer
        if self.path == "/":
            self._send(200, "text/html; charset=utf-8", self.server.page)
        elif self.path == "/state":
            self._send(200, "application/json", json.dumps(viewer.snapshot()).encode())
        else:
            state = viewer.snapshot()
            if self.path == state.get("image"):
                try:
                    data = (viewer.cache_dir / (state["key"] + ".jpg")).read_bytes()
                except OSError:
                    viewer.image_unavailable(state["key"])
                    self.send_error(404)
                    return
                self._send(200, "image/jpeg", data)
            else:
                self.send_error(404)

    def _same_origin(self):
        return self.headers.get("Origin") == f"http://127.0.0.1:{self.server.server_port}"

    def do_POST(self):
        """Accept retry clicks from this local viewer."""
        if not self._same_origin():
            self.send_error(403)
        elif self.path.startswith("/retry/") and self.server.viewer.retry(self.path.removeprefix("/retry/")):
            self._send(204, "text/plain", b"")
        else:
            self.send_error(409)

    def log_message(self, format, *args):
        """Keep browser polling out of the game's input prompt."""


def launch_tab(url):
    """Open the local URL when a default browser is available."""
    try:
        webbrowser.open_new_tab(url)
    except (webbrowser.Error, OSError):
        pass  # Headless players can use the printed URL.


@contextmanager
def open_companion():
    """Launch a local viewer and release its server when the game ends."""
    try:
        page = (HERE / "room_view.html").read_bytes()
        server = ThreadingHTTPServer(("127.0.0.1", 0), ViewerHandler)
    except OSError:
        print("[Companion] The local viewer could not start. Continuing in the terminal.", flush=True)
        yield None
        return
    with server:
        try:
            cache_dir = HERE / ".room-art"
            cache_dir.mkdir(parents=True, exist_ok=True)
            viewer = RoomCompanion(cache_dir, os.getenv("OPENAI_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL)
        except OSError:
            print("[Companion] The image cache could not be opened. Continuing in the terminal.", flush=True)
            yield None
            return
        server.viewer, server.page = viewer, page
        threading.Thread(target=server.serve_forever, name="room-viewer", daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            print(f"[Companion] Room view: {url}", flush=True)
            launch_tab(url)
            yield viewer
        finally:
            viewer.close()
            server.shutdown()
