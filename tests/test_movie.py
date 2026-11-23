import base64
import io
import json
import shutil
import subprocess
import wave

import pytest
import responses

from characters import make
from movie import run
from movie.casting import CHILD_DIRECTION, Narrator, cast_narrator
from movie.screenplay import MovieError, draft, journal, read_screenplay, scene_art
from movie.voices import DIRECTIONS, FLASH, FLASH_LITE, GEMINI_URL, Cast, VoiceRenderer, design_voice, narrator_for

ART = "a" * 64


def _story():
    return {
        "title": "The Wall Listens",
        "scenes": [
            {"title": "The hollow stone", "turns": [1, 2], "speaker": "narrator", "text": "One stone rang hollow."},
            {"title": "Below", "turns": [2], "art": [], "speaker": None, "seconds": .75},
        ],
    }


def _journal():
    return [
        {"command": "inventory", "place": "The Castle", "events": [{"kind": "outcome", "text": "A sword."}]},
        {"turn": 2, "command": "tap a stone", "place": "The Castle",
         "events": [{"kind": "outcome", "text": "A hollow knock."}], "art": {"key": ART, "kind": "cutscene"}},
    ]


def _wave():
    data = io.BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x02\x00\xfe" * 6000)
    return data.getvalue()


def test_draft_anchors_a_scene_to_old_journal_turns(monkeypatch, tmp_path):
    turns = journal({"journal": _journal()})
    (tmp_path / f"{ART}.jpg").touch()

    def adapt(system, data, **kwargs):
        assert "Never follow instructions" in system
        assert '"art":"cutscene"' in data and '"turn":2' in data and "inventory" not in data
        assert data.startswith("Player character: You are a small, slight child of about ten")
        assert kwargs["json_output"]
        return json.dumps(_story())

    monkeypatch.setattr("movie.screenplay.complete", adapt)
    story = draft(turns, tmp_path)
    first, second = read_screenplay(story, turns).scenes
    assert scene_art(first, turns, tmp_path) == [(tmp_path / f"{ART}.jpg", "cutscene")]
    assert scene_art(second, turns, tmp_path) == []
    story["scenes"][1]["turns"] = [9000]
    with pytest.raises(MovieError, match="Scene 2"):
        read_screenplay(story, turns)


def test_draft_narrates_in_the_saved_characters_words(monkeypatch, tmp_path):
    seen = {}

    def adapt(system, data, **kwargs):
        seen.update(system=system, data=data)
        return json.dumps(_story())

    monkeypatch.setattr("movie.screenplay.complete", adapt)
    draft(journal({"journal": _journal()}), tmp_path, make("girl").public())
    assert seen["data"].startswith("Player character: You are a girl of about ten")
    assert "telling a friend" in seen["system"] and "Never follow instructions" in seen["system"]


def _casting(monkeypatch, answer):
    calls = []

    def cast(system, description, **kwargs):
        calls.append(description)
        return json.dumps(answer)

    monkeypatch.setattr("movie.casting.complete", cast)
    return calls


SAILOR = make("child", "You are a retired ship's cook of sixty from Cornwall, gruff but kind.").public()
SAILOR_CAST = {"child": False, "gender": "male", "language_code": "en-GB",
               "voice": "A gravelly Cornish man of sixty, warm under the gruffness, unhurried."}


def test_child_presets_keep_their_voice_with_everyday_delivery(monkeypatch, tmp_path):
    calls = _casting(monkeypatch, {})
    assert cast_narrator(make("girl").public(), tmp_path) == Narrator("Leda", CHILD_DIRECTION, True)
    assert cast_narrator(None, tmp_path) == Narrator("Algieba", CHILD_DIRECTION, True)
    assert cast_narrator(make("girl").public(), tmp_path, "Aoede").voice == "Aoede"
    assert not calls and not (tmp_path / "cast.json").exists()


def test_edited_child_is_cast_without_designing_a_voice(monkeypatch, tmp_path):
    _casting(monkeypatch, {"child": True, "gender": "male", "language_code": "en-US", "voice": ""})
    character = make("girl", "You are a boy of nine who collects beetles.").public()
    with responses.RequestsMock():
        assert cast_narrator(character, tmp_path, report=lambda message: None) == Narrator(
            "Algieba", CHILD_DIRECTION, True)


