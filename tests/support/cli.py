"""Drive terminal games through their normal input and OpenAI HTTP boundaries."""

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

import responses

import main
import saga
from tests.support.openai import OPENAI_URL, completion
from tests.support.stories import expanded_arc_plan


def run_cli(inputs, **options):
    """Capture the terminal transcript for a sequence of commands."""
    output = io.StringIO()
    with patch("builtins.input", side_effect=[*inputs, EOFError()]), redirect_stdout(output):
        main.main(**options)
    return output.getvalue()


def run_game(openai_api, inputs, commands, plans=()):
    """Run the game loop with scripted OpenAI replies at the HTTP boundary."""
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
    return run_cli(inputs)
