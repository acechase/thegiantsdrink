import contextlib
import io
import json
import pickle
import shutil
from unittest.mock import patch

import pytest

import room_companion
from browser_game import BrowserGame
from content_generator import ContentGenerator
from game_session import GameSession
from playthrough import render
from saves import SaveError, SaveSlot, _key, _sign
from tests.test_browser_game import submit
from tests.test_game import openai_api  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def quiet_narrator(monkeypatch):
    # Patch the class, not an instance, so sessions stay picklable.
    monkeypatch.setattr(ContentGenerator, "generate_description", lambda self, name, *a, **kw: kw["description"])


def played(*commands):
    session, journal = GameSession(), []
    for command in commands:
        events = session.turn(command)
        journal.append({"turn": len(journal) + 1, "command": command, "place": "", "chapter": "The Castle",
                        "events": [{"kind": e.kind, "text": e.text} for e in events if e.kind != "activity"]})
    return session, journal


def test_a_saved_game_resumes_where_it_stopped(tmp_path):
    session, journal = played("take candle", "take helm", "north", "quit")
    slot = SaveSlot.new(tmp_path)
    slot.save(session, journal)
    record = slot.record()
    assert record["turns"] == 4 and record["place"] == "Corridor" and record["journal"] == journal
    resumed, saved_journal = SaveSlot.find(tmp_path).load()
    assert saved_journal == journal and resumed.room.name == "Corridor" and not resumed.ended
    assert [item.name for item in resumed.game.inventory] == ["candle"]
    assert resumed.game.trail == [("north", "Corridor")]
    assert "rusty sword" in [item.name for item in resumed.rooms["Starting Room"].items]


def test_saves_from_elsewhere_or_altered_saves_are_view_only(tmp_path):
    session, journal = played("take candle")
    slot = SaveSlot.new(tmp_path / "mine")
    slot.save(session, journal)
    shutil.copytree(slot.path, tmp_path / "theirs" / slot.ident)
    with pytest.raises(SaveError, match="another computer"):
        SaveSlot.find(tmp_path / "theirs", slot.ident).load()
    assert "take candle" in render(SaveSlot(tmp_path / "theirs", slot.ident).record(), tmp_path)
    state = slot.path / "state.bin"
    blob = bytearray(state.read_bytes())
    blob[-5] ^= 1
    state.write_bytes(bytes(blob))
    with pytest.raises(SaveError, match="changed"):
        slot.load()
    with pytest.raises(SaveError, match="no saved game called"):
        SaveSlot.find(tmp_path / "mine", "../mine")


def test_older_saves_gain_state_added_since(tmp_path):
    slot = SaveSlot.new(tmp_path)
    slot.save(*played("take candle"))
    # Rewrite the snapshot as an older build would have pickled it, before these attributes existed.
    data = pickle.loads((slot.path / "state.bin").read_bytes()[32:])
    session = data["session"]
    del session.game.worn, session.portrait, session.rooms["Great Staircase"].trial
    snapshot = pickle.dumps(data)
    (slot.path / "state.bin").write_bytes(_sign(_key(tmp_path, create=False), snapshot) + snapshot)
    resumed, _ = slot.load()
    assert resumed.game.worn == {} and resumed.portrait is None and resumed.rooms["Great Staircase"].trial is None


def test_continue_picks_the_latest_resumable_game(tmp_path):
    older, newer, archive = SaveSlot(tmp_path, "a"), SaveSlot(tmp_path, "b"), SaveSlot(tmp_path, "c")
    for slot in (older, newer):
        slot.save(*played("take candle"))
    archive.write_record(GameSession().public_state(), [])
    assert SaveSlot.all(tmp_path)[0].ident == "c"
    assert SaveSlot.find(tmp_path).ident == "b"
    assert "view only" in archive.summary()
    with pytest.raises(SaveError, match="no world snapshot"):
        archive.load()


