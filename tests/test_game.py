# tests/test_game.py
import contextlib
import copy
import io
import json
from unittest.mock import patch

import pytest
import requests
import responses

from adaptation_ai import AdaptationAI
from behavior_analyzer import BehaviorAnalyzer
from command_parser import command_cache, parse_command
from content_generator import ContentGenerator
from game_world import world_map
from simulation_engine import GameState


@pytest.fixture
def game():
    return GameState()


@pytest.fixture
def wmap():
    """Deep-copy the world map so tests don't mutate shared state."""
    return copy.deepcopy(world_map)


# ── Movement ─────────────────────────────────────────────────────────


class TestMovement:
    def test_move_north_from_start(self, game, wmap):
        result = game.move("north", wmap)
        assert game.current_room == "Corridor"
        assert "Corridor" in result

    def test_move_invalid_direction(self, game, wmap):
        result = game.move("west", wmap)
        assert game.current_room == "Starting Room"
        assert "can't" in result.lower()

    def test_move_through_multiple_rooms(self, game, wmap):
        game.move("north", wmap)  # -> Corridor
        game.move("east", wmap)  # -> Treasure Room
        assert game.current_room == "Treasure Room"
        game.move("west", wmap)  # -> Corridor
        assert game.current_room == "Corridor"

    def test_move_to_locked_victory_chamber(self, game, wmap):
        game.current_room = "Throne Antechamber"
        result = game.move("north", wmap)
        assert game.current_room == "Throne Antechamber"
        assert "locked" in result.lower()

    def test_move_to_victory_chamber_when_unlocked(self, game, wmap):
        game.current_room = "Throne Antechamber"
        game.door_unlocked = True
        game.move("north", wmap)
        assert game.current_room == "Victory Chamber"


# ── Inventory ────────────────────────────────────────────────────────


class TestInventory:
    def test_take_item(self, game, wmap):
        result = game.take_item("rusty sword", wmap)
        assert "rusty sword" in result.lower()
        assert len(game.inventory) == 1
        assert game.inventory[0].name == "rusty sword"

    def test_take_nonexistent_item(self, game, wmap):
        result = game.take_item("golden axe", wmap)
        assert "no such item" in result.lower()
        assert len(game.inventory) == 0

    def test_item_removed_from_room_after_take(self, game, wmap):
        game.take_item("rusty sword", wmap)
        assert len(wmap["Starting Room"].items) == 0

    def test_take_item_in_treasure_room(self, game, wmap):
        game.current_room = "Treasure Room"
        result = game.take_item("magic potion", wmap)
        assert "magic potion" in result.lower()
        assert len(game.inventory) == 1


# ── Item Use ─────────────────────────────────────────────────────────


class TestItemUse:
    def test_use_potion(self, game, wmap):
        game.current_room = "Treasure Room"
        game.take_item("magic potion", wmap)
        game.use_item("potion")
        assert game.potion_active is True
        assert len(game.inventory) == 0

    def test_use_book(self, game, wmap):
        game.current_room = "Library"
        game.take_item("ancient book", wmap)
        game.use_item("book")
        assert game.book_knowledge is True

    def test_use_key_in_antechamber(self, game, wmap):
        game.current_room = "Giant's Hall"
        game.take_item("key", wmap)
        game.current_room = "Throne Antechamber"
        game.use_item("key")
        assert game.door_unlocked is True

    def test_use_key_wrong_room(self, game, wmap):
        game.current_room = "Giant's Hall"
        game.take_item("key", wmap)
        game.current_room = "Library"
        game.use_item("key")
        assert game.door_unlocked is False

    def test_use_nonexistent_item(self, game, wmap):
        result = game.use_item("banana")
        assert "can't use" in result.lower()


# ── Combat ───────────────────────────────────────────────────────────


