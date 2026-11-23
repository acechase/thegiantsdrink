import pytest
import responses

from command_parser import command_cache
from game_world import fresh_world
from simulation_engine import GameState


@pytest.fixture
def game():
    return GameState()


@pytest.fixture
def wmap():
    return fresh_world()


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
