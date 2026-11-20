# The Giant's Drink

[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue)](https://www.python.org/)
[![CI Pipeline](https://github.com/acechase/thegiantsdrink/actions/workflows/ci.yml/badge.svg)](https://github.com/acechase/thegiantsdrink/actions/workflows/ci.yml)

A text-based adventure game where you explore a castle, collect items, and face a giant. OpenAI parses your commands and narrates rooms. The game tracks your actions and adapts its difficulty to your playstyle.

## Features

- **Natural language commands** — type freely; an LLM parses your intent
- **15 rooms** with rich, atmospheric descriptions inspired by Ender's Game's Mind Game
- **Lives and checkpoints** — danger follows repeatable rules, and what you learn survives each death
- **Autosave and shareable playthroughs** — resume any game, and export the whole story with its illustrations as one HTML page
- **Endless arcs after the giant** — the world keeps growing, one AI-planned challenge arc at a time
- **Adaptive difficulty** — the game watches how you play and adjusts the world accordingly
- **Player profiling** — the behavior analyzer tracks your play style and tailors content

## Setup

On macOS, check your Python interpreter with `python3 -c "import ssl; print(ssl.OPENSSL_VERSION)"`. If it reports LibreSSL, create the virtual environment with an OpenSSL-backed Python. For example, with uv installed, use `uv venv --python 3.11 --seed .venv` for the environment creation step below. [urllib3 TLS requirements](https://urllib3.readthedocs.io/en/stable/v2-migration-guide.html)

```bash
# Clone the repository
git clone https://github.com/acechase/thegiantsdrink.git
cd thegiantsdrink

# Create and activate a virtual environment
python3 -m venv .venv
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
| `OPENAI_IMAGE_MODEL` | Optional. The image model for browser play and the room companion. Defaults to `gpt-image-2.6-flare`. |

For example, set `OPENAI_MODEL=gpt-6-luna` and `OPENAI_REASONING_EFFORT=low` in your `.env`, then restart the game. Luna accepts `none`, `low`, `medium` (default), `high`, `xhigh`, and `max`. [OpenAI model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna)

Common commands such as `examine the sword` and `take the candle` resolve immediately. For more elaborate phrasing, the game sends OpenAI your command, the current scene, and your inventory. Entering a room can trigger a separate narration request. [OpenAI Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)

## How to Run

Play in a browser with the room illustration, story, and command input on one page.

```bash
./.venv/bin/python main.py --browser
```

The game opens a tab and prints its local address. Click a detail to examine it, click an exit to move, or type an action in your own words. The Trail row lists your last four moves. A small counter under the input shows how many AI tokens the current game has used, live; hover over it for text, reasoning and illustration totals. Cached narration and illustrations cost nothing, and the total is kept in the game's save, listed by `--saves` and shown in its playthrough. Inventory shows what you carry and marks what you have on. The journal keeps your commands, outcomes, and chapter arrivals. You can draft your next action while a turn is running.

Refresh or reopen the printed address to rejoin the same game. All tabs at that address share one player. Typing `quit` ends play and leaves the journal available. Press Ctrl+C in the launching terminal to stop the server. Every turn is saved, so you can stop at any time and resume later (see [Saving and sharing](#saving-and-sharing)).

On Windows use `.venv\Scripts\python.exe main.py --browser`. For terminal play on macOS or Linux, run `./.venv/bin/python main.py`. Add `--visuals` for a separate read-only room companion.

Anything you put on, such as a helm, a tapestry worn as a cloak, or a chain looped like a necklace, stays on until you take it off or set it down. A character card beside the room name shows your character as they look now, with your current life. It repaints whenever you put something on, take it off, pick something up or put it down, and keeps the previous painting until the new one is ready. Moving between rooms never repaints it. Select the card, or type `look at yourself`, to see a full-length portrait in place of the room illustration. Your next action brings the room back. Each distinct outfit is painted once and cached.

Both browser modes listen on `127.0.0.1`. The game generates and caches illustrations in `.room-art/` for each distinct visible scene. Taking an item or opening a passage can trigger another image request. Revisiting a cached scene reuses its art. Browser play shows a sketch of the exits while an illustration is pending or unavailable. You can continue playing or retry a failed illustration.

## Saving and sharing

Every game saves itself after each turn, in the browser and in the terminal. Each game gets a folder in `saves/` named after the moment it started, such as `saves/2026-11-20_15-32-07/`.

```bash
./.venv/bin/python main.py --browser --continue          # resume your most recent game
./.venv/bin/python main.py --browser --load 2026-11-20_15-32-07
./.venv/bin/python main.py --saves                       # list saved games
./.venv/bin/python main.py --export                      # write a playthrough of the latest game
./.venv/bin/python main.py --export 2026-11-20_15-32-07
```

Leave out `--browser` to resume in the terminal instead. Resuming a game you quit reopens it.

A **playthrough** is one self-contained HTML page with every command, outcome, death, lesson and chapter, along with the illustrations and portraits painted along the way. Open it from the **Playthrough** link while you play, or export it with `--export` to `saves/<game>/playthrough.html`. You can email the file or host it anywhere. Rooms you passed through before their illustration finished appear without a picture.

Each save folder holds `game.json` (the readable journal) and `state.bin` (a snapshot of the world). Snapshots are signed with `saves/.key`, which is created on your computer the first time you save. The game resumes only snapshots that carry that signature, because loading a snapshot can run code. Saves copied from someone else can still be exported as playthroughs. Keep `saves/.key` if you move your saves to another computer.

To keep the story of a game that is still running on a build from before saving existed, give its address to `--archive`. This writes a view-only save with a text playthrough:

```bash
./.venv/bin/python main.py --archive http://127.0.0.1:64482
```

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
| `remember` | Recall lessons from previous lives |
| `look at yourself` | Describe what you have on and carry; in the browser, paint your portrait |
| `inventory` | Check your inventory |
| `help` | Show available commands |
| `quit` / `exit` | Leave the game |

## Lives and checkpoints

The castle gives you time to look. Read the room, examine what seems out of place, and type what you want to try. A dangerous action can kill your character. A death tells you what happened and returns you to a checkpoint in the same game. You keep your possessions, discoveries, and learned routes. Temporary potion strength fades.

Repeated deaths at the giant’s table send you progressively farther back through the castle. In later chapters, forcing an unresolved crossing after its warning can send you back to that chapter’s entrance. Type `remember` or open the browser journal to recall what previous lives taught you.

## After the giant

Defeating the giant doesn't end the game. A crack opens behind the empty throne, and the game plans the first of an endless series of **arcs**: AI-generated regions of three to six rooms, each ending in an obstacle that bars the way onward.

Each arc is planned in full before you enter it. The plan covers the rooms, the items in them, the obstacle, and two or three intended ways past it. At least one of those uses an item placed in the arc, and others may use things you already carry. The planner sees your play style, your recent commands and a chronicle of what you've done. It tries to design a challenge your habits haven't tested yet. If you've relied on force, force alone may fail here.

The game checks each plan before you can play it. Every room must be reachable. The obstacle must be a real object in the arc. At least one solution must use an item the arc provides. Room and item names can't clash with existing ones. If a plan fails these checks, the planner gets the reason and tries again.

When you reach the obstacle, an intended solution works directly, for example `use kite on gap`, or the same thing in your own words. You can also try something the plan didn't anticipate. The model accepts it if it's at least as clever and as physically plausible as the intended solutions. Once you're past, the game shows a glimpse of what lies beyond. It then plans the next arc to begin there and opens the way. Earlier arcs stay connected, so you can always walk back.

Planning an arc takes one longer request, around 40 seconds with `gpt-6-luna` at medium effort. If the request fails, the way onward shows grey mist. Walk that way again to retry. Rooms in an arc use their planned descriptions, so moving between them needs no further narration requests.

## Running Tests

Install the development dependencies with `pip install -r requirements-dev.txt`. The tests simulate OpenAI responses and run without credentials.

```bash
pytest tests -v
```

## Local Development

### Prerequisites
- Python 3.9 or higher
- Git

### Setup

```bash
# Create virtual environment
python3 -m venv .venv
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
pytest tests -v

# Run with coverage
pytest tests --cov=. --cov-report=html

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