def test_playthrough_page_is_escaped_and_embeds_each_picture_once(tmp_path):
    room, portrait = "a" * 64, "b" * 64
    (tmp_path / f"{room}.jpg").write_bytes(b"room-art")
    (tmp_path / f"{portrait}.jpg").write_bytes(b"portrait-art")
    turn = {"place": "Hall", "chapter": "The Castle", "events": [{"kind": "death", "text": "You die."}]}
    record = {"started": "2026-11-20T15:32:07", "life": 2, "lessons": ["Hold the rail."], "journal": [
        {**turn, "command": "<script>alert(1)</script>", "art": {"key": room, "kind": "room"}},
        {**turn, "command": "me", "art": {"key": portrait, "kind": "portrait"}},
        {**turn, "command": "look", "art": {"key": room, "kind": "room"}},
        {**turn, "command": "x", "art": {"key": "../../secret", "kind": "room"}, "events": [{"kind": "bad\"", "text": "<b>"}]},
    ]}
    page = render(record, tmp_path)
    assert "<script>alert" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "&lt;b&gt;" in page
    assert page.count("cm9vbS1hcnQ=") == 1 and page.count("cG9ydHJhaXQtYXJ0") == 1  # Each image is embedded once.
    assert page.count(f'class="art a{room}"') == 2 and f'class="art portrait a{portrait}"' in page
    assert "November 20, 2026 · 4 turns · 1 death · 1 chapter" in page and "<li>Hold the rail.</li>" in page
    assert 'class="bad' not in page and '<p class="death">You die.</p>' in page


def test_terminal_games_autosave_resume_list_and_export(openai_api, tmp_path):  # noqa: F811
    import main

    def run(inputs, **options):
        out = io.StringIO()
        with patch("builtins.input", side_effect=[*inputs, EOFError()]), contextlib.redirect_stdout(out):
            main.main(**options)
        return out.getvalue()

    first = run(["take candle", "take sword"])
    (slot,) = SaveSlot.all(tmp_path / "saves")
    assert f"Autosaving as {slot.ident}" in first and slot.record()["turns"] == 2
    second = run(["inventory"], resume=True)
    assert f"Resuming {slot.ident} after turn 2" in second and "Inventory: candle, rusty sword" in second
    assert [e["turn"] for e in slot.record()["journal"]] == [1, 2, 3]
    assert "3 turns, life 1, Starting Room in The Castle" in run([], saves=True)
    assert "Wrote the playthrough" in run([], export="")
    assert "take sword" in (slot.path / "playthrough.html").read_text()
    with pytest.raises(SystemExit, match="no saved game called nope"):
        run([], load="nope")


def test_browser_games_autosave_and_resume_with_their_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(room_companion, "generate_image", lambda payload: b"art")
    slot = SaveSlot.new(tmp_path / "saves")
    game = BrowserGame(GameSession(), room_companion.RoomCompanion(tmp_path / "art", "m"), store=slot)
    try:
        submit(game, "take candle")
        submit(game, "look at yourself")
        with game.condition:
            assert game.condition.wait_for(lambda: (game.saved or {}).get("turn") == 2, timeout=3)
        assert game.snapshot()["save"] == {"id": slot.ident, "turn": 2}
        assert "look at yourself" in game.playthrough()
    finally:
        game.close()
    session, journal = slot.load()
    assert journal[1]["art"]["kind"] == "portrait"
    resumed = BrowserGame(session, room_companion.RoomCompanion(tmp_path / "art", "m"), journal=journal, store=slot)
    try:
        assert resumed.snapshot()["turn"] == 2 and resumed.snapshot()["room"]["inventory"] == ["candle"]
        assert submit(resumed, "inventory")["latest"]["turn"] == 3
        with resumed.condition:
            assert resumed.condition.wait_for(lambda: resumed.saved["turn"] == 3, timeout=3)
        assert json.loads((slot.path / "game.json").read_text())["turns"] == 3
    finally:
        resumed.close()
