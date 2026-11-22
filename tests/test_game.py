# tests/test_game.py
import contextlib
import copy
import io
import json
import os
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
        assert [item.name for item in wmap["Starting Room"].items] == ["candle"]

    def test_take_item_in_treasure_room(self, game, wmap):
        game.current_room = "Treasure Room"
        result = game.take_item("magic potion", wmap)
        assert "magic potion" in result.lower()
        assert len(game.inventory) == 1


# ── Item Use ─────────────────────────────────────────────────────────


class TestItemUse:
    def test_probe_requires_a_carried_tool_and_local_target(self, game, wmap):
        start = wmap["Starting Room"]
        assert "carrying" in game.use_on("sword", "walls", wmap)
        assert "down" not in start.exits
        game.take_item("sword", wmap)
        assert "no telescope" in game.use_on("sword", "telescope", wmap)
        assert "Nothing happens" in game.use_on("sword", "shelf", wmap)
        game.move("north", wmap)
        assert "no iron rings" in game.use_on("sword", "iron rings", wmap)
        assert "down" not in start.exits

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
        from saga import Saga

        Saga().begin(wmap, "You passed through the silver.")
        game.current_room = "Giant's Hall"
        game.take_item("key", wmap)
        game.current_room = "Throne Antechamber"
        game.use_item("key")
        assert game.door_unlocked is True

    def test_use_key_wrong_room(self, game, wmap):
        from saga import Saga

        Saga().begin(wmap, "You passed through the silver.")
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
    @pytest.mark.parametrize("difficulty,potion,book", [(0.5, False, False), (1.0, False, False),
                                                      (1.5, False, False), (1.5, True, False), (1.0, True, True)])
    def test_direct_attack_costs_a_life_even_with_equipment(self, game, wmap, difficulty, potion, book):
        game.take_item("rusty sword", wmap)
        game.current_room = "Giant's Hall"
        game.potion_active, game.book_knowledge = potion, book
        assert "You die." in game.attack("giant", difficulty, world_map=wmap)
        assert not game.giant_defeated and game.deaths == 1 and not game.potion_active
        assert game.book_knowledge == book and game.inventory[0].name == "rusty sword"

    def test_attack_nothing(self, game):
        assert "nothing to attack" in game.attack("giant").lower()
        assert game.deaths == 0

    def test_crystal_vial_offers_a_clue(self, game, wmap):
        game.current_room = "Hidden Study"
        game.take_item("crystal vial", wmap)
        game.current_room = "Giant's Hall"
        assert "upside down" in game.use_item("crystal vial")
        assert not game.giant_defeated and game.inventory[0].name == "crystal vial"


# ── Behavior Analyzer ──────────────────────────────────────────────


class TestBehaviorAnalyzer:
    def test_discovery_counts_as_exploration(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("discover")
        assert ba.profile["exploration"] == 1
        assert ba.get_action_ratios()["explore"] == 1

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
    def test_model_sees_current_scene_and_rechecks_after_pickup(self, openai_api, game, wmap):
        room = wmap["Starting Room"]
        openai_api.post(OPENAI_URL, json=completion('{"action":"examine","target":"shelf"}'))
        openai_api.post(OPENAI_URL, json=completion('{"action":"examine","target":"candle"}'))
        command = "what am I looking at?"
        assert parse_command(command, room, game.inventory) == ("examine", "shelf")
        game.take_item("candle", wmap)
        assert parse_command(command, room, game.inventory) == ("examine", "candle")
        instructions = json.loads(openai_api.calls[1].request.body)["messages"][0]["content"]
        assert '"carrying": ["candle"]' in instructions and '"items": ["rusty sword"]' in instructions

    def test_parse_command_valid(self, openai_api):
        openai_api.post(OPENAI_URL, json=completion('{"action": "move", "target": "north"}'))
        assert parse_command("head towards the north") == ("move", "north")

        request = openai_api.calls[0].request
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.body)
        assert payload["model"] == "gpt-5.4-mini"
        assert "reasoning_effort" not in payload
        assert payload["max_completion_tokens"] == 100
        assert payload["messages"][0]["role"] == "developer"
        assert payload["messages"][1] == {"role": "user", "content": "head towards the north"}
        assert payload["response_format"] == {"type": "json_object"}

    def test_cache_hit(self, openai_api):
        openai_api.post(OPENAI_URL, json=completion('{"action": "take", "target": "rusty sword"}'))
        parse_command("take sword")
        parse_command("take sword")
        assert len(openai_api.calls) == 1

    def test_api_error_can_be_retried(self, openai_api):
        openai_api.post(OPENAI_URL, status=429, json={"error": {"message": "Rate limited"}})
        openai_api.post(OPENAI_URL, json=completion('{"action": "move", "target": "north"}'))
        assert parse_command("head towards the north") == (None, None)
        assert parse_command("head towards the north") == ("move", "north")

    @pytest.mark.parametrize(
        "text", ["not json", '{"action": [], "target": "giant"}', '{"action":"use_on","target":"wall"}', None]
    )
    def test_invalid_output(self, openai_api, text):
        openai_api.post(OPENAI_URL, json=completion(text))
        assert parse_command("gibberish xyz") == (None, None)


