import json
import sqlite3
from http.client import HTTPConnection

import pytest

from game_session import GameSession
from main import _nominate, _stories
from saves import SaveSlot
from story_catalog import Catalog, StoryError, bundle_bytes, nomination, read_nomination
from tests.test_browser_game import running_server, submit
from tests.test_game import EXPLORE_ORCHARD, arc_plan, expanded_arc_plan

SOLVE = ("go north", "go north", "take silver bell", "go east", "use silver bell on glass heron")


def post_cup(session):
    session.game.current_room = "Giant's Hall"
    session.game.giant_defeated = True
    session.turn("look")
    return session


def test_private_capture_requires_entry_and_explicit_confirmation(tmp_path, monkeypatch):
    first, next_arc = expanded_arc_plan(), expanded_arc_plan("Second ")
    first["private_metadata"] = "omit this"
    responses = iter([first, next_arc])
    monkeypatch.setattr("saga.complete", lambda *a, **kw: json.dumps(next(responses)))
    session = post_cup(GameSession(catalog_path=tmp_path / "uncreated.sqlite3"))
    slot = SaveSlot.new(tmp_path)
    with pytest.raises(StoryError, match="Enter a chapter"):
        session.story_nomination()
    for command in (*EXPLORE_ORCHARD, SOLVE[-1]):
        session.turn(command)
    assert len(session.saga.arcs) == 2
    assert session.story_nomination() == nomination([expanded_arc_plan()])
    slot.save(session, [])
    restored, _ = slot.load()
    assert restored.story_nomination() == session.story_nomination()
    monkeypatch.setattr("builtins.input", lambda _: "no")
    _nominate(restored, slot)
    assert not (tmp_path / "nominations").exists()
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    _nominate(restored, slot)
    _nominate(restored, slot)
    exported, = (tmp_path / "nominations").glob("*.json")
    assert read_nomination(exported) == nomination([expanded_arc_plan()])
    assert not (tmp_path / "uncreated.sqlite3").exists()
    restored.saga.arcs[0].plan = None
    with pytest.raises(StoryError, match="older save"):
        restored.story_nomination()


def test_publish_reuse_and_extend_at_the_first_uncached_crossing(tmp_path, monkeypatch):
    catalog = Catalog(tmp_path / "published.sqlite3")
    first, second, third = arc_plan(), arc_plan("Second "), expanded_arc_plan("Third ")
    catalog.publish(nomination([first]), "orchard", 1, "The Glass Orchard")
    label, pinned = catalog.route("orchard")
    catalog.publish(nomination([first, second]), "orchard", 2, "The Glass Orchard")
    assert catalog.route("orchard")[0] == "orchard@2"
    assert catalog.route(label)[1] == pinned
    assert catalog.matching_routes([first, second]) == [("orchard@1", False), ("orchard@2", True)]

    calls = []

    def generate(_prompt, request, **kwargs):
        calls.append(json.loads(request)["arc_number"])
        return json.dumps(third)

    monkeypatch.setattr("saga.complete", generate)
    session = post_cup(GameSession(edition=label, plans=pinned, catalog_path=catalog.path))
    for command in SOLVE:
        session.turn(command)
    assert calls == []
    assert session.saga.arcs[1].title == second["title"]
    for command in ("go north", "go north", "take second silver bell", "go east", "use second silver bell on glass heron"):
        session.turn(command)
    assert calls == [3]
    assert len(session.story_nomination()["arcs"]) == 2
    session.turn("go north")
    assert len(session.story_nomination()["arcs"]) == 3
    assert len(catalog.editions()) == 2
    with sqlite3.connect(catalog.path) as db:
        assert db.execute("SELECT COUNT(*) FROM plans").fetchone() == (2,)

    fresh = post_cup(GameSession(catalog_path=catalog.path))
    assert fresh.saga.arcs[0].title == third["title"]
    assert calls == [3, 1]