class TestCombat:
    def test_attack_giant_with_sword_normal_difficulty(self, game, wmap):
        game.take_item("rusty sword", wmap)
        game.current_room = "Giant's Hall"
        game.attack("giant", difficulty=1.0)
        assert game.giant_defeated is True

    def test_attack_giant_without_sword(self, game, wmap):
        game.current_room = "Giant's Hall"
        result = game.attack("giant", difficulty=1.0)
        assert game.giant_defeated is False
        assert "fail" in result.lower()

    def test_attack_giant_high_difficulty_no_potion(self, game, wmap):
        game.take_item("rusty sword", wmap)
        game.current_room = "Giant's Hall"
        result = game.attack("giant", difficulty=1.5)
        assert game.giant_defeated is False
        assert "potion" in result.lower()

    def test_attack_giant_high_difficulty_with_potion(self, game, wmap):
        game.take_item("rusty sword", wmap)
        game.current_room = "Giant's Hall"
        game.potion_active = True
        game.attack("giant", difficulty=1.5)
        assert game.giant_defeated is True

    def test_attack_giant_low_difficulty(self, game, wmap):
        game.take_item("rusty sword", wmap)
        game.current_room = "Giant's Hall"
        game.attack("giant", difficulty=0.5)
        assert game.giant_defeated is True

    def test_attack_nothing(self, game, wmap):
        result = game.attack("giant", difficulty=1.0)
        assert "nothing to attack" in result.lower()

    def test_crystal_vial_defeats_giant(self, game, wmap):
        game.current_room = "Hidden Study"
        game.take_item("crystal vial", wmap)
        game.current_room = "Giant's Hall"
        game.use_item("crystal vial")
        assert game.giant_defeated is True

    def test_strategist_path(self, game, wmap):
        game.take_item("rusty sword", wmap)
        game.current_room = "Library"
        game.take_item("ancient book", wmap)
        game.use_item("book")
        game.current_room = "Treasure Room"
        game.take_item("magic potion", wmap)
        game.use_item("potion")
        game.current_room = "Giant's Hall"
        game.attack("giant", difficulty=1.0)
        assert game.giant_defeated is True


# ── Behavior Analyzer ──────────────────────────────────────────────


