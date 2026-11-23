import contextlib
import io
import json
import os
from unittest.mock import patch

import pytest

from command_parser import parse_command
from content_generator import ContentGenerator
from tests.support.cli import run_game


def test_missing_api_key(openai_api, monkeypatch):
    import main

    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        main.main()
    events = []
    assert parse_command("head towards the north", report=events.append) == (None, None)
    assert ContentGenerator().generate_description("Library", {}, report=events.append) == "You are in the Library."
    assert events == ["[Procedural] Using the written room description."]
    assert not openai_api.calls


@pytest.mark.parametrize("effort", ["", "none", "low", "medium", "high", "xhigh", "max"])
def test_play_with_dotenv_settings(openai_api, monkeypatch, tmp_path, effort):
    monkeypatch.delenv("OPENAI_API_KEY")
    (tmp_path / ".env").write_text(
        f"OPENAI_API_KEY=dotenv-test-key\nOPENAI_MODEL=gpt-6-luna\nOPENAI_REASONING_EFFORT={effort}\n"
    )
    with patch.dict("os.environ"):
        output = run_game(openai_api, ["head north, please", "time to leave"], [("move", "north"), ("quit", "game")])
    assert "Thanks for playing" in output
    assert len(openai_api.calls) == 3  # Move, narrate the room, quit.
    assert output.count("[AI] Calling gpt-6-luna") == len(openai_api.calls)
    for call in openai_api.calls:
        assert call.request.headers["Authorization"] == "Bearer dotenv-test-key"
        payload = json.loads(call.request.body)
        assert payload["model"] == "gpt-6-luna"
        if effort:
            assert payload["reasoning_effort"] == effort
        else:
            assert "reasoning_effort" not in payload
        assert (payload["max_completion_tokens"] > 200) == (effort != "none")


def test_queued_input_survives_interrupted_narration(monkeypatch):
    import main

    termios = pytest.importorskip("termios")
    master, slave = os.openpty()
    with os.fdopen(master, "wb", buffering=0) as keyboard, os.fdopen(slave, "r") as terminal:
        monkeypatch.setattr(main.sys, "stdin", terminal)
        original = termios.tcgetattr(slave)
        with pytest.raises(KeyboardInterrupt), main._quiet_typeahead():
            assert not termios.tcgetattr(slave)[3] & termios.ECHO
            keyboard.write(b"inventory\n")
            raise KeyboardInterrupt
        assert termios.tcgetattr(slave) == original
        assert terminal.readline() == "inventory\n"