class TestContentGenerator:
    def test_scene_change_invalidates_cached_narration(self, openai_api, game, wmap):
        cg = ContentGenerator()
        room = wmap["Starting Room"]
        openai_api.post(OPENAI_URL, json=completion("Wax drips from a lit candle."))
        openai_api.post(OPENAI_URL, json=completion("The bare shelf casts a long shadow."))
        assert "candle" in cg.generate_description(room.name, {}, room.prose(), ["rusty sword", "candle"])
        game.take_item("candle", wmap)
        assert "bare shelf" in cg.generate_description(room.name, {}, room.prose(), ["rusty sword"])
        assert len(openai_api.calls) == 2

    def test_generate_description_success(self, openai_api, monkeypatch):
        monkeypatch.setenv("OPENAI_MODEL", "custom-model")
        openai_api.post(OPENAI_URL, json=completion("A dark and eerie room."))
        cg = ContentGenerator()
        result = cg.generate_description(
            "Starting Room", {}, description="static desc", items=["rusty sword"], profile_summary="explorer"
        )
        assert result == "A dark and eerie room."
        assert cg.generate_description("Starting Room", {}, "static desc", ["rusty sword"], "explorer") == result
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
        events = []
        assert cg.generate_description("Starting Room", {}, description="static desc", report=events.append) == "static desc"
        assert events == [
            "[AI] Calling gpt-5.4-mini for room narration...",
            "[Procedural] Using the written room description.",
        ]


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
        output = _run_game(openai_api, ["head north, please", "time to leave"], [("move", "north"), ("quit", "game")])
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


# ── End-to-End Game Loop ──────────────────────────────────────────


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


def arc_plan(prefix="", horizon="Beyond the pool, a stair of cloud climbs toward a dark lighthouse."):
    """A small, valid arc: three rooms, a bell placed before a glass heron that bars the way north."""
    return {
        "title": f"{prefix}The Glass Orchard",
        "premise": "Glass trees chime in a wind you cannot feel. Something at the orchard's heart will not let you pass.",
        "start": f"{prefix}Orchard Gate",
        "rooms": [
            {"name": f"{prefix}Orchard Gate", "description": "Glass trees ring a cold clearing. A path winds north.",
             "exits": {"north": f"{prefix}Chiming Grove"}, "items": [],
             "scenery": [{"name": "glass trees", "description": "Their leaves chime faintly."}]},
            {"name": f"{prefix}Chiming Grove", "description": "Glass trees crowd close and chime. A path leads east.",
             "exits": {"east": f"{prefix}Still Pool"},
             "items": [{"name": f"{prefix}silver bell".strip(), "description": "A small bell with a clear tone.",
                        "room_text": "A silver bell hangs from a low glass branch."}],
             "scenery": []},
            {"name": f"{prefix}Still Pool", "description": "A pool lies perfectly still beneath a hush of glass leaves.",
             "exits": {}, "items": [],
             "scenery": [{"name": "glass heron", "description": "A glass heron stands with its beak across the path."}]},
        ],
        "challenge": {
            "room": f"{prefix}Still Pool", "obstacle": "glass heron", "blocks": "north",
            "barrier": "A glass heron stands across the northern path and will not move.",
            "solutions": [{"tool": f"{prefix}silver bell".strip(), "approach": "ring a matching note",
                           "outcome": "The heron answers the bell's note and steps aside."}],
            "room_after": "A pool lies still beneath glass leaves. The northern path is clear.",
            "obstacle_after": "The glass heron stands aside, beak raised.",
        },
        "horizon": horizon,
    }