class TestBehaviorAnalyzer:
    def test_update_profile_move(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("move")
        assert ba.profile["exploration"] == 1

    def test_update_profile_take(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("take")
        assert ba.profile["strategy"] == 1

    def test_update_profile_use(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("use")
        assert ba.profile["strategy"] == 1

    def test_update_profile_attack(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("attack")
        assert ba.profile["aggression"] == 1

    def test_get_action_ratios(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("move")
        ba.update_profile("move")
        ba.update_profile("attack")
        ba.update_profile("use")
        ratios = ba.get_action_ratios()
        assert ratios["explore"] == pytest.approx(0.5)
        assert ratios["attack"] == pytest.approx(0.25)
        assert ratios["use"] == pytest.approx(0.25)

    def test_get_action_ratios_empty(self):
        ba = BehaviorAnalyzer()
        ratios = ba.get_action_ratios()
        assert ratios == {"explore": 0.0, "attack": 0.0, "use": 0.0}

    def test_get_profile_summary_explorer(self):
        ba = BehaviorAnalyzer()
        for _ in range(6):
            ba.update_profile("move")
        ba.update_profile("take")
        summary = ba.get_profile_summary()
        assert "explorer" in summary.lower()

    def test_get_profile_summary_aggressor(self):
        ba = BehaviorAnalyzer()
        for _ in range(5):
            ba.update_profile("attack")
        ba.update_profile("move")
        summary = ba.get_profile_summary()
        assert "aggress" in summary.lower() or "fighter" in summary.lower()

    def test_get_profile_summary_strategist(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("take")
        ba.update_profile("use")
        ba.mark_book_read()
        ba.update_profile("attack")
        summary = ba.get_profile_summary()
        assert "strategist" in summary.lower() or "prepares" in summary.lower()

    def test_mark_book_read(self):
        ba = BehaviorAnalyzer()
        assert ba.read_book is False
        ba.mark_book_read()
        assert ba.read_book is True

    def test_read_book_before_attack_true(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("use")
        ba.mark_book_read()
        ba.update_profile("attack")
        assert ba.read_book_before_attack() is True

    def test_read_book_before_attack_false(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("attack")
        ba.update_profile("use")
        assert ba.read_book_before_attack() is False


# ── Adaptation AI ──────────────────────────────────────────────────


class TestAdaptationAI:
    def test_adjust_difficulty_increases_on_aggression(self):
        ai = AdaptationAI()
        profile = {"exploration": 0, "aggression": 6, "strategy": 0}
        ai.adjust_difficulty(profile)
        assert ai.get_difficulty() == 1.5

    def test_adjust_difficulty_decreases_on_exploration(self):
        ai = AdaptationAI()
        profile = {"exploration": 0, "aggression": 0, "strategy": 6}
        ai.adjust_difficulty(profile)
        assert ai.get_difficulty() == 0.8

    def test_get_difficulty_default(self):
        ai = AdaptationAI()
        assert ai.get_difficulty() == 1.0

    def test_adapt_world_blocks_corridor_for_aggressor(self):
        ai = AdaptationAI()
        wmap = copy.deepcopy(world_map)
        profile = {"exploration": 0, "aggression": 4, "strategy": 0}
        ai.adapt_world(wmap, profile, "aggressive fighter")
        assert "north" not in wmap["Corridor"].exits
        assert ai._corridor_blocked is True

    def test_adapt_world_adds_hidden_passage_for_explorer(self):
        ai = AdaptationAI()
        wmap = copy.deepcopy(world_map)
        profile = {"exploration": 5, "aggression": 0, "strategy": 0}
        ai.adapt_world(wmap, profile, "cautious explorer")
        assert wmap["Library"].exits.get("west") == "Hidden Study"
        assert wmap["Hidden Study"].exits.get("east") == "Library"
        assert ai._hidden_passage_revealed is True


# ── Command Parser (mocked) ───────────────────────────────────────


OPENAI_URL = "https://api.openai.com/v1/chat/completions"


@pytest.fixture
def openai_api(monkeypatch, tmp_path):
    import main

    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)
    command_cache.clear()
    with responses.RequestsMock() as api:
        yield api


def completion(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class TestCommandParser:
    def test_parse_command_valid(self, openai_api):
        openai_api.post(OPENAI_URL, json=completion('{"action": "move", "target": "north"}'))
        assert parse_command("go north") == ("move", "north")

        request = openai_api.calls[0].request
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.body)
        assert payload["model"] == "gpt-5.4-mini"
        assert "reasoning_effort" not in payload
        assert payload["max_completion_tokens"] == 100
        assert payload["messages"][0]["role"] == "developer"
        assert payload["messages"][1] == {"role": "user", "content": "go north"}
        assert payload["response_format"] == {"type": "json_object"}

    def test_cache_hit(self, openai_api):
        openai_api.post(OPENAI_URL, json=completion('{"action": "take", "target": "rusty sword"}'))
        parse_command("take sword")
        parse_command("take sword")
        assert len(openai_api.calls) == 1

    def test_api_error_can_be_retried(self, openai_api):
        openai_api.post(OPENAI_URL, status=429, json={"error": {"message": "Rate limited"}})
        openai_api.post(OPENAI_URL, json=completion('{"action": "move", "target": "north"}'))
        assert parse_command("go north") == (None, None)
        assert parse_command("go north") == ("move", "north")

    @pytest.mark.parametrize("text", ["not json", '{"action": [], "target": "giant"}', None])
    def test_invalid_output(self, openai_api, text):
        openai_api.post(OPENAI_URL, json=completion(text))
        assert parse_command("gibberish xyz") == (None, None)


class TestContentGenerator:
    def test_generate_description_success(self, openai_api, monkeypatch):
        monkeypatch.setenv("OPENAI_MODEL", "custom-model")
        openai_api.post(OPENAI_URL, json=completion("A dark and eerie room."))
        cg = ContentGenerator()
        result = cg.generate_description(
            "Starting Room", {}, description="static desc", items=["rusty sword"], profile_summary="explorer"
        )
        assert result == "A dark and eerie room."
        assert cg.generate_description("Starting Room", {}, profile_summary="explorer") == result
        assert len(openai_api.calls) == 1
        payload = json.loads(openai_api.calls[0].request.body)
        assert payload["model"] == "custom-model"
        assert "response_format" not in payload
        assert "rusty sword" in payload["messages"][1]["content"]
        assert "explorer" in payload["messages"][1]["content"]

    @pytest.mark.parametrize("reply", [{"status": 503}, {"body": requests.Timeout()}, {"json": {"choices": []}}])
    def test_generate_description_fallback_on_error(self, openai_api, reply):
        openai_api.post(OPENAI_URL, **reply)
        cg = ContentGenerator()
        assert cg.generate_description("Starting Room", {}, description="static desc") == "static desc"


def test_missing_api_key(openai_api, monkeypatch):
    import main

    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        main.main()
    assert parse_command("go north") == (None, None)
    assert ContentGenerator().generate_description("Library", {}) == "You are in the Library."
    assert not openai_api.calls


@pytest.mark.parametrize("effort", ["", "none", "low", "medium", "high", "xhigh", "max"])
def test_play_with_dotenv_settings(openai_api, monkeypatch, tmp_path, effort):
    monkeypatch.delenv("OPENAI_API_KEY")
    (tmp_path / ".env").write_text(
        f"OPENAI_API_KEY=dotenv-test-key\nOPENAI_MODEL=gpt-6-luna\nOPENAI_REASONING_EFFORT={effort}\n"
    )
    with patch.dict("os.environ"):
        output = _run_game(openai_api, ["go north", "quit"], [("move", "north"), ("quit", "game")])
    assert "Thanks for playing" in output
    assert len(openai_api.calls) == 3  # Move, narrate the room, quit.
    for call in openai_api.calls:
        assert call.request.headers["Authorization"] == "Bearer dotenv-test-key"
        payload = json.loads(call.request.body)
        assert payload["model"] == "gpt-6-luna"
        if effort:
            assert payload["reasoning_effort"] == effort
        else:
            assert "reasoning_effort" not in payload
        assert (payload["max_completion_tokens"] > 200) == (effort != "none")


# ── End-to-End Game Loop ──────────────────────────────────────────


def _run_game(openai_api, inputs, commands):
    """Run the game loop with scripted OpenAI replies at the HTTP boundary."""
    import main

    replies = dict(zip(inputs, commands))

    def respond(request):
        payload = json.loads(request.body)
        if "response_format" in payload:
            reply = replies[payload["messages"][1]["content"]]
            text = json.dumps({"action": reply[0], "target": reply[1]}) if reply else "???"
        else:
            text = "You stand in a room."
        return 200, {}, json.dumps(completion(text))

    openai_api.add_callback(responses.POST, OPENAI_URL, callback=respond, content_type="application/json")
    buf = io.StringIO()
    with (
        patch("builtins.input", side_effect=inputs),
        patch.object(main, "world_map", copy.deepcopy(world_map)),
        contextlib.redirect_stdout(buf),
    ):
        main.main()
    return buf.getvalue()


class TestEndToEnd:
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
            "go up",
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
        output = _run_game(openai_api, inputs, cmds)
        assert "defeated" in output.lower() or "giant falls" in output.lower()

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
            "go up",
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
        output = _run_game(openai_api, inputs, cmds)
        assert "defeated" in output.lower() or "topples" in output.lower()

    def test_explorer_path(self, openai_api):
        """Navigate to Hidden Study → take crystal vial → use crystal vial in Giant's Hall."""
        inputs = [
            "go north",
            "go west",
            "head north",
            "go east",
            "head east",
            "go up",
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
        output = _run_game(openai_api, inputs, cmds)
        assert "crumbles" in output.lower() or "defeated" in output.lower()

    def test_attack_without_sword(self, openai_api):
        """Attack giant without sword → giant_defeated stays False."""
        inputs = ["go north", "head north", "go up", "walk north", "attack giant", "quit"]
        cmds = [
            ("move", "north"),
            ("move", "north"),
            ("move", "up"),
            ("move", "north"),
            ("attack", "giant"),
            ("quit", "game"),
        ]
        output = _run_game(openai_api, inputs, cmds)
        assert "fail" in output.lower() or "without a weapon" in output.lower()
        assert "congratulations" not in output.lower()

    def test_quit(self, openai_api):
        """Quit command exits cleanly."""
        output = _run_game(openai_api, ["quit"], [("quit", "game")])
        assert "thanks for playing" in output.lower()

    def test_invalid_command(self, openai_api):
        """Gibberish → fallback message, game continues."""
        output = _run_game(openai_api, ["xyzzy", "quit"], [None, ("quit", "game")])
        assert "do not understand" in output.lower()
        assert "thanks for playing" in output.lower()
