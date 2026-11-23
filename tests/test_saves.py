import gzip
import json
import pickle
import shutil
from pathlib import Path

import pytest

import room_companion
from browser_game import BrowserGame
from characters import STOCK, make
from content_generator import ContentGenerator
from game_session import GameSession
from game_session.journal import JournalTurn
from playthrough import render
from saves import SaveError, SaveSlot, _key, _sign
from tests.support.browser import submit
from tests.support.cli import run_cli


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
    character = session.game.character = make("girl", "You are a girl in a <blue> coat.")
    slot = SaveSlot.new(tmp_path)
    slot.save(session, journal)
    record = slot.record()
    assert record["turns"] == 4 and record["place"] == "Corridor" and record["journal"] == journal
    assert record["character"] == session.public_state()["character"] == character.public()
    resumed, saved_journal = SaveSlot.find(tmp_path).load()
    assert saved_journal == journal and resumed.room.name == "Corridor" and not resumed.ended
    assert resumed.game.character == character
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
    slot.save(*played("take sword", "use sword on wall", "look at yourself"))
    # Rewrite the snapshot as an older build would have pickled it, before these attributes existed.
    data = pickle.loads((slot.path / "state.bin").read_bytes()[32:])
    session = data["session"]
    del session.game.character, session.discovered_actions, session.rooms["Great Staircase"].trial
    object.__delattr__(session.portrait, "look")
    object.__delattr__(session.cutscene.outfit, "look")
    snapshot = pickle.dumps(data)
    (slot.path / "state.bin").write_bytes(_sign(_key(tmp_path, create=False), snapshot) + snapshot)
    resumed, _ = slot.load()
    assert resumed.game.worn == {} and resumed.rooms["Great Staircase"].trial is None
    assert resumed.discovered_actions == set()
    assert resumed.game.character == STOCK
    for painting in (resumed.portrait, resumed.cutscene):
        assert STOCK.description in painting.request("model")["prompt"]


def test_resume_and_finish_a_puzzle_saved_before_the_package_split(tmp_path):
    # Commit 49d85e5 wrote this snapshot after EXPLORE_ORCHARD and "look at yourself".
    # Keep its original pickle bytes and sign them for this test installation.
    fixture = Path(__file__).with_name("fixtures") / "session-49d85e5.pickle.gz"
    snapshot = gzip.decompress(fixture.read_bytes())
    slot = SaveSlot(tmp_path, "orchard")
    slot.path.mkdir()
    (slot.path / "state.bin").write_bytes(_sign(_key(tmp_path, create=True), snapshot) + snapshot)

    session, journal = slot.load()
    assert len(journal) == 16 and journal[-1]["command"] == "look at yourself"
    assert session.room.name == session.portrait.place == "Still Pool"
    assert session.cutscene.scene.name == "Weather Porch"
    assert session.room.challenge.solution_for("silver bell").approach == "ring a matching note"
    command = "use silver bell on glass heron"
    turn = JournalTurn.start(len(journal) + 1, command, session.public_state())
    turn.events = session.turn(command)
    journal.append(turn.record(art=None))
    assert session.room.challenge.resolved
    slot.save(session, journal)
    restored, saved_journal = slot.load()
    assert restored.room.challenge.resolved and saved_journal == journal


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
    character = {"name": "<A name>", "description": "You wear a <blue> coat."}
    record = {"started": "2026-11-20T15:32:07", "life": 2, "lessons": ["Hold the rail."], "character": character, "journal": [
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
    assert "<A name>" not in page and "<blue>" not in page
    assert page.count("&lt;A name&gt;") == page.count("You wear a &lt;blue&gt; coat.") == 1
    assert page.index("You wear a &lt;blue&gt; coat.") < page.index("</header>")


def test_saved_cutscene_survives_later_turns_and_does_not_repeat(tmp_path):
    session = GameSession()
    for command in ("take sword", "use sword on wall", "north"):
        session.turn(command)
    slot = SaveSlot.new(tmp_path)
    slot.save(session, [])
    restored, _ = slot.load()
    assert restored.cutscene == session.cutscene
    assert restored.discovered_actions == session.discovered_actions
    restored.turn("south")
    restored.turn("use sword on wall")
    assert restored.cutscene_turn == 2 and restored.cutscene == session.cutscene


@pytest.mark.parametrize("description", [
    "The tapestries are knotted into a large fabric bag.",
    "A faded tapestry folded into a crude backpack and bound with an iron chain.",
])
def test_legacy_tapestry_bag_recovers_storage_and_keeps_it_on_resume(tmp_path, description):
    from game_world import Item

    session = GameSession()
    tapestry = Item("tapestries", description, aliases=("tapestry",))
    salt = Item("salt", "A pinch of salt.")
    session.game.inventory.extend([tapestry, salt])
    # The legacy snapshot stored wearing by name on GameState.
    vars(session.game)["worn"] = {"tapestries": "The bag hangs from your shoulders."}
    slot = SaveSlot.new(tmp_path)
    slot.save(session, [])
    restored, _ = slot.load()
    assert restored.public_state()["worn"] == ["tapestry bag"]
    assert restored.public_state()["containers"] == {"tapestry bag": []}
    restored.turn("place the salt into the makeshift bag")
    assert restored.public_state()["inventory"] == ["tapestry bag"]
    assert restored.public_state()["containers"] == {"tapestry bag": ["salt"]}
    assert "Inside: salt." in restored.turn("look in bag")[-1].text
    slot.save(restored, [])
    restored, _ = slot.load()
    restored.turn("take the salt from the backpack")
    assert restored.public_state()["inventory"] == ["tapestry bag", "salt"]
    assert restored.public_state()["containers"] == {"tapestry bag": []}


def test_terminal_games_autosave_resume_list_and_export(openai_api, tmp_path):
    first = run_cli(["take candle", "take sword"])
    (slot,) = SaveSlot.all(tmp_path / "saves")
    assert f"Autosaving as {slot.ident}" in first and slot.record()["turns"] == 2
    assert "This game has used 0 AI tokens: 0 for text and 0 for 0 illustrations." in first
    second = run_cli(["inventory"], resume=True)
    assert f"Resuming {slot.ident} after turn 2" in second and "Inventory: candle, rusty sword" in second
    assert [e["turn"] for e in slot.record()["journal"]] == [1, 2, 3]
    assert "3 turns, life 1, Starting Room in The Castle" in run_cli([], saves=True)
    assert "Wrote the playthrough" in run_cli([], export="")
    assert "take sword" in (slot.path / "playthrough.html").read_text()
    with pytest.raises(SystemExit, match="no saved game called nope"):
        run_cli([], load="nope")


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