class TestEndToEnd:
    def test_activity_matches_actual_requests(self, openai_api):
        ask = "what is making that light?"
        inputs = ["look", ask, ask, "go north", "go south", "go north", "quit"]
        output = run_game(openai_api, inputs, [None, ("examine", "candle"), ("examine", "candle"), None, None, None, None])
        labels = [line for line in output.splitlines() if line.startswith("[")]
        assert labels == [
            "[Procedural] Opening room.",
            "[Procedural] Resolving look.",
            "[AI] Calling gpt-5.4-mini for command interpretation...",
            "[Procedural] Resolving examine.",
            "[Cached AI] Reusing command interpretation.",
            "[Procedural] Resolving examine.",
            "[Procedural] Resolving move.",
            "[AI] Calling gpt-5.4-mini for room narration...",
            "[Procedural] Resolving move.",
            "[AI] Calling gpt-5.4-mini for room narration...",
            "[Procedural] Resolving move.",
            "[Cached AI] Reusing room narration.",
            "[Procedural] Resolving quit.",
        ]
        assert len(openai_api.calls) == output.count("[AI]")
        assert "You move to the Corridor.\n[AI]" in output

    def test_opening_room_transcript(self, openai_api):
        probe = "use the sword to strike the walls of room to see if any stones are loose"
        inputs = [
            "pick up the sword",
            "examine the sword",
            "examine the sword",
            "examine the candle",
            probe,
            "take the guttering candle",
            "take the candle",
            "look",
            "inventory",
            "go down",
            "go up",
            "examine the wall",
            "use sword on wall",
            "quit",
        ]
        replies = [None] * len(inputs)
        replies[4] = {"action": "use_on", "tool": "rusty sword", "target": "stone wall"}
        output = run_game(openai_api, inputs, replies)
        turns = output.split("Exits: north\n", 1)[1]
        assert turns.count("Rust freckles the blade") == 2
        assert "warm beeswax" in turns
        assert turns.count("hollow knock") == 1
        assert "You take the candle." in turns and "already carrying the candle" in turns
        assert "Inventory: rusty sword, candle" in turns
        assert "A single candle gutters" not in turns
        assert "You move to the Dungeon." in turns and "Exits: north, down" in turns
        assert turns.count("An opening below the shelf") == 3  # Look, inspect, then repeat the interaction.
        assert len(openai_api.calls) == 3  # Interpret the compound action and narrate two room changes.

    def test_aggressor_path(self, openai_api):
        """Take sword → use potion → navigate to Giant's Hall → attack giant (difficulty 1.5)."""
        inputs = [
            "take sword",
            "go north",
            "go east",
            "take potion",
            "use potion",
            "go west",
            "go north again",
            "follow the banister",
            "head north",
            "attack giant",
            "hit giant",
            "strike giant",
            "fight giant",
            "swing at giant",
            "slay giant",
        ]
        cmds = [
            ("take", "rusty sword"),
            ("move", "north"),
            ("move", "east"),
            ("take", "magic potion"),
            ("use", "potion"),
            ("move", "west"),
            ("move", "north"),
            ("move", "up"),
            ("move", "north"),
            ("attack", "giant"),
            ("attack", "giant"),
            ("attack", "giant"),
            ("attack", "giant"),
            ("attack", "giant"),
            ("attack", "giant"),
        ]
        output = run_game(openai_api, inputs, cmds)
        assert "Life 2. You wake at the Giant's Hall." in output
        assert "Life 3. You wake at the Upper Landing." in output
        assert "bargain is broken" not in output

    def test_strategist_path(self, openai_api):
        """Take sword → read book → use potion → attack giant."""
        inputs = [
            "take sword",
            "go north",
            "go west",
            "take book",
            "use book",
            "go east",
            "head east",
            "take potion",
            "use potion",
            "walk west",
            "walk north",
            "follow the banister",
            "head north",
            "attack giant",
        ]
        cmds = [
            ("take", "rusty sword"),
            ("move", "north"),
            ("move", "west"),
            ("take", "ancient book"),
            ("use", "book"),
            ("move", "east"),
            ("move", "east"),
            ("take", "magic potion"),
            ("use", "potion"),
            ("move", "west"),
            ("move", "north"),
            ("move", "up"),
            ("move", "north"),
            ("attack", "giant"),
        ]
        output = run_game(openai_api, inputs, cmds)
        assert "woodcut" in output and "You die." in output
        assert "bargain is broken" not in output

    def test_explorer_path(self, openai_api):
        """Navigate to Hidden Study → take crystal vial → use crystal vial in Giant's Hall."""
        inputs = [
            "go north",
            "go west",
            "head north",
            "go east",
            "head east",
            "follow the banister",
            "walk west",
            "walk north",
            "take crystal vial",
            "go south",
            "walk east",
            "move north",
            "use crystal vial",
        ]
        cmds = [
            ("move", "north"),
            ("move", "west"),
            ("move", "north"),
            ("move", "east"),
            ("move", "east"),
            ("move", "up"),
            ("move", "west"),
            ("move", "north"),
            ("take", "crystal vial"),
            ("move", "south"),
            ("move", "east"),
            ("move", "north"),
            ("use", "crystal vial"),
        ]
        output = run_game(openai_api, inputs, cmds)
        assert "In the crystal vial the cups appear upside down" in output
        assert "bargain is broken" not in output

    def test_attack_without_sword(self, openai_api):
        """Attack giant without sword → giant_defeated stays False."""
        inputs = ["go north", "head north", "follow the banister", "walk north", "attack giant", "quit"]
        cmds = [
            ("move", "north"),
            ("move", "north"),
            ("move", "up"),
            ("move", "north"),
            ("attack", "giant"),
            ("quit", "game"),
        ]
        output = run_game(openai_api, inputs, cmds)
        assert "Life 2. You wake at the Giant's Hall." in output
        assert "bargain is broken" not in output

    def test_quit(self, openai_api):
        """Quit command exits cleanly."""
        with patch("builtins.input", return_value="quit"), contextlib.redirect_stdout(io.StringIO()) as buf:
            import main

            main.main()
        output = buf.getvalue()
        assert not openai_api.calls
        assert "thanks for playing" in output.lower()

    def test_invalid_command(self, openai_api):
        """Gibberish → fallback message, game continues."""
        output = run_game(openai_api, ["xyzzy", "quit"], [None, ("quit", "game")])
        assert "do not understand" in output.lower()
        assert "thanks for playing" in output.lower()
