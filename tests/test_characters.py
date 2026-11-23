import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import main
from characters import MAX_DESCRIPTION, PRESETS, STOCK, CharacterError, clean, make, presets_public
from environment_actions import resolve_environment
from game_session import GameSession
from game_world import Item
from room_companion import Avatar
from saves import SaveSlot
from tests.test_game import openai_api  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def no_openai(openai_api):  # noqa: F811
    yield
    assert not openai_api.calls


def test_presets_and_custom_character():
    assert list(PRESETS) == ["child", "girl"]
    assert presets_public() == [make("child").public(), make("girl").public()]
    assert make() == STOCK
    assert make("girl", f"  {PRESETS['girl'][1]}\n").name == "The girl"
    custom = make("girl", "You have a blue ribbon.")
    assert custom.public() == {"preset": "girl", "name": "Your own character",
                               "description": "You have a blue ribbon.", "edited": True}
    assert not STOCK.edited and STOCK.name == "The child"
    with pytest.raises(CharacterError, match="preset"):
        make("giant")


def test_description_is_cleaned_before_validation():
    assert clean(" \tYou\x1b wear\n a blue\u200b coat.\x00 ") == "You wear a blue coat."
    assert clean("x" * MAX_DESCRIPTION + "  ") == "x" * MAX_DESCRIPTION
    with pytest.raises(CharacterError, match="Enter a character description"):
        clean("\n\x00 ")
    with pytest.raises(CharacterError, match=str(MAX_DESCRIPTION)):
        clean("x" * (MAX_DESCRIPTION + 1))


def test_character_changes_all_player_painting_requests():
    def paintings(character):
        session = GameSession()
        session.game.character = character
        for command in ("take sword", "use sword on wall", "look at yourself"):
            outcome = session.turn(command)[-1].text
        assert outcome.startswith(f"You look yourself over. {character.description} ")
        return [painting.request("model") for painting in (Avatar.of(session.game), session.portrait, session.cutscene)]

    custom = make("girl", "You have a long braid with a blue ribbon.")
    for stock, edited in zip(paintings(STOCK), paintings(custom), strict=True):
        assert STOCK.description in stock["prompt"] and custom.description in edited["prompt"]
        assert stock != edited


def test_environment_adjudicator_receives_character_description(monkeypatch):
    session = GameSession()
    session.game.character = make("girl")
    complete = Mock(return_value='{"effect":"observe","anchor":"pedestal","reply":"It is too tall to climb."}')
    monkeypatch.setattr("environment_actions.complete", complete)
    resolve_environment(session.game, session.room, "climb onto the pedestal", "pedestal")
    prompt, payload = complete.call_args.args
    assert json.loads(payload)["player"] == session.game.character.description
    assert "physical plausibility and fit" in prompt


def test_body_part_tool_reaches_the_adjudicator_without_a_held_tool(monkeypatch):
    session = GameSession()
    session.game.character = make("girl")
    parse = Mock(return_value='{"action":"use_on","tool":"braid","target":"candle"}')
    reply = "The candle is too hot to wrap your braid around."
    complete = Mock(return_value=json.dumps({"effect": "observe", "anchor": "candle", "reply": reply}))
    monkeypatch.setattr("command_parser.complete", parse)
    monkeypatch.setattr("environment_actions.complete", complete)
    command = "wrap my braid around the candle"

    assert session.turn(command)[-1].text == reply
    parse.assert_called_once()
    complete.assert_called_once()
    payload = json.loads(complete.call_args.args[1])
    assert payload["attempt"] == command and payload["subject"] == "candle"
    assert payload["held_tool"] is None and payload["player"] == session.game.character.description


@pytest.mark.parametrize("tool", ["sword", "shelf"])
def test_tool_in_the_room_must_be_carried_first(monkeypatch, tool):
    session = GameSession()
    complete = Mock(side_effect=AssertionError("A tool in the room must be carried first."))
    monkeypatch.setattr("environment_actions.complete", complete)

    assert session.turn(f"use {tool} on candle")[-1].text == f"You need to be carrying the {tool} first."
    complete.assert_not_called()


@pytest.mark.parametrize(("item", "command", "authored", "reply"), [
    ("candle", "tuck the candle into my braid", "You lift the candle. Its flame throws your shadow across the room.",
     "The flame would singe your braid."),
    ("silver shield", "balance the shield on my hand", "You raise the silver shield. It might protect you in battle.",
     "The shield tips to one side."),
    ("torch", "hold the torch behind my back", "The torch flares brightly, pushing shadows to the walls.",
     "You feel the torch's heat against your back."),
])
def test_carried_items_keep_bare_use_and_adjudicate_described_use(monkeypatch, item, command, authored, reply):
    session = GameSession()
    session.game.character = make("girl")
    if item == "candle":
        session.turn("take candle")
    else:
        session.game.inventory.append(Item(item, "An item from the castle.", usable=True))
    parse = Mock(return_value=json.dumps({"action": "use", "target": item}))
    complete = Mock(return_value=json.dumps({"effect": "observe", "anchor": item, "reply": reply}))
    monkeypatch.setattr("command_parser.complete", parse)
    monkeypatch.setattr("environment_actions.complete", complete)

    assert session.turn(f"use {item}")[-1].text == authored
    parse.assert_not_called()
    complete.assert_not_called()
    assert session.turn(command)[-1].text == reply
    parse.assert_called_once()
    complete.assert_called_once()
    payload = json.loads(complete.call_args.args[1])
    assert payload["attempt"] == command and payload["subject"] == item


