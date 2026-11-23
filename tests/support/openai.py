"""Script AI completions and inspect requests made by the game."""

import json

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def completion(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def adjudication(**reply):
    return completion(json.dumps(reply))


def sent_scene(openai_api, index=0):
    return json.loads(json.loads(openai_api.calls[index].request.body)["messages"][1]["content"])


def scripted(monkeypatch, replies):
    """Answer parser and adjudicator calls in order, failing loudly on an unexpected request."""
    replies = iter(replies)

    def complete(*args, **kw):
        return next(replies)

    monkeypatch.setattr("command_parser.complete", complete)
    monkeypatch.setattr("environment_actions.complete", complete)