def expanded_arc_plan(prefix="", **kwargs):
    plan = arc_plan(prefix, **kwargs)
    hub, grove, pool = plan["rooms"]
    bell = grove["items"].pop()
    hub["exits"].update(east=prefix + "Reed Bank", west=prefix + "Weather Porch")
    grove["exits"].clear()
    grove["description"] = "A glass shutter catches the sound from the trees."
    grove["scenery"].append({"name": "glass shutter", "description": "A shallow wedge-shaped notch interrupts the frame."})
    plan["rooms"].extend([
        {"name": prefix + "Reed Bank", "description": "Reeds tap a thin rhythm along the bank.",
         "exits": {"north": prefix + "Map Room"},
         "items": [{"name": prefix + "reed wedge", "description": "A wedge rubbed smooth by a glass frame."}]},
        {"name": prefix + "Weather Porch", "description": "A weather dial trembles in an open stone arch.",
         "exits": {}, "scenery": [{"name": "weather dial", "description": "The spindle is missing from its empty hub."}]},
        {"name": prefix + "Map Room", "description": "A map shows the orchard as a collection of musical instruments.",
         "exits": {}, "scenery": [{"name": "map", "description": "The instruments face opposite sides of the orchard."}]},
        {"name": prefix + "Bell Foundry", "description": "A bench stands where metal once ran molten.",
         "exits": {}, "scenery": [{"name": "bench", "description": "Old circular tools have left marks on the wood."}],
         "items": [{"name": prefix + "brass spindle", "description": "A brass shaft etched with the points of a compass."}]},
        {"name": prefix + "Echo Vault", "description": "The air rings with the echo of a single note.",
         "exits": {"east": pool["name"]}, "items": [bell]},
    ])
    for room in plan["rooms"]:
        room.setdefault("items", [])
        room.setdefault("scenery", [])
    plan["passages"] = [
        {"room": grove["name"], "landmark": "glass shutter", "tool": prefix + "reed wedge", "direction": "east",
         "destination": prefix + "Bell Foundry", "reveal": "The shutter slides aside.",
         "visible": "An open shutter leads east."},
        {"room": prefix + "Weather Porch", "landmark": "weather dial", "tool": prefix + "brass spindle",
         "direction": "north", "destination": prefix + "Echo Vault", "reveal": "The dial swings back into the arch.",
         "visible": "The arch opens north."},
    ]
    return plan


# From the Giant's Hall through the linked mechanisms and up to the final obstacle.
EXPLORE_ORCHARD = (
    "go north", "go east", "take reed wedge", "go west", "go north",
    "use reed wedge on glass shutter", "go east", "take brass spindle", "go west", "go south", "go west",
    "use brass spindle on weather dial", "go north", "take silver bell", "go east",
)


def _run_game(openai_api, inputs, commands, plans=()):
    """Run the game loop with scripted OpenAI replies at the HTTP boundary."""
    import main
    import saga

    replies = dict(zip(inputs, commands))
    plans = list(plans) or [expanded_arc_plan()]

    def respond(request):
        payload = json.loads(request.body)
        if payload["messages"][0]["content"] == saga.PROMPT:
            text = json.dumps(plans.pop(0) if len(plans) > 1 else plans[0])
        elif "response_format" in payload:
            reply = replies[payload["messages"][1]["content"]]
            if isinstance(reply, dict):
                text = json.dumps(reply)
            else:
                text = json.dumps({"action": reply[0], "target": reply[1]}) if reply else "???"
        else:
            text = "You stand in a room."
        return 200, {}, json.dumps(completion(text))

    openai_api.add_callback(responses.POST, OPENAI_URL, callback=respond, content_type="application/json")
    buf = io.StringIO()
    with (
        patch("builtins.input", side_effect=[*inputs, EOFError()]),
        patch.object(main, "world_map", copy.deepcopy(world_map)),
        contextlib.redirect_stdout(buf),
    ):
        main.main()
    return buf.getvalue()


class TestEndToEnd:
    def test_activity_matches_actual_requests(self, openai_api):
        ask = "what is making that light?"
        inputs = ["look", ask, ask, "go north", "go south", "go north", "quit"]
        output = _run_game(openai_api, inputs, [None, ("examine", "candle"), ("examine", "candle"), None, None, None, None])
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
        output = _run_game(openai_api, inputs, replies)
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
        output = _run_game(openai_api, inputs, cmds)
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
        output = _run_game(openai_api, inputs, cmds)
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
        output = _run_game(openai_api, inputs, cmds)
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
        output = _run_game(openai_api, inputs, cmds)
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
        output = _run_game(openai_api, ["xyzzy", "quit"], [None, ("quit", "game")])
        assert "do not understand" in output.lower()
        assert "thanks for playing" in output.lower()


# ── Environmental interaction ────────────────────────────────────────


def adjudication(**reply):
    return completion(json.dumps(reply))


def sent_scene(openai_api, index=0):
    return json.loads(json.loads(openai_api.calls[index].request.body)["messages"][1]["content"])


