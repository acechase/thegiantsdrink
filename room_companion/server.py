"""HTTP routes and lifecycle for the read-only room viewer."""

import json
import os
import threading
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .images import DEFAULT_IMAGE_MODEL
from .renderer import RoomCompanion

STATIC_DIR = Path(__file__).resolve().with_name("static")
HERE = Path(__file__).resolve().parents[1]
ART_CACHE = HERE / ".room-art"


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
            state = dict(viewer.snapshot(), cutscene=viewer.cutscenes.snapshot() if viewer.cutscenes else None)
            self._send(200, "application/json", json.dumps(state).encode())
        elif self.path == "/cutscene.js":
            self._send(200, "text/javascript; charset=utf-8", (STATIC_DIR / "cutscene.js").read_bytes())
        else:
            for illustrator in self._illustrators():
                state = illustrator.snapshot()
                if self.path == state.get("image"):
                    try:
                        data = (illustrator.cache_dir / (state["key"] + ".jpg")).read_bytes()
                    except OSError:
                        illustrator.image_unavailable(state["key"])
                        self.send_error(404)
                        return
                    self._send(200, "image/jpeg", data)
                    return
            self.send_error(404)

    def _illustrators(self):
        viewer = self.server.viewer
        return [item for item in (viewer, viewer.cutscenes, getattr(self.server, "avatar", None)) if item]

    def _same_origin(self):
        return self.headers.get("Origin") == f"http://127.0.0.1:{self.server.server_port}"

    def do_POST(self):
        """Accept retry clicks from this local viewer."""
        if not self._same_origin():
            self.send_error(403)
        elif self.path.startswith("/retry/") and any(
            illustrator.retry(self.path.removeprefix("/retry/")) for illustrator in self._illustrators()
        ):
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
def open_companion(meter=None):
    """Launch a local viewer and release its server when the game ends."""
    try:
        page = (STATIC_DIR / "room_view.html").read_bytes()
        server = ThreadingHTTPServer(("127.0.0.1", 0), ViewerHandler)
    except OSError:
        print("[Companion] The local viewer could not start. Continuing in the terminal.", flush=True)
        yield None
        return
    with server:
        try:
            ART_CACHE.mkdir(parents=True, exist_ok=True)
            viewer = RoomCompanion(ART_CACHE, os.getenv("OPENAI_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL, meter=meter,
                                   cutscenes=True)
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