@pytest.mark.parametrize(("options", "expected"), [
    ({}, STOCK),
    ({"character": "girl"}, make("girl")),
    ({"character_description": "You wear a green coat."}, make(description="You wear a green coat.")),
    ({"character": "girl", "character_description": "You wear a blue coat."}, make("girl", "You wear a blue coat.")),
])
def test_piped_game_uses_flags_or_stock_without_prompting(tmp_path, monkeypatch, capsys, options, expected):
    monkeypatch.setattr(sys, "stdin", io.StringIO("look at yourself\n"))
    main.main(**options)
    output = capsys.readouterr().out
    assert "Who enters the castle?" not in output
    assert f"You look yourself over. {expected.description}" in output
    slot = SaveSlot.find(tmp_path / "saves")
    resumed, journal = slot.load()
    assert resumed.game.character == expected and [turn["command"] for turn in journal] == ["look at yourself"]


@pytest.mark.parametrize("options", [
    {"resume": True, "character": "girl"},
    {"load": "earlier", "character_description": ""},
])
def test_resumed_game_rejects_character_flags(options):
    with pytest.raises(SystemExit, match=r"^A resumed game keeps its character\.$"):
        main.main(**options)


def test_invalid_description_flag_reports_the_validation_error():
    with pytest.raises(SystemExit, match=r"^Enter a character description\.$"):
        main.main(character_description="  ")


def test_tty_reasks_for_an_invalid_choice_and_description(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setitem(sys.modules, "readline", None)
    answers = Mock(side_effect=["later", "2", "x" * 601, "You have short hair.", "look at yourself", EOFError()])
    monkeypatch.setattr("builtins.input", answers)
    main.main()
    output = capsys.readouterr().out
    assert output.index("Who enters the castle?") < output.index("Welcome to The Giant's Drink!")
    assert "Choose 1 or 2." in output and "600 characters or fewer." in output
    assert "You look yourself over. You have short hair." in output
    assert answers.call_args_list[0] == answers.call_args_list[1]


@pytest.mark.parametrize("readline", [None, SimpleNamespace(__doc__="Command line editing using libedit readline.")])
def test_tty_defaults_to_the_stock_description_without_prefill(monkeypatch, capsys, readline):
    monkeypatch.setitem(sys.modules, "readline", readline)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", Mock(return_value=""))
    assert main._choose_character() == STOCK
    assert capsys.readouterr().out.splitlines()[-1] == STOCK.description


@pytest.mark.parametrize("interrupt", [EOFError, KeyboardInterrupt])
def test_tty_character_prompt_exits_cleanly(monkeypatch, capsys, interrupt):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", Mock(side_effect=interrupt))
    main.main()
    output = capsys.readouterr().out
    assert "Thanks for playing The Giant's Drink!" in output and "Welcome" not in output


def test_readline_prefills_and_clears_the_hook_on_interrupt(monkeypatch):
    readline = SimpleNamespace(insert_text=Mock(), set_startup_hook=Mock())
    monkeypatch.setitem(sys.modules, "readline", readline)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    def interrupt(prompt):
        readline.set_startup_hook.call_args.args[0]()
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    with pytest.raises(KeyboardInterrupt):
        main._edit_description(STOCK.description)
    readline.insert_text.assert_called_once_with(STOCK.description)
    readline.set_startup_hook.assert_called_with()


def test_browser_chooses_only_for_new_games_without_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", Mock(side_effect=AssertionError("browser prompted in the terminal")))
    start = Mock()
    monkeypatch.setattr(main, "play_in_browser", start)
    saved = GameSession()
    saved.game.character = make("girl", "You wear a red cloak.")
    SaveSlot.new(tmp_path / "saves").save(saved, [])

    for options, expected, choose in [
        ({}, STOCK, True),
        ({"character": "girl"}, make("girl"), False),
        ({"character_description": "You wear a green coat."}, make(description="You wear a green coat."), False),
        ({"resume": True}, saved.game.character, False),
    ]:
        main.main(browser=True, **options)
        assert start.call_args.args[0].game.character == expected
        assert start.call_args.kwargs["choose_character"] is choose