class TestEnvironment:
    def test_piece_of_described_scenery_can_be_taken(self, openai_api, game, wmap):
        game.move("north", wmap)
        corridor = wmap["Corridor"]
        openai_api.post(
            OPENAI_URL,
            json=adjudication(
                effect="detach",
                anchor="tapestries",
                reply="You tear one tapestry from the wall.",
                room_after="A long passage. One tapestry is missing from the walls.",
                description="A gap shows where one tapestry hung.",
                piece="tapestry",
                piece_description="A rolled tapestry of feasting giants.",
            ),
        )
        assert game.investigate("tapestries", wmap, "tear down a tapestry") == "You tear one tapestry from the wall."
        assert [item.name for item in game.inventory] == ["tapestry"]
        assert corridor.prose() == "A long passage. One tapestry is missing from the walls."
        assert sent_scene(openai_api)["fixed_features"].startswith("A long passage stretches")

    def test_worn_items_follow_the_adjudicated_outcome(self, openai_api, game, wmap):
        game.move("north", wmap)
        fixed = wmap["Corridor"].fixed_text()
        cloak = "The tapestry hangs from your shoulders like a cloak."
        openai_api.post(OPENAI_URL, json=adjudication(
            effect="detach", anchor="tapestries", reply="You pull one down and swing it over your shoulders.",
            room_after=fixed, description="One tapestry is missing.", piece="tapestry",
            piece_description="Faded giants feast across the weave.", worn=cloak))
        openai_api.post(OPENAI_URL, json=adjudication(
            effect="observe", anchor="tapestry", reply="The weave smells of dust.", worn="off"))
        openai_api.post(OPENAI_URL, json=adjudication(
            effect="alter", anchor="tapestry", reply="You fold it over your arm.", room_after=fixed,
            description="A folded tapestry.", worn="off"))
        game.investigate("tapestries", wmap, "wear a tapestry as a cloak")
        assert game.worn == {"tapestry": cloak}
        assert game.examine("tapestry", wmap) == f"Faded giants feast across the weave. {cloak}"
        game.investigate("tapestry", wmap, "sniff my cloak")
        assert game.worn == {"tapestry": cloak} and sent_scene(openai_api, 1)["wearing"] == {"tapestry": cloak}
        game.investigate("tapestry", wmap, "fold the cloak over my arm")
        assert game.worn == {}

    def test_anchor_with_descriptive_words_still_grounds_on_the_object(self, openai_api, game, wmap):
        game.move("north", wmap)
        openai_api.post(OPENAI_URL, json=adjudication(
            effect="detach", anchor="Faded tapestries", reply="You drape one over your head like a hood.",
            room_after=wmap["Corridor"].fixed_text(), description="One is missing.", piece="tapestry",
            piece_description="A dusty tapestry.", worn="The tapestry hangs over your head like a hooded cloak."))
        assert game.investigate("tapestries", wmap, "wear a tapestry like a hooded cloak").startswith("You drape")
        assert game.worn == {"tapestry": "The tapestry hangs over your head like a hooded cloak."}

    def test_detail_mentioned_only_in_prose_can_be_taken(self, openai_api, game, wmap):
        for direction in ("north", "east"):
            game.move(direction, wmap)
        treasury = wmap["Treasure Room"]
        openai_api.post(
            OPENAI_URL,
            json=adjudication(
                effect="take",
                anchor="gold coins",
                reply="You scoop up the coins.",
                room_after="Faceted jewels lie across the floor around a marble pedestal.",
                description="A clinking handful of gold coins.",
            ),
        )
        assert game.take_item("gold coins", wmap, command="fill my pockets with gold coins") == "You scoop up the coins."
        assert [item.name for item in game.inventory] == ["gold coins"]
        assert "gold" not in treasury.prose().casefold()
        assert treasury.landmark("gold coins") is None

    def test_invented_or_substituted_subjects_are_rejected(self, openai_api, game, wmap):
        start = wmap["Starting Room"]
        before = start.prose()
        openai_api.post(OPENAI_URL, json=adjudication(effect="take", anchor="golden crown", reply="You take it.",
                                                      room_after="An empty room.", description="A crown."))
        openai_api.post(OPENAI_URL, json=adjudication(effect="take", anchor="candle", reply="You take the candle.",
                                                      room_after="An empty room.", description="A candle."))
        assert game.take_item("golden crown", wmap, command="take the golden crown") == "There is no such item here."
        assert game.investigate("shelf", wmap, "lift the shelf").startswith("A shallow stone ledge")
        assert game.inventory == [] and start.prose() == before

    def test_rewrite_absorbs_discoveries_without_repeating_sentences(self, openai_api, game, wmap):
        dungeon = wmap["Dungeon"]
        game.current_room = "Dungeon"
        assert "Seven tally marks" in game.investigate("stone walls", wmap, None, search=True)
        tally = dungeon.discoveries["stone walls"]
        helm = "An iron helm rests in the corner, forgotten by some earlier prisoner."
        openai_api.post(
            OPENAI_URL,
            json=adjudication(
                effect="alter",
                anchor="overhead grate",
                reply="The grate rattles loose.",
                room_after=f"Damp stone walls press close. The grate above hangs askew. {helm} {tally}",
                description="The grate hangs askew.",
            ),
        )
        assert game.investigate("overhead grate", wmap, "rattle the grate") == "The grate rattles loose."
        scene = dungeon.prose()
        assert scene.count("Seven tally marks") == 1 and scene.count("iron helm rests") == 1
        assert sent_scene(openai_api)["engine_sentences"][0] == helm
        assert game.examine("stone walls", wmap).count("Seven tally marks") == 1

    def test_tool_reveals_a_discovery_once(self, game, wmap):
        game.inventory.append(wmap["Starting Room"].items[0])
        game.current_room = "Dungeon"
        first = game.use_on("sword", "iron rings", wmap)
        assert "three different notes" in first and "tiny bell" in first
        assert game.use_on("sword", "iron rings", wmap) == "A tiny bell is stamped into the middle iron ring."
        assert wmap["Dungeon"].prose().count("tiny bell") == 1

    def test_drop_and_retake(self, game, wmap):
        game.take_item("sword", wmap)
        game.move("north", wmap)
        assert game.drop_item("sword", wmap) == "You set down the rusty sword."
        assert [item.name for item in wmap["Corridor"].items] == ["rusty sword"]
        assert game.drop_item("sword", wmap) == "You're not carrying the sword."
        assert game.take_item("sword", wmap) == "You take the rusty sword."

    def test_attack_accepts_a_described_giant(self, openai_api):
        inputs = ["take sword", "go north", "go north", "follow the banister", "go north", "swing at the sleeping giant"]
        replies = [None] * 5 + [("attack", "the sleeping giant")]
        output = _run_game(openai_api, inputs, replies)
        assert "Life 2. You wake at the Giant's Hall." in output


