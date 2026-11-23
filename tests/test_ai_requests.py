import json

import pytest
import requests

from command_parser import parse_command
from content_generator import ContentGenerator
from tests.support.openai import OPENAI_URL, completion


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
