# The Giant's Drink

[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue)](https://www.python.org/)
[![CI Pipeline](https://github.com/acechase/thegiantsdrink/actions/workflows/ci.yml/badge.svg)](https://github.com/acechase/thegiantsdrink/actions/workflows/ci.yml)

A text-based adventure game where you explore a castle, collect items, and face a giant. OpenAI parses your commands and narrates rooms. The game tracks your actions and adapts its difficulty to your playstyle.

## Features

- **Natural language commands** — type freely; an LLM parses your intent
- **15 rooms** with rich, atmospheric descriptions inspired by Ender's Game's Mind Game
- **3 paths to victory** — aggressor, strategist, or explorer
- **Adaptive difficulty** — the game watches how you play and adjusts the world accordingly
- **Player profiling** — the behavior analyzer tracks your play style and tailors content

## Setup

On macOS, check your Python interpreter with `python3 -c "import ssl; print(ssl.OPENSSL_VERSION)"`. If it reports LibreSSL, create the virtual environment with an OpenSSL-backed Python. For example, with uv installed, use `uv venv --python 3.11 --seed .venv` for the environment creation step below. [urllib3 TLS requirements](https://urllib3.readthedocs.io/en/stable/v2-migration-guide.html)

```bash
# Clone the repository
git clone https://github.com/acechase/thegiantsdrink.git
cd thegiantsdrink

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# Install dependencies
pip install -r requirements.txt
```

## OpenAI setup

Copy `.env.example` to `.env` next to `main.py`, then fill in `OPENAI_API_KEY` with your [OpenAI API key](https://platform.openai.com/api-keys). The game also reads these variables from your shell. Shell values take precedence.

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | Required to play. Authenticates requests to the OpenAI API. |
| `OPENAI_MODEL` | Optional. Defaults to `gpt-5.4-mini`. Choose a Chat Completions model that supports JSON output for command parsing. |
| `OPENAI_REASONING_EFFORT` | Optional. Sets the reasoning effort for command parsing and room narration. Omit it to use the model's default. |
| `OPENAI_IMAGE_MODEL` | Optional. The image model for the `--visuals` room companion. Defaults to `gpt-image-2.6-flare`. |

For example, set `OPENAI_MODEL=gpt-6-luna` and `OPENAI_REASONING_EFFORT=low` in your `.env`, then restart the game. Luna accepts `none`, `low`, `medium` (default), `high`, `xhigh`, and `max`. [OpenAI model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna)

Common commands such as `examine the sword` and `take the candle` resolve immediately. For more elaborate phrasing, the game sends OpenAI your command, the current scene, and your inventory. Entering a room can trigger a separate narration request. [OpenAI Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)

## How to Run

```bash
python main.py
```

To see each room as you play, add `--visuals`:

```bash
python main.py --visuals
```

The game prints a local address and opens it in your browser. The page shows an AI illustration of your current room, its items and exits. It redraws when the room changes, for example when you pick something up, open a passage, or alter the scenery. Each distinct scene is generated once and cached in `.room-art/`, so revisiting an unchanged room costs nothing. The page shows whether an illustration is queued, being generated, or loaded from the cache, and offers a retry if generation fails. The viewer only listens on `127.0.0.1`.

## How to Play

The terminal shows which parts of each turn use AI. `[AI]` appears as a request starts and names the model and task. `[Cached AI]` means the game reused an earlier model response. `[Procedural]` marks local game rules or written text. Movement, inventory, combat, and difficulty changes all run locally. For example, `go north` can show a procedural move followed by an AI request to describe the destination.

Anything the game describes is something you can act on, whether it appears in the room text, an AI narration, or an examination. Describe what you want to do in your own words, such as `tear down one of the tapestries`, `fill my pockets with gold coins`, or `pry the ring out of the wall with the sword`. When no written rule covers the attempt, the game asks the model to judge it. It labels that request `[AI] ... environmental interaction`. A plausible attempt changes the world. You might take the object or a piece of it, change it, destroy it, or leave a carried item there. The room description is rewritten to match, and that change stays for the rest of the game. An implausible attempt fails with a physical reason or a clue. The game only commits changes to objects that are actually in the scene. The model can't create new objects out of nothing, move exits, or award victory.

| Command | Description |
|---|---|
| `look` | Look around the current room |
| `examine <object>` or `look at <object>` | Inspect anything described here or in your inventory |
| `go <direction>` | Move north, south, east, west, up, or down |
| `take <item>` | Pick up an item, or try to take something described in the room |
| `drop <item>` | Set down something you're carrying |
| `search <object>` | Investigate something closely |
| `use <item>` | Use an item from your inventory |
| `use <item> on <object>` | Try a carried tool on something here |
| `attack <target>` | Attack something |
| `inventory` | Check your inventory |
| `help` | Show available commands |
| `quit` / `exit` | Leave the game |

## Running Tests

Install the development dependencies with `pip install -r requirements-dev.txt`. The tests simulate OpenAI responses and run without credentials.

```bash
pytest tests/test_game.py -v
```

## Local Development

### Prerequisites
- Python 3.9 or higher
- Git

### Setup

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### Development Commands

```bash
# Format code with Ruff
ruff format .

# Lint and fix issues
ruff check --fix .

# Run all tests
pytest tests/test_game.py -v

# Run with coverage
pytest tests/test_game.py --cov=. --cov-report=html

# Generate API documentation
pdoc --serve simulation_engine behavior_analyzer adaptation_ai content_generator

# Play the game locally
python main.py
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for branch naming, commit conventions, and development workflow.

This project follows [Conventional Commits](https://www.conventionalcommits.org) for all commit messages.

## Documentation

- [Generate API documentation locally](CONTRIBUTING.md#generating-documentation)
- [Changelog](CHANGELOG.md) — Version history and release notes
- [Contributing Guide](CONTRIBUTING.md) — How to contribute
