# Contributing to The Giant's Drink

Thanks for your interest in contributing! This guide covers the conventions
and workflows used in this project.

## Branch Naming

Use the following prefixes:

| Prefix       | Purpose                        |
|--------------|--------------------------------|
| `feature/`   | New features                   |
| `fix/`       | Bug fixes                      |
| `docs/`      | Documentation only             |
| `chore/`     | Maintenance, CI, dependencies  |
| `test/`      | Adding or updating tests       |

Example: `feature/add-magic-system`

## Commit Messages

This project follows [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <short description>

<optional body>
```

Types: `feat`, `fix`, `docs`, `test`, `ci`, `chore`, `refactor`, `style`

## Local Development Setup

```bash
# Clone and enter the repo
git clone https://github.com/acechase/thegiantsdrink.git
cd thegiantsdrink

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install runtime + dev dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

## Code layout

| Change | Where to work |
| --- | --- |
| Author a room, item, or encounter | `game_world/castle.py` holds the castle template. `game_world/models.py` defines rooms, items, discoveries, and challenges. `trials.py` defines the authored encounters. |
| Interpret or resolve an action | `game_world/names.py` matches object references. `command_parser.py` turns input into an intent. `simulation_engine.py` applies game rules, and `environment_actions.py` adjudicates improvised actions. |
| Extend inventory or crafting | `game_world/gear.py` handles transfers, numbered references, and inventory views. The design notes cover item capabilities, save compatibility, and regression tests. [Crafted storage and attachments](development/inventory.md) |
| Change what happens during a turn | `GameSession` in `game_session/` coordinates the simulation, narration, and progression for both interfaces. `game_session/journal.py` defines the events and journal entries those interfaces share. |
| Extend a story or publish a replay | `saga/` manages progression. `plans.py` builds rooms from plans, `routes.py` checks puzzle reachability, `prompt.py` contains the planner instructions, and `catalog.py` handles nominations and editions. |
| Change an illustration or browser interaction | `room_companion/scenes.py` captures the scene and builds the image request. `renderer.py` handles the cache and queue, `images.py` calls the image service, and `server.py` serves the companion. `browser_game.py` serves interactive play. Both views use `room_companion/static/`. |
| Change saves or playthrough exports | `saves.py` signs and restores session snapshots and writes the JSON journal. `playthrough.py` turns a saved journal and its cached artwork into a standalone HTML page. |

Each session starts with `game_world.fresh_world()`, which copies the castle and preserves shared references inside that copy. Modify the session's rooms during play. Use the same factory with a custom map for an isolated scenario or test.

Old snapshots look up classes by their Python module and name. When moving a saved type, keep its original import path working and test loading its earlier field values. Catalog plan IDs depend on the serialized plan fields. Cached illustration IDs depend on the exact image request.

## Starting characters

Add new starting characters to `characters.PRESETS`. Give each preset a unique key, a display name, and a second-person description that includes the character's base clothes and fits within `characters.MAX_DESCRIPTION`. Presets appear in order in the browser and terminal choosers.

## Running Tests

Subsystem tests live under `tests/`. Shared pytest fixtures live in `tests/conftest.py`. Use `tests/support/` for sample story plans, scripted model replies, and drivers for the terminal and browser. The save tests include a game written by commit `49d85e5` so future package moves are checked against a real earlier snapshot.

```bash
# Run all tests with verbose output
python -m pytest -v

# Run with coverage
python -m pytest --cov=. --cov-report=term-missing
```

## Linting

```bash
# Check for lint issues
ruff check .

# Auto-fix what can be fixed
ruff check . --fix

# Format code
ruff format .
```

## Generating Documentation

```bash
# Generate HTML docs with pdoc
python -m pdoc -o docs game_session game_world saga room_companion \
  simulation_engine command_parser content_generator behavior_analyzer adaptation_ai main

# Preview locally
python -m http.server --directory docs 8080
```

## Pull Requests

1. Create a feature branch from `main`.
2. Make your changes with conventional commit messages.
3. Ensure all tests pass and `ruff check .` is clean.
4. Open a PR using the repository's pull request template.
5. Request review from a maintainer.

## Review and publish replay editions

Ask players to send the JSON file produced by **Nominate storyline**. The game has no submission server. Keep the files in a private review queue until you approve them.

```sh
python main.py stories import /path/to/storyline-nomination.json
python main.py stories queue
python main.py stories review NOMINATION_ID
python main.py stories playtest NOMINATION_ID
python main.py stories publish NOMINATION_ID lantern-marsh 1 --title "Lantern Marsh"
python main.py stories list
```

Import prints the nomination ID and flags matches or extensions of published editions. The queue lives under `saves/story-review/`. Review shows the submitted prose, room exits, obstacles, and solutions. Playtest starts just after the Giant’s Cup and stops extending the story at the end of the nomination. Add `--browser` to playtest in the browser. Use ordinary movement, take, and use commands to check that the planned route works without an arc-planning request.

Publish only after reviewing the story text and playing its route. If it needs changes, ask for a revised submission. Publish an extension under the same edition ID with the next revision number. Earlier revisions remain selectable, and an identical route can only be published once.

Publishing updates `stories.sqlite3`. Include that file with the next full game distribution and retain every previously published revision. Players read the catalog directly from the shipped game. You can replace it on upgrade while keeping the separate `saves/` directory. To rehearse publication, put `--catalog /path/to/test.sqlite3` before the story command and use `--story-catalog /path/to/test.sqlite3` when starting a replay against it.

### How replay is implemented

1. **Capture and export.** The arc validator retains the playable fields of each accepted plan in the current save. Entry into the arc’s first room marks it eligible for nomination. Export writes just the reached route in versioned JSON, with a 100-arc or 2 MB limit. Identical exports share a review ID.
2. **Validate and publish.** Import rebuilds the route against a clean post-Cup castle. Validation checks that all rooms can reach the crossing and that at least one tool found in each arc can solve its obstacle with ordinary commands. SQLite stores each plan by content hash and publishes its route links and edition revision in one transaction.
3. **Resolve crossings.** A replay saves its selected revision and plans when the game starts. Both interfaces check the edition’s next plan first if the player has followed that edition so far. They then look for compatible published branches at the current parent arc and exit. Each reused plan creates new room objects for the session. Optional solutions that require a missing item are skipped. A miss uses the existing planner and the resulting arc stays in the current save.
4. **Verify compatibility.** Existing saves keep their current world and resume in their original fresh-story mode if they predate the feature. The tests cover private capture, nomination confirmation, queue validation, immutable revisions, browser preview, and planner calls on published and unpublished crossings.
