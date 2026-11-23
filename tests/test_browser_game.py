import json
import threading
from http.client import HTTPConnection

import pytest

import room_companion
import saga
from browser_game import BrowserGame
from characters import MAX_DESCRIPTION, STOCK, make, presets_public
from game_session import GameSession
from tests.support.browser import http_request, running_server, submit
from tests.support.stories import EXPLORE_ORCHARD, expanded_arc_plan


@pytest.fixture
def game(tmp_path, monkeypatch):
    monkeypatch.setattr(room_companion, "generate_image", lambda payload: b"art")
    viewer = room_companion.RoomCompanion(tmp_path, "test-model")
    game = BrowserGame(GameSession(), viewer)
    yield game
    game.close()


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
    plans = iter([None, None, json.dumps(expanded_arc_plan()), None])
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
    for command in EXPLORE_ORCHARD[1:]:
        state = submit(game, command)
    assert "north" in state["room"]["exits"]
    warning = submit(game, "go north")
    assert "will not move" in warning["latest"]["events"][0]["text"]
    assert "will cost you this life" in warning["room"]["description"]
    state = submit(game, "go north")
    assert state["room"]["life"] == 2 and state["room"]["name"] == "Orchard Gate"
    assert state["room"]["inventory"] == ["rusty sword", "reed wedge", "brass spindle", "silver bell"]
    assert state["room"]["lessons"]
    for command in ("go west", "go north", "go east"):
        state = submit(game, command)
    assert "will cost you this life" in state["room"]["description"]
    assert "What will you use it on?" in str(submit(game, "use silver bell")["latest"]["events"])
    state = submit(game, "use silver bell on glass heron")
    assert "stair of cloud" in str(state["latest"]["events"]) and "will cost you this life" not in state["room"]["description"]
    assert submit(game, "go west")["room"]["name"] == "Echo Vault"


def test_browser_character_chooser(tmp_path, monkeypatch):
    cards = []

    def illustrate(payload):
        if "character card" in payload["prompt"]:
            cards.append(payload["prompt"])
        return b"art"

    with running_server(tmp_path, monkeypatch, choose_character=True, illustrate=illustrate) as server:
        port = server.server_port
        headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
        status, page = http_request(port, "GET", "/")
        assert status == 200 and b'<dialog id="character"' in page and b"Who enters the castle?" in page
        assert b'<label for="character-description">' in page and b'<textarea id="character-description"' in page

        status, body = http_request(port, "GET", "/state")
        state = json.loads(body)
        assert status == 200 and state["turn"] == 0
        assert state["choosing"] == {"presets": presets_public(), "limit": MAX_DESCRIPTION}
        assert state["character"] is state["room"]["character"] is state["avatar"] is None
        assert state["art"]["kind"] == "room"
        assert server.avatar.snapshot() == {"status": "waiting"} and not cards

        command = json.dumps({"id": "first", "command": "look at yourself", "after": 0})
        status, body = http_request(port, "POST", "/commands", command, headers)
        assert status == 409 and json.loads(body) == {"error": "Choose your character first."}

        def choose(preset, description, **overrides):
            body = json.dumps({"preset": preset, "description": description})
            return http_request(port, "POST", "/character", body, dict(headers, **overrides))

        for preset, description, error in [
            ("unknown", "You have a braid.", "Choose a known character preset."),
            ("girl", " \t ", "Enter a character description."),
            ("girl", None, "Enter a character description."),
            ("girl", "x" * (MAX_DESCRIPTION + 1),
             f"Keep your character description to {MAX_DESCRIPTION} characters or fewer."),
        ]:
            status, body = choose(preset, description)
            assert status == 400 and json.loads(body) == {"error": error}
        description = "You have a dark braid and <bright> boots."
        assert choose("girl", description, Host="example.invalid")[0] == 403
        assert choose("girl", description, Origin="http://example.invalid")[0] == 403
        assert choose("girl", description, **{"Content-Type": "text/plain"})[0] == 415
        assert choose("girl", description, **{"Content-Length": "20000"})[0] == 413
        assert server.avatar.snapshot() == {"status": "waiting"} and not cards

        status, body = choose("girl", f"  {description}\n")
        state = json.loads(body)
        character = make("girl", description)
        assert status == 200 and state["choosing"] is None and state["turn"] == 0
        assert server.game.session.game.character == character
        assert state["character"] == state["room"]["character"] == character.public()
        with server.avatar._condition:
            assert server.avatar._condition.wait_for(lambda: server.avatar.snapshot()["status"] == "ready", timeout=3)
        assert len(cards) == 1 and description in cards[0]
        assert choose("child", STOCK.description)[0] == 409
        assert b"&lt;bright&gt;" in http_request(port, "GET", "/playthrough")[1]
        assert http_request(port, "POST", "/commands", command, headers)[0] == 202
        with server.game.condition:
            assert server.game.condition.wait_for(lambda: len(server.game.journal) == 1, timeout=3)
        state = json.loads(http_request(port, "GET", "/state")[1])
        assert description in state["latest"]["events"][-1]["text"]


@pytest.mark.parametrize("choose_character,journal,has_history", [
    (False, None, False), (True, [{"turn": 1}], False), (True, None, True),
])
def test_browser_character_chooser_is_disabled(tmp_path, monkeypatch, choose_character, journal, has_history):
    session = GameSession()
    session.game.character = make("girl")
    if has_history:
        session.turn("look")
    with running_server(tmp_path, monkeypatch, session, choose_character=choose_character, journal=journal) as server:
        port = server.server_port
        state = json.loads(http_request(port, "GET", "/state")[1])
        assert state["choosing"] is None and state["character"] == session.game.character.public()
        assert state["avatar"]["kind"] == "avatar"
        headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
        body = json.dumps({"preset": "child", "description": STOCK.description})
        assert http_request(port, "POST", "/character", body, headers)[0] == 409


