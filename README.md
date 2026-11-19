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

Each new command can make one API request to parse your input. Actions such as moving can make a second request to narrate the room. The game caches successful results during play. [OpenAI Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)

## How to Run

```bash
python main.py
```

## How to Play

| Command | Description |
|---|---|
| `look` | Look around the current room |
| `go <direction>` | Move north, south, east, west, up, or down |
| `take <item>` | Pick up an item |
| `use <item>` | Use an item from your inventory |
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