# ── Room companion ───────────────────────────────────────────────────


def _wait_for(predicate, timeout=5):
    import time

    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


class TestRoomCompanion:
    def test_illustrates_each_distinct_scene(self, monkeypatch, tmp_path, wmap):
        import room_companion

        requests_made = []
        monkeypatch.setattr(
            room_companion, "generate_image", lambda payload: requests_made.append(payload) or b"\xff\xd8\xffart\xff\xd9"
        )
        viewer = room_companion.RoomCompanion(tmp_path, "test-image")
        try:
            room = wmap["Starting Room"]
            viewer.show(room)
            _wait_for(lambda: viewer.snapshot()["status"] == "ready")
            first = viewer.snapshot()
            assert first["image"] == f"/art/{first['key']}.jpg"
            assert (tmp_path / f"{first['key']}.jpg").read_bytes() == b"\xff\xd8\xffart\xff\xd9"
            viewer.show(room)
            room.items.remove(room.items[0])
            viewer.show(room)
            _wait_for(lambda: len(requests_made) == 2 and viewer.snapshot()["status"] == "ready")
            assert "rusty sword" in requests_made[0]["prompt"] and "rusty sword" not in requests_made[1]["prompt"]
            room.items.insert(0, wmap["Starting Room"].items[0])
        finally:
            viewer.close()

    def test_failed_scene_can_be_retried(self, monkeypatch, tmp_path, wmap):
        import room_companion

        outcomes = [RuntimeError("Image request failed (HTTP 429)."), b"\xff\xd8\xffart\xff\xd9"]

        def generate(payload):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(room_companion, "generate_image", generate)
        viewer = room_companion.RoomCompanion(tmp_path, "test-image")
        try:
            viewer.show(wmap["Corridor"])
            _wait_for(lambda: viewer.snapshot()["status"] == "error")
            assert "429" in viewer.snapshot()["error"]
            assert not viewer.retry("stale-key")
            assert viewer.retry(viewer.snapshot()["key"])
            _wait_for(lambda: viewer.snapshot()["status"] == "ready")
        finally:
            viewer.close()


def test_described_use_of_a_plain_item_is_judged(openai_api, game, wmap):
    game.take_item("sword", wmap)
    openai_api.post(OPENAI_URL, json=adjudication(effect="observe", anchor="rusty sword", reply="You test the edge."))
    assert game.use_item("sword", world_map=wmap, command="use the sword") == (
        "You're holding the rusty sword. What would you like to use it on?"
    )
    assert game.use_item("sword", world_map=wmap, command="run my thumb along the blade") == "You test the edge."
    assert len(openai_api.calls) == 1


# ── Endless arcs ─────────────────────────────────────────────────────

TO_THE_GIANT = ["take sword", "go north", "go north", "follow the banister", "go north", "upend the tray", "enter reflection"]


def _arc_world():
    from saga import Saga

    rooms = copy.deepcopy(world_map)
    game = GameState()
    game.current_room = "Giant's Hall"
    saga = Saga()
    saga.begin(rooms, "You attack the giant with the rusty sword and defeat him!")
    return rooms, game, saga


