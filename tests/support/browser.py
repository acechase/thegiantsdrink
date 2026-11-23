"""Drive browser turns and serve test games on a temporary loopback port."""

import threading
from contextlib import contextmanager
from http.client import HTTPConnection

import room_companion
from browser_game import play_server
from game_session import GameSession


def submit(game, command, ident=None):
    turn = game.snapshot()["turn"]
    assert game.submit({"id": ident or str(turn), "command": command, "after": turn})[0] == 202
    with game.condition:
        assert game.condition.wait_for(lambda: len(game.journal) == turn + 1, timeout=3)
    return game.snapshot()


@contextmanager
def running_server(tmp_path, monkeypatch, session=None, *, illustrate=lambda payload: b"art", **options):
    monkeypatch.setattr(room_companion, "generate_image", illustrate)
    with play_server(session or GameSession(), tmp_path, "test-model", **options) as server:
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()
        try:
            yield server
        finally:
            server.shutdown()
            worker.join(timeout=2)


def http_request(port, method, path, payload=None, headers=None):
    client = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        client.request(method, path, payload, headers or {})
        response = client.getresponse()
        return response.status, response.read()
    finally:
        client.close()
