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

## Running Tests

```bash
# Run all tests with verbose output
pytest tests/test_game.py -v

# Run with coverage
pytest tests/test_game.py --cov=. --cov-report=term-missing
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
pdoc --html --output-dir docs \
  simulation_engine command_parser content_generator \
  behavior_analyzer adaptation_ai game_world main

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
