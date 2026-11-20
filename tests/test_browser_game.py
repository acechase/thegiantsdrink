import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection

import pytest

import room_companion
import saga
from browser_game import BrowserGame, play_server
from game_session import GameSession
from tests.test_game import arc_plan


@pytest.fixture
def game(tmp_path, monkeypatch):
    monkeypatch.setattr(room_companion, "generate_image", lambda payload: b"art")
    viewer = room_companion.RoomCompanion(tmp_path, "test-model")
    game = BrowserGame(GameSession(), viewer)
    yield game
    game.close()


def submit(game, command, ident=None):
    turn = game.snapshot()["turn"]
    assert game.submit({"id": ident or str(turn), "command": command, "after": turn})[0] == 202
    with game.condition:
        assert game.condition.wait_for(lambda: len(game.journal) == turn + 1, timeout=3)
    return game.snapshot()


def test_concurrent_and_duplicate_commands_have_one_effect(game, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def narrate(*a, **kw):
        calls.append(1)
        entered.set()
        assert release.wait(timeout=3)
        return "Threads of gold hang from the tapestries."

    monkeypatch.setattr(game.session.content, "generate_description", narrate)
    request = {"id": "first", "after": 0, "command": "go north"}
    ack = game.submit(request)
    try:
        assert entered.wait(timeout=3)
        snapshot = game.snapshot()
        assert snapshot["room"]["name"] == "Starting Room" and snapshot["turn"] == 0
        assert any("Corridor" in evt["text"] for evt in snapshot["pending"]["events"])
        assert game.submit(request) == ack == (202, {"id": "first", "turn": 1})
        assert game.submit(dict(request, id="second"))[0] == 409
        assert game.submit(dict(request, command="quit"))[0] == 409
    finally:
        release.set()
    with game.condition:
        assert game.condition.wait_for(lambda: not game.pending, timeout=3)
    completed = game.snapshot()
    assert completed["room"]["description"] == "Threads of gold hang from the tapestries."
    assert game.submit(request) == ack and calls == [1]
    assert game.submit(dict(request, id="late"))[0] == 409
    assert len(game.entries()["entries"]) == 1


def test_failed_turn_rolls_back_and_can_retry(game, monkeypatch):
    original = GameSession.turn

    def fail_after_pickup(session, command, on_event):
        original(session, command, on_event)
        raise RuntimeError("interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(GameSession, "turn", fail_after_pickup)
        state = submit(game, "take candle")
    assert state["room"]["inventory"] == [] and "candle" in state["room"]["items"]
    assert state["latest"]["events"][0]["kind"] == "error"
    assert submit(game, "take candle")["room"]["inventory"] == ["candle"]


def test_replaying_a_fatal_request_keeps_the_same_life_and_journal_entry(game, monkeypatch):
    monkeypatch.setattr(game.session.content, "generate_description", lambda *args, **kw: kw["description"])
    for command in ("north", "north", "follow the banister", "north"):
        submit(game, command)
    turn = game.snapshot()["turn"]
    result = submit(game, "drink right", ident="first-sip")
    assert game.submit({"command": "drink right", "after": turn, "id": "first-sip"}) == (
        202, {"id": "first-sip", "turn": turn + 1}
    )
    assert game.snapshot()["room"] == result["room"] and result["room"]["life"] == 2
    entry, *earlier = game.entries()["entries"]
    assert [e["kind"] for e in entry["events"]] == ["death", "lesson", "respawn", "arrival"]
    assert len(earlier) == turn and result["room"]["lessons"] == [entry["events"][1]["text"]]


def test_pending_gate_obstacle_and_chapter_survive_browser_turns(game, monkeypatch):
    plans = iter([None, None, json.dumps(arc_plan()), None])
    monkeypatch.setattr(saga, "complete", lambda *args, **kwargs: next(plans))
    monkeypatch.setattr(game.session.content, "generate_description", lambda *a, **kw: kw["description"])
    for command in ("take sword", "go north", "go north", "follow the banister", "go north", "upend the tray"):
        submit(game, command)
    state = submit(game, "enter reflection")
    assert "north" in state["room"]["exits"]
    assert any(e["kind"] == "horizon" for e in state["latest"]["events"])
    assert "grey mist" in state["latest"]["events"][-1]["text"]
    state = submit(game, "go north")
    assert state["room"]["name"] == "Giant's Hall" and "north" in state["room"]["exits"]
    state = submit(game, "go north")
    assert state["room"]["name"] == "Orchard Gate" and state["room"]["inventory"] == ["rusty sword"]
    assert any(e["kind"] == "chapter" for e in state["latest"]["events"])
    assert "silver bell" not in json.dumps(state) and "planned_solutions" not in json.dumps(state)
    submit(game, "go south")
    assert all(e["kind"] != "chapter" for e in submit(game, "go north")["latest"]["events"])
    for command in ("go north", "take silver bell", "go east"):
        state = submit(game, command)
    assert "north" in state["room"]["exits"]
    warning = submit(game, "go north")
    assert "will not move" in warning["latest"]["events"][0]["text"]
    assert "will cost you this life" in warning["room"]["description"]
    state = submit(game, "go north")
    assert state["room"]["life"] == 2 and state["room"]["name"] == "Orchard Gate"
    assert state["room"]["inventory"] == ["rusty sword", "silver bell"] and state["room"]["lessons"]
    for command in ("go north", "go east"):
        state = submit(game, command)
    assert "will cost you this life" in state["room"]["description"]
    assert "What will you use it on?" in str(submit(game, "use silver bell")["latest"]["events"])
    state = submit(game, "use silver bell on glass heron")
    assert "stair of cloud" in str(state["latest"]["events"]) and "will cost you this life" not in state["room"]["description"]
    assert submit(game, "go west")["room"]["name"] == "Chiming Grove"


@contextmanager
def running_server(tmp_path, monkeypatch):
    monkeypatch.setattr(room_companion, "generate_image", lambda payload: b"art")
    with play_server(GameSession(), tmp_path, "test-model") as server:
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()
        try:
            yield server
        finally:
            server.shutdown()
            worker.join(timeout=2)


def test_http_game_refresh_quit_and_validation(tmp_path, monkeypatch):
    with running_server(tmp_path, monkeypatch) as server:
        port = server.server_port

        def request(method, path, payload=None, headers=None):
            client = HTTPConnection("127.0.0.1", port, timeout=2)
            try:
                client.request(method, path, payload, headers or {})
                response = client.getresponse()
                return response.status, response.read()
            finally:
                client.close()

        headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
        assert request("GET", "/")[0] == request("GET", "/play.js")[0] == request("GET", "/play.css")[0] == 200
        assert request("GET", "/state", headers={"Host": "example.invalid"})[0] == 403
        assert request("POST", "/commands", "{}")[0] == 403
        assert request("POST", "/commands", "x", dict(headers, **{"Content-Type": "text/plain"}))[0] == 415
        assert request("POST", "/commands", "{}", dict(headers, **{"Content-Length": "20000"}))[0] == 413
        for invalid in ("bad json", "[]", '{"id": "x", "after": true, "command": "look"}'):
            assert request("POST", "/commands", invalid, headers)[0] == 400
        payload = json.dumps({"id": "first", "after": 0, "command": "take candle"})
        ack = request("POST", "/commands", payload, headers)
        assert ack[0] == 202
        with server.game.condition:
            assert server.game.condition.wait_for(lambda: len(server.game.journal) == 1, timeout=2)
        assert request("POST", "/commands", payload, headers) == ack
        for _ in range(2):
            state = json.loads(request("GET", "/state")[1])
            assert state["turn"] == 1 and state["room"]["inventory"] == ["candle"]
        journal = json.loads(request("GET", "/journal")[1])
        assert len(journal["entries"]) == 1 and not journal["more"]
        assert not json.loads(request("GET", "/journal?before=1")[1])["entries"]
        state = submit(server.game, "quit")
        assert state["room"]["ended"] and "Thanks" in str(state["latest"])
        assert request("POST", "/commands", json.dumps({"id": "late", "after": 2, "command": "look"}), headers)[0] == 409


def test_cli_and_browser_follow_identical_turns(game, monkeypatch):
    import main
    from content_generator import ContentGenerator

    commands = ["look", "help", "take sword", "inventory", "go north", "go north", "follow the banister",
                "go north", "drink left", "upend the tray", "enter reflection", "go north", "quit"]
    monkeypatch.setattr(ContentGenerator, "generate_description", lambda self, name, *a, **kw: f"You enter {name}.")
    monkeypatch.setattr(saga, "complete", lambda *a, **kw: None)
    cli_session = GameSession()
    terminal = []

    def record(command, on_event):
        events = GameSession.turn(cli_session, command, on_event)
        visible = [{"kind": e.kind, "text": e.text} for e in events if e.kind != "activity"]
        terminal.append((cli_session.public_state(), visible))
        return events

    inputs = iter(commands)
    monkeypatch.setattr(cli_session, "turn", record)
    monkeypatch.setattr(main, "GameSession", lambda world: cli_session)
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    main._play(None)
    for command, (room, events) in zip(commands, terminal):
        state = submit(game, command)
        assert state["room"] == room and state["latest"]["events"] == events
    assert len(terminal) == len(commands)
    assert game.session.behavior.get_profile() == cli_session.behavior.get_profile()
    assert game.session.adaptation.get_difficulty() == cli_session.adaptation.get_difficulty()
    assert game.session.saga.gate == cli_session.saga.gate


def test_unavailable_cache_leaves_text_playable(tmp_path):
    cache = tmp_path / "unavailable"
    cache.write_text("occupies the cache path")
    viewer = room_companion.RoomCompanion(cache, "test-model")
    browser = BrowserGame(GameSession(), viewer)
    try:
        with viewer._condition:
            assert viewer._condition.wait_for(lambda: viewer.snapshot()["status"] == "error", timeout=2)
        assert submit(browser, "examine shelf")["latest"]["events"][0]["kind"] == "outcome"
    finally:
        browser.close()
