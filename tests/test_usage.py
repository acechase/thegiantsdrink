import base64
import copy
import pickle

import room_companion
from browser_game import BrowserGame
from game_session import GameSession
from openai_client import complete
from playthrough import render
from saves import SaveSlot
from tests.test_browser_game import submit
from tests.test_game import OPENAI_URL, openai_api  # noqa: F401 - fixture
from usage import Meter, metering

USAGE = {"prompt_tokens": 120, "completion_tokens": 30, "completion_tokens_details": {"reasoning_tokens": 12}}


def reply(text, usage=USAGE):
    return {"choices": [{"message": {"role": "assistant", "content": text}}], **({"usage": usage} if usage else {})}


def test_meter_is_shared_by_staged_copies_and_survives_saves():
    meter = Meter()
    meter.add(text_in=5, text_out=-3, images="2", unknown=9)
    assert copy.deepcopy(meter) is meter
    restored = pickle.loads(pickle.dumps(meter))
    restored.add(text_out=4)
    assert restored.snapshot()["total"] == 9 and meter.snapshot()["total"] == 5


def test_text_requests_bill_the_game_that_made_them(openai_api):  # noqa: F811
    openai_api.post(OPENAI_URL, json=reply("one"))
    openai_api.post(OPENAI_URL, json=reply("two", usage=None))
    openai_api.post(OPENAI_URL, json=reply("three"))
    meter = Meter()
    with metering(meter):
        assert complete("s", "u", max_tokens=5) == "one"
        assert complete("s", "u", max_tokens=5) == "two"  # A reply without usage still counts as a request.
    assert complete("s", "u", max_tokens=5) == "three"  # Outside a game, nothing is billed.
    tokens = meter.snapshot()
    assert (tokens["text_in"], tokens["text_out"], tokens["reasoning"], tokens["text_calls"]) == (120, 30, 12, 2)


def test_paintings_bill_the_game_from_the_illustrator_thread(openai_api, tmp_path):  # noqa: F811
    jpeg = base64.b64encode(b"\xff\xd8\xff-art-\xff\xd9").decode()
    openai_api.post("https://api.openai.com/v1/images/generations",
                    json={"data": [{"b64_json": jpeg}], "usage": {"input_tokens": 80, "output_tokens": 400}})
    session = GameSession()
    painter = room_companion.RoomCompanion(tmp_path, "m", meter=session.meter)
    try:
        painter.show(session.room)
        with painter._condition:
            assert painter._condition.wait_for(lambda: painter.snapshot()["status"] == "ready", timeout=3)
        painter.show(session.room)  # Showing the same scene again repaints nothing.
    finally:
        painter.close()
    tokens = session.meter.snapshot()
    assert (tokens["image"], tokens["images"], tokens["total"]) == (480, 1, 480)


def test_game_totals_are_saved_resumed_and_shown(openai_api, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(room_companion, "generate_image", lambda payload: b"art")
    openai_api.post(OPENAI_URL, json=reply('{"action":"examine","target":"candle"}'))
    session = GameSession()
    slot = SaveSlot.new(tmp_path / "saves")
    game = BrowserGame(session, room_companion.RoomCompanion(tmp_path, "m"), store=slot)
    try:
        assert game.snapshot()["tokens"]["total"] == 0
        state = submit(game, "what is that flickering light?")
        assert state["tokens"]["total"] == 150 and state["tokens"]["text_calls"] == 1
        with game.condition:
            assert game.condition.wait_for(lambda: (game.saved or {}).get("turn") == 1, timeout=3)
        session.meter.add(image_out=50)  # A painting that finishes after the turn was saved.
    finally:
        game.close()
    record = slot.record()
    assert record["tokens"]["total"] == 200 and "200 AI tokens" in slot.summary()
    assert "200 AI tokens" in render(record, tmp_path)
    resumed, _ = slot.load()
    assert resumed.meter.snapshot()["total"] == 200