def test_discovery_paintings_queue_without_blocking_turns_or_losing_earlier_scenes(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    requests = []

    def illustrate(payload):
        prompt = payload["prompt"]
        if "Landscape story illustration" in prompt:
            requests.append(prompt)
            if len(requests) == 1:
                entered.set()
                assert release.wait(timeout=3)
        return b"art"

    monkeypatch.setattr("content_generator.ContentGenerator.generate_description", lambda self, *a, **kw: kw["description"])
    with running_server(tmp_path, monkeypatch, illustrate=illustrate) as server:
        game = server.game
        try:
            assert not submit(game, "take sword")["cutscene"].get("key")
            state = submit(game, "use sword on wall")
            first = state["cutscene"]["key"]
            assert state["art"]["kind"] == "room" and state["latest"]["art"]["kind"] == "cutscene"
            assert entered.wait(timeout=3)
            for command in ("north", "north", "follow the banister", "north", "upend the tray"):
                state = submit(game, command)
            last = state["cutscene"]["key"]
            assert last != first and state["cutscene"]["status"] == "queued"
            assert len(requests) == 1
        finally:
            release.set()
        painter = server.viewer.cutscenes
        with painter._condition:
            assert painter._condition.wait_for(lambda: painter.snapshot()["status"] == "ready", timeout=3)
        assert len(requests) == 3 and "rusty sword" in requests[0]
        assert (tmp_path / f"{first}.jpg").is_file()
        assert "The player in action" in game.playthrough()
        client = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            client.request("GET", f"/art/{last}.jpg")
            response = client.getresponse()
            assert response.status == 200 and response.read() == b"art"
            client.request("GET", "/cutscene.js")
            response = client.getresponse()
            assert response.status == 200 and b"createCutscenePlayer" in response.read()
        finally:
            client.close()


def test_http_game_refresh_quit_and_validation(tmp_path, monkeypatch):
    with running_server(tmp_path, monkeypatch) as server:
        port = server.server_port

        def request(method, path, payload=None, headers=None):
            return http_request(port, method, path, payload, headers)

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
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    class Slot:
        ident, saved = "cli", []

        def save(self, session, journal):
            self.saved.append(len(journal))

    cli_journal = []
    main._play(None, cli_session, cli_journal, Slot())
    for command, (room, events) in zip(commands, terminal):
        state = submit(game, command)
        assert state["room"] == room and state["latest"]["events"] == events
    assert len(terminal) == len(commands) and Slot.saved == list(range(1, len(commands) + 1))
    assert [(e["command"], e["place"], e["events"]) for e in cli_journal] == [
        (e["command"], e["place"], e["events"]) for e in game.journal]
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


def test_portrait_replaces_the_room_art_for_one_turn(game):
    submit(game, "take candle")
    state = submit(game, "look at yourself")
    assert state["art"]["kind"] == "portrait" and list(state["art"]["items"]) == ["candle"]
    assert state["latest"]["events"][-1]["text"].endswith("You have nothing from the castle on. You carry the candle.")
    portrait_key = state["art"]["key"]
    state = submit(game, "examine shelf")
    assert state["art"]["kind"] == "room" and state["art"]["name"] == "Starting Room"
    assert submit(game, "me")["art"]["key"] == portrait_key  # An unchanged outfit reuses its painting.


def test_character_card_follows_the_outfit_but_not_the_room(tmp_path, monkeypatch):
    requests, outage = [], [True]

    def illustrate(payload):
        requests.append(payload)
        if outage[0] and "character card" in payload["prompt"] and "iron helm" in payload["prompt"]:
            raise RuntimeError("The image service could not be reached. Try again in a moment.")
        return b"art"

    monkeypatch.setattr("content_generator.ContentGenerator.generate_description",
                        lambda self, name, *a, **kw: kw["description"])
    with running_server(tmp_path, monkeypatch, illustrate=illustrate) as server:
        game, port = server.game, server.server_port

        def card():
            with server.avatar._condition:
                assert server.avatar._condition.wait_for(
                    lambda: server.avatar.snapshot()["status"] in ("ready", "cached", "error"), timeout=3)
            return game.snapshot()["avatar"]

        plain = card()
        requests.clear()  # Count outfit changes after the first card finishes.
        assert plain["kind"] == "avatar" and plain["items"] == () and plain["image"]
        client = HTTPConnection("127.0.0.1", port, timeout=2)
        client.request("GET", plain["image"], headers={"Host": f"127.0.0.1:{port}"})
        assert client.getresponse().read() == b"art"
        submit(game, "go north")
        assert card()["key"] == plain["key"]  # Walking around never repaints the card.
        submit(game, "south")
        submit(game, "take candle")
        holding = card()
        assert holding["key"] != plain["key"] and holding["items"] == ("candle",)
        cards = [r for r in requests if "character card" in r["prompt"]]
        assert len(cards) == 1 and "candle" in cards[0]["prompt"]  # Only the new outfit was painted.
        prompt = next(r for r in requests if "candle" in r["prompt"] and "character card" in r["prompt"])
        assert prompt["size"] == "1024x1536" and "Room" not in prompt["prompt"]
        for command in ("take sword", "use sword on wall", "down", "take helm", "use helm"):
            submit(game, command)
        failed = card()
        assert failed["status"] == "error" and game.snapshot()["room"]["worn"] == ["iron helm"]
        outage[0] = False
        client = HTTPConnection("127.0.0.1", port, timeout=2)
        client.request("POST", "/retry/" + failed["key"],
                       headers={"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}"})
        assert client.getresponse().status == 204
        assert card()["status"] == "ready"