class TestArcs:
    def test_play_continues_through_planned_arcs(self, openai_api):
        inputs = [*TO_THE_GIANT, *EXPLORE_ORCHARD, "go north", "use silver bell on glass heron", "go north", "go south"]
        replies = [None] * len(inputs)
        plans = [expanded_arc_plan(), expanded_arc_plan("Second ", horizon="Further still, a lighthouse shines darkness.")]
        output = _run_game(openai_api, inputs, replies, plans)
        assert "The giant’s bargain is broken." in output
        assert "A way north has opened." in output
        assert "── Arc 1: The Glass Orchard ──" in output and "Glass trees chime" in output
        assert "A glass heron stands across the northern path and will not move." in output
        assert "The heron answers the bell's note and steps aside." in output
        assert "a stair of cloud climbs toward a dark lighthouse" in output
        assert "── Arc 2: Second The Glass Orchard ──" in output
        assert "You move to the Still Pool." in output  # The way back still works.
        assert "Thanks for playing" in output
        planning = [json.loads(call.request.body) for call in openai_api.calls]
        planning = [json.loads(p["messages"][1]["content"]) for p in planning if "planning" in str(p) or
                    p["messages"][0]["content"].startswith("You plan the next arc")]
        assert [p["arc_number"] for p in planning] == [1, 2]
        assert planning[1]["horizon"] == "Beyond the pool, a stair of cloud climbs toward a dark lighthouse."
        assert planning[1]["chronicle"][-1].startswith("The Glass Orchard: The heron answers")
        assert "use silver bell on glass heron" in planning[1]["recent_commands"]
        assert "[Cached AI] Using the planned room description." in output

    def test_plan_builds_a_connected_arc_behind_the_gate(self, openai_api):
        rooms, game, saga = _arc_world()
        openai_api.post(OPENAI_URL, json=completion(json.dumps(expanded_arc_plan())))
        arc = saga.plan_next(rooms, game)
        assert arc.start == "Orchard Gate" and saga.gate is None
        assert rooms["Giant's Hall"].exits["north"] == "Orchard Gate"
        assert rooms["Orchard Gate"].exits == {
            "north": "Chiming Grove", "east": "Reed Bank", "west": "Weather Porch", "south": "Giant's Hall"
        }
        assert rooms["Still Pool"].exits == {"west": "Echo Vault"}
        assert rooms["Chiming Grove"].exits == {"south": "Orchard Gate"}
        assert "dust" in rooms["Giant's Hall"].prose() and "sits upon it" not in rooms["Giant's Hall"].prose()
        request = json.loads(json.loads(openai_api.calls[0].request.body)["messages"][1]["content"])
        assert request["reserved_start_exit"] == "south" and "Giant's Hall" in request["existing_room_names"]

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            (lambda p: p["rooms"][0].update(name="Giant's Hall"), "already used"),
            (lambda p: p["rooms"][0]["exits"].update(south="Still Pool"), "south exit free"),
            (lambda p: p["rooms"][2]["exits"].update(north="Orchard Gate"), "not already an exit"),
            (lambda p: p["challenge"].update(obstacle="glass swan"), "scenery"),
            (lambda p: p["challenge"]["solutions"][0].update(tool="rusty sword"), "placed in this arc"),
            (lambda p: p["challenge"]["solutions"][0].update(tool="moonbeam"), "not an item"),
            (lambda p: p["rooms"][1].update(exits={}), "reachable"),
            (lambda p: p["rooms"].pop(), "3 to 6 rooms"),
        ],
    )
    def test_unplayable_plans_are_rejected(self, change, reason):
        from saga import PlanError, build_arc

        plan = arc_plan()
        change(plan)
        with pytest.raises(PlanError, match=reason):
            build_arc(json.dumps(plan), number=1, reserved="south", world=world_map, carried=["rusty sword"])

    def test_rejected_plan_is_retried_with_the_reason(self, openai_api):
        rooms, game, saga = _arc_world()
        broken = expanded_arc_plan()
        broken["challenge"]["obstacle"] = "glass swan"
        openai_api.post(OPENAI_URL, json=completion(json.dumps(broken)))
        openai_api.post(OPENAI_URL, json=completion(json.dumps(expanded_arc_plan())))
        assert saga.plan_next(rooms, game).title == "The Glass Orchard"
        assert "was rejected: challenge.obstacle" in json.loads(openai_api.calls[1].request.body)["messages"][1]["content"]

    def test_new_generation_retries_a_short_legacy_plan(self, openai_api):
        rooms, game, saga = _arc_world()
        openai_api.post(OPENAI_URL, json=completion(json.dumps(arc_plan())))
        openai_api.post(OPENAI_URL, json=completion(json.dumps(expanded_arc_plan())))
        arc = saga.plan_next(rooms, game)
        assert len(arc.rooms) == 8
        assert "8 to 12 rooms" in json.loads(openai_api.calls[1].request.body)["messages"][1]["content"]

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            (lambda p: p["rooms"][0]["exits"].update(up="Echo Vault"), "must open new rooms"),
            (lambda p: p["rooms"][4]["exits"].update(north="Echo Vault"), "both ends"),
            (lambda p: p["rooms"][5]["items"].append(p["rooms"][6]["items"][0]), "not unique"),
            (lambda p: p["rooms"][5]["items"].append(p["rooms"][6]["items"].pop()), "tool from the previous stage"),
            (lambda p: p["challenge"]["solutions"][0].update(tool="reed wedge"), "beyond the second passage"),
        ],
    )
    def test_expanded_plan_rejects_shortcuts_and_conflicting_exits(self, change, reason):
        from saga import PlanError, build_arc

        plan = expanded_arc_plan()
        change(plan)
        with pytest.raises(PlanError, match=reason):
            build_arc(json.dumps(plan), number=1, reserved="south", world=world_map, require_passages=True)

    def test_fabricated_tool_and_improvisation_cannot_bypass_the_first_passage(self, openai_api):
        from game_world import Item
        from saga import build_arc

        _, rooms = build_arc(json.dumps(expanded_arc_plan()), number=1, reserved="south", world=world_map)
        game = GameState()
        game.current_room = "Chiming Grove"
        game.inventory.append(Item("reed wedge", "A crude imitation."))
        game.use_on("reed wedge", "glass shutter", rooms)
        openai_api.post(OPENAI_URL, json=adjudication(effect="remove", anchor="glass shutter",
                                                      reply="You tear off the shutter.", room_after="The shutter is gone."))
        game.investigate("glass shutter", rooms, "tear off the glass shutter")
        assert rooms["Chiming Grove"].exits == {"south": "Orchard Gate"}
        assert rooms["Chiming Grove"].scenery[0].name == "glass shutter"
        assert "west" not in rooms["Bell Foundry"].exits

    def test_unavailable_planner_leaves_mist_until_the_player_tries_again(self, openai_api):
        inputs = [*TO_THE_GIANT, "go north", "go north"]
        attempts = []

        def respond(request):
            payload = json.loads(request.body)
            if payload["messages"][0]["content"].startswith("You plan the next arc"):
                attempts.append(1)
                if len(attempts) <= 2:
                    return 503, {}, "{}"
                return 200, {}, json.dumps(completion(json.dumps(expanded_arc_plan())))
            if "response_format" in payload:
                return 200, {}, json.dumps(completion(json.dumps({"action": "attack", "target": "giant"})))
            return 200, {}, json.dumps(completion("You stand in a room."))

        import main

        openai_api.add_callback(responses.POST, OPENAI_URL, callback=respond, content_type="application/json")
        buf = io.StringIO()
        with (
            patch("builtins.input", side_effect=[*inputs, EOFError()]),
            patch.object(main, "world_map", copy.deepcopy(world_map)),
            contextlib.redirect_stdout(buf),
        ):
            main.main()
        output = buf.getvalue()
        assert "To the north there is only grey mist so far." in output
        assert "Only grey mist lies that way." in output
        assert "── Arc 1: The Glass Orchard ──" in output and len(attempts) == 3

    def test_unplanned_but_clever_solution_can_overcome_the_obstacle(self, openai_api):
        rooms, game, saga = _arc_world()
        openai_api.post(OPENAI_URL, json=completion(json.dumps(expanded_arc_plan())))
        saga.plan_next(rooms, game)
        game.current_room = "Still Pool"
        pool = rooms["Still Pool"]
        openai_api.post(OPENAI_URL, json=adjudication(effect="overcome", anchor="glass heron",
                                                      reply="You mimic the wind; the heron tilts aside.",
                                                      room_after="The pool is still. The way north is open.",
                                                      description="The heron leans aside."))
        assert game.move("north", rooms).startswith("A glass heron stands across the northern path and will not move.")
        result = game.investigate("glass heron", rooms, "whistle like the wind through glass leaves")
        assert result == "You mimic the wind; the heron tilts aside." and game.breakthrough is pool
        assert pool.prose() == "The pool is still. The way north is open."
        assert game.examine("glass heron", rooms) == "The heron leans aside."
        challenge = json.loads(json.loads(openai_api.calls[1].request.body)["messages"][1]["content"])["challenge"]
        assert challenge["planned_solutions"] == ["ring a matching note with the silver bell"]

    def test_obstacle_cannot_be_carried_off_or_overcome_through_something_else(self, openai_api):
        rooms, game, saga = _arc_world()
        openai_api.post(OPENAI_URL, json=completion(json.dumps(expanded_arc_plan())))
        saga.plan_next(rooms, game)
        game.current_room = "Still Pool"
        openai_api.post(OPENAI_URL, json=adjudication(effect="overcome", anchor="pool", reply="You swim past.",
                                                      room_after="The pool ripples.", description="Rippling."))
        openai_api.post(OPENAI_URL, json=adjudication(effect="take", anchor="glass heron", reply="You lift the heron.",
                                                      room_after="The pool is empty of herons.", description="Light."))
        before = rooms["Still Pool"].prose()
        assert game.investigate("pool", rooms, "swim around the heron").startswith("A pool lies perfectly still")
        assert game.breakthrough is None and rooms["Still Pool"].prose() == before
        assert game.investigate("glass heron", rooms, "pick up the heron") == "You lift the heron."
        assert game.breakthrough is rooms["Still Pool"] and game.inventory == []
        assert "glass heron" in [thing.name for thing in rooms["Still Pool"].scenery]

    def test_defeated_giant_stays_defeated(self, game, wmap):
        game.inventory.append(wmap["Starting Room"].items[0])
        game.current_room = "Giant's Hall"
        table = wmap[game.current_room].trial
        table.resolve("spill both", game, wmap)
        table.resolve("enter reflection", game, wmap)
        assert game.attack("giant") == "The giant is gone. Only a drift of grey dust remains."