def test_replay_skips_incompatible_preference_and_accepts_missing_optional_tool(tmp_path, monkeypatch):
    catalog = Catalog(tmp_path / "published.sqlite3")
    first, alternate = arc_plan(), arc_plan("Other ")
    alternate["challenge"]["solutions"].append({"tool": "old flute", "approach": "play", "outcome": "The gate opens."})
    catalog.publish(nomination([first]), "orchard", 1, "Orchard")
    catalog.publish(nomination([alternate]), "other", 1, "Other")
    label, plans = catalog.route("orchard")
    session = GameSession(edition=label, plans=plans, catalog_path=catalog.path)
    from game_world import Item
    session.game.inventory.append(Item("silver bell", "A bell from elsewhere."))
    monkeypatch.setattr("saga.complete", lambda *a, **kw: pytest.fail("Called the planner on a published crossing"))
    post_cup(session)
    assert session.saga.arcs[0].title == alternate["title"]
    challenge = session.rooms[session.saga.arcs[0].challenge_room].challenge
    assert len(challenge.solutions) == 1
    assert challenge.outcome("other silver bell")


def test_published_passages_keep_their_progress_on_resume(tmp_path, monkeypatch):
    catalog = Catalog(tmp_path / "published.sqlite3")
    catalog.publish(nomination([expanded_arc_plan()]), "orchard", 1, "Orchard")
    label, plans = catalog.route("orchard")
    monkeypatch.setattr("saga.complete", lambda *a, **kw: pytest.fail("Called the planner on a published arc"))
    session = post_cup(GameSession(edition=label, plans=plans, catalog_path=catalog.path))
    for command in EXPLORE_ORCHARD[:8]:
        session.turn(command)
    assert session.room.name == "Bell Foundry"
    assert session.public_state()["exits"] == ["west"]
    assert not session.rooms["Weather Porch"].passage_open
    slot = SaveSlot.new(tmp_path / "saves")
    slot.save(session, [])
    restored, _ = slot.load()
    for command in EXPLORE_ORCHARD[8:]:
        restored.turn(command)
    assert restored.room.name == "Still Pool"
    assert restored.rooms["Chiming Grove"].passage_open
    assert restored.rooms["Weather Porch"].passage_open
    assert session.rooms["Weather Porch"].exits == {"east": "Orchard Gate"}


def test_maintainer_can_queue_review_playtest_and_publish(tmp_path, monkeypatch, capsys):
    path, queue = tmp_path / "nomination.json", tmp_path / "queue"
    db = tmp_path / "published.sqlite3"
    path.write_bytes(bundle_bytes(nomination([arc_plan()])))
    _stories(["--catalog", str(db), "import", str(path)], queue)
    queued, = queue.glob("*.json")
    ident = queued.stem
    assert ident in capsys.readouterr().out
    assert not db.exists()
    _stories(["--catalog", str(db), "review", ident], queue)
    assert "The Glass Orchard" in capsys.readouterr().out
    monkeypatch.setattr("saga.complete", lambda *a, **kw: pytest.fail("Played past the end of the nomination"))
    commands = iter([*SOLVE, "go north", "quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(commands))
    _stories(["playtest", ident], queue)
    assert "You have reached the end of the nominated route." in capsys.readouterr().out
    _stories(["--catalog", str(db), "publish", ident, "orchard", "1", "--title", "Glass Orchard"], queue)
    assert Catalog(db).route("orchard")[0] == "orchard@1"
    with pytest.raises(StoryError, match="already published as orchard@1"):
        Catalog(db).publish(nomination([arc_plan()]), "copy", 1, "A Copy")
    with pytest.raises(StoryError, match="already published"):
        Catalog(db).publish(nomination([arc_plan("Alternate ")]), "orchard", 1, "Orchard")
    with pytest.raises(StoryError, match="next revision"):
        Catalog(db).publish(nomination([arc_plan("Alternate ")]), "orchard", 3, "Orchard")
    broken = arc_plan()
    broken["challenge"]["room"] = []
    path.write_bytes(bundle_bytes(nomination([broken])))
    with pytest.raises(SystemExit, match="challenge.room must name"):
        _stories(["import", str(path)], queue)
    assert list(queue.glob("*.json")) == [queued]


def test_browser_only_previews_the_entered_route(tmp_path, monkeypatch):
    monkeypatch.setattr("saga.complete", lambda *a, **kw: json.dumps(expanded_arc_plan()))
    with running_server(tmp_path, monkeypatch, session=post_cup(GameSession())) as server:
        client = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            client.request("GET", "/nomination")
            response = client.getresponse()
            assert response.status == 409
            response.read()
            assert submit(server.game, "go north")["room"]["can_nominate"]
            client.request("GET", "/nomination")
            response = client.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == nomination([expanded_arc_plan()])
        finally:
            client.close()