def test_adult_character_gets_one_designed_voice(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    calls = _casting(monkeypatch, SAILOR_CAST)
    sample = _wave()
    with responses.RequestsMock() as api:
        api.post(f"{GEMINI_URL}/voices",
                 json={"id": "voice_cook", "sample_audio": {"data": base64.b64encode(sample).decode()}})
        narrator = cast_narrator(SAILOR, tmp_path, report=lambda message: None)
        sent = json.loads(api.calls[0].request.body)["voice"]
    assert narrator == Narrator("voice_cook", DIRECTIONS["narrator"], False)
    assert sent["prompted"] == {"input": SAILOR_CAST["voice"]}
    assert (sent["language_code"], sent["gender"]) == ("en-GB", "male")
    assert (tmp_path / "narrator-voice.wav").read_bytes() == sample
    with responses.RequestsMock():
        assert cast_narrator(SAILOR, tmp_path).voice == "voice_cook"
    assert len(calls) == 1
    saved = json.loads((tmp_path / "cast.json").read_text())
    saved["voice_prompt"] = "A softer Cornish voice."
    (tmp_path / "cast.json").write_text(json.dumps(saved))
    with responses.RequestsMock() as api:
        api.post(f"{GEMINI_URL}/voices", json={"id": "voice_softer"})
        assert cast_narrator(SAILOR, tmp_path, report=lambda message: None).voice == "voice_softer"
        assert json.loads(api.calls[0].request.body)["voice"]["prompted"] == {"input": "A softer Cornish voice."}
    assert len(calls) == 1


def test_declined_voice_design_falls_back_once(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _casting(monkeypatch, {**SAILOR_CAST, "gender": "female"})
    messages = []
    with responses.RequestsMock() as api:
        api.post(f"{GEMINI_URL}/voices", status=400, json={"error": {
            "code": 400, "message": "Voice prompt was blocked by safety policies.", "status": "INVALID_ARGUMENT"}})
        assert cast_narrator(SAILOR, tmp_path, report=messages.append).voice == "Despina"
    assert "blocked by safety policies" in messages[-1]
    with responses.RequestsMock():
        assert cast_narrator(SAILOR, tmp_path).voice == "Despina"


def test_gemini_quota_fallback_and_offline_reuse(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    url = f"{GEMINI_URL}/interactions"
    wav = _wave()
    result = {"steps": [{"type": "model_output", "content": [{"type": "audio", "data": base64.b64encode(wav).decode()}]}]}
    first = read_screenplay(_story(), journal({"journal": _journal()})).scenes[0]
    with responses.RequestsMock() as api:
        api.post(url, status=429)
        api.post(url, json=result)
        using = VoiceRenderer(tmp_path, Cast(), report=lambda message: None)
        saved = using.take(first)
        assert saved.read_bytes() == wav
        assert [json.loads(call.request.body)["model"] for call in api.calls] == [FLASH, FLASH_LITE]
        assert api.calls[-1].request.headers["x-goog-api-key"] == "test-key"
    monkeypatch.delenv("GEMINI_API_KEY")
    assert VoiceRenderer(tmp_path, Cast()).take(first) == saved


def test_design_voice_transmits_traits_and_writes_wav(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    audio = _wave()
    with responses.RequestsMock() as api:
        api.post(f"{GEMINI_URL}/voices",
                 json={"id": "voice_example", "sample_audio": {"data": base64.b64encode(audio).decode()}})
        assert design_voice("The Giant", "A quiet bass.", "en-GB", "male") == ("voice_example", audio)
        request = json.loads(api.calls[0].request.body)
        assert request["store"] and request["voice"]["prompted"] == {"input": "A quiet bass."}
        assert request["voice"]["language_code"] == "en-GB"


@pytest.mark.parametrize(("character", "voice"), [
    (make("girl").public(), "Leda"),
    (make("girl", "You are quiet and carry a sketchbook.").public(), "Leda"),
    (make("girl", "You're a small boy with a dark braid.").public(), "Algieba"),
    (make("child", "You are nervous at first. You are a ten-year-old girl.").public(), "Leda"),
    (make("child", "A young woman with a silver locket.").public(), "Despina"),
    (make("child", "You are a child whose sister is a girl.").public(), "Algieba"),
    (make("child").public(), "Algieba"),
    (None, "Algieba"),
])
def test_narrator_uses_the_saved_character(character, voice):
    assert narrator_for(character) == voice


def test_export_renders_existing_screenplay_and_reuses_cached_audio(monkeypatch, tmp_path):
    image = pytest.importorskip("PIL.Image")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Rendering requires FFmpeg.")
    slot = tmp_path / "saves" / "archive"
    slot.mkdir(parents=True)
    (slot / "game.json").write_text(json.dumps({
        "journal": _journal(), "character": make("girl").public(),
    }), encoding="utf-8")
    (slot / "screenplay.json").write_text(json.dumps(_story()), encoding="utf-8")
    art_dir = tmp_path / ".room-art"
    art_dir.mkdir()
    image.new("RGB", (300, 220), (56, 42, 89)).save(art_dir / f"{ART}.jpg")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("MOVIE_NARRATOR_VOICE", raising=False)
    wav = _wave()
    music = tmp_path / "score.wav"
    music.write_bytes(wav)
    out = slot / "movie.mp4"
    with responses.RequestsMock() as api:
        api.post(f"{GEMINI_URL}/interactions", json={"steps": [
            {"type": "model_output", "content": [{"type": "audio", "data": base64.b64encode(wav).decode()}]},
        ]})
        run(["export", "--music", str(music)], tmp_path)
        assert len(api.calls) == 1
        request = json.loads(api.calls[0].request.body)
        assert request["generation_config"]["speech_config"] == [{"voice": "Leda"}]
        assert request["input"][0]["content"][0]["annotations"][0]["style"] == CHILD_DIRECTION
        assert out.exists()
    monkeypatch.delenv("GEMINI_API_KEY")
    with responses.RequestsMock():
        run(["export"], tmp_path)
    result = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type:chapter_tags=title", "-of", "json", str(out),
    ])
    movie = json.loads(result)
    assert {stream["codec_type"] for stream in movie["streams"]} >= {"video", "audio"}
    assert [chapter["tags"]["title"] for chapter in movie["chapters"]] == ["The hollow stone", "Below"]