def test_items_described_in_room_prose_move_to_their_own_sentence():
    from saga import build_arc

    plan = arc_plan()
    grove = plan["rooms"][1]
    grove["description"] = "Glass trees crowd close. A silver bell hangs from a low branch. A path leads east."
    grove["items"][0].pop("room_text")
    plan["rooms"][0]["description"] += " A silver bell's chime carries from somewhere ahead."
    arc, rooms = build_arc(json.dumps(plan), number=1, reserved="south", world=world_map)
    assert rooms["Chiming Grove"].description == "Glass trees crowd close. A path leads east."
    assert rooms["Chiming Grove"].items[0].room_text == "A silver bell hangs from a low branch."
    assert rooms["Chiming Grove"].prose().count("silver bell") == 1


# ── Naming things that share words ───────────────────────────────────


def test_a_modifier_alone_never_names_a_different_thing():
    from game_world import Item, find_among, find_object

    chip, bell, sword = Item("chip of marble", "A chip."), Item("glass bell", "A bell."), Item("rusty sword", "A blade.")
    frames, marble = Item("empty picture frames", "Frames."), Item("marble", "A marble.")
    assert find_object("marble", [chip]) is None and find_object("glass", [bell]) is None
    assert find_object("chip", [chip]) is chip and find_object("the bell", [bell]) is bell
    assert find_object("sword", [sword]) is sword and find_object("picture frames", [frames]) is frames
    assert find_among("marble", [], [chip], [marble]) is marble  # An exact name anywhere beats the carried chip.
    assert find_among("bell", [Item("brass bell", "B.")], [bell]) .name == "brass bell"  # Nearer group wins a tie.


@pytest.fixture
def gallery():
    from game_world import Item, Room

    room = Room("Weather Gallery", "A long gallery holds a row of empty picture frames. A marble rolls down the "
                "floor and stops beside your foot.", exits={"west": "Rain Landing"},
                scenery=(Item("empty picture frames", "Each frame clouds over as you approach."),
                         Item("marble", "A glass marble with a tiny blue river inside.")))
    game = GameState()
    game.current_room = room.name
    game.inventory = [Item("chip of marble", "A white chip."), Item("glass bell", "A clear bell."),
                      Item("rusty sword", "Rust freckles the blade.")]
    return game, {room.name: room}


def test_taking_the_rolled_marble_does_not_grab_the_carried_chip(openai_api, gallery):
    game, rooms = gallery
    take = adjudication(effect="take", anchor="marble", reply="You pick up the marble.",
                        room_after="A long gallery holds a row of empty picture frames.", description="A glass marble.")
    openai_api.post(OPENAI_URL, json=take)
    assert game.take_item("marble", rooms, command="take marble") == "You pick up the marble."
    assert sent_scene(openai_api)["subject"] == "marble"
    game.drop_item("chip", rooms)
    game.drop_item("marble", rooms)
    assert game.take_item("marble", rooms, command="take marble") == "You take the marble."
    assert [item.name for item in rooms["Weather Gallery"].items] == ["chip of marble"]


def test_the_glass_in_a_frame_is_not_the_carried_glass_bell(openai_api, gallery):
    game, rooms = gallery
    openai_api.post(OPENAI_URL, json=adjudication(effect="observe", anchor="empty picture frames",
                                                  reply="The blade sinks into the clouding inside the frame like fog."))
    result = game.use_on("rusty sword", "glass", rooms, command="break the glass of one of the paintings using your sword")
    assert result == "The blade sinks into the clouding inside the frame like fog."
    assert sent_scene(openai_api)["subject"] == "glass" and sent_scene(openai_api)["held_tool"] == "rusty sword"
