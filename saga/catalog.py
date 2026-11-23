"""Portable story nominations and the published SQLite catalog."""

import hashlib
import json
import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from game_world import OPPOSITE, fresh_world
from saves import write_atomic

from .models import PlanError
from .plans import build_arc
from .routes import castle_crossing, validate_legacy_crossing

DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "stories.sqlite3"
FORMAT = 1
MAX_BYTES = 2_000_000
MAX_ARCS = 100
EDITION = r"[a-z0-9][a-z0-9-]{0,47}"
SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS editions (
    name TEXT NOT NULL, revision INTEGER NOT NULL, title TEXT NOT NULL,
    route_id TEXT NOT NULL UNIQUE, PRIMARY KEY (name, revision)
);
CREATE TABLE IF NOT EXISTS steps (
    name TEXT NOT NULL, revision INTEGER NOT NULL, position INTEGER NOT NULL,
    parent_id TEXT NOT NULL, direction TEXT NOT NULL, plan_id TEXT NOT NULL REFERENCES plans(id),
    PRIMARY KEY (name, revision, position),
    FOREIGN KEY (name, revision) REFERENCES editions(name, revision)
);
CREATE INDEX IF NOT EXISTS crossings ON steps (parent_id, direction, plan_id);
"""


class StoryError(ValueError):
    """An invalid nomination, edition, or catalog operation."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode("ascii")).hexdigest()


def plan_id(plan):
    """Identify a plan by its playable content."""
    return _digest(plan) if plan else None


def nomination(plans):
    """Make a portable bundle from a reached route."""
    if not plans:
        raise StoryError("Enter a chapter beyond the Giant's Hall before nominating a storyline.")
    if any(plan is None for plan in plans):
        raise StoryError("This route includes chapters from an older save that did not retain their plans.")
    bundle = {"format": FORMAT, "arcs": list(plans)}
    if len(plans) > MAX_ARCS or len(bundle_bytes(bundle)) > MAX_BYTES:
        raise StoryError("This route exceeds the nomination limit of 100 arcs or 2 MB.")
    return bundle


def bundle_bytes(bundle):
    """Encode a nomination for export."""
    return (json.dumps(bundle, indent=2) + "\n").encode("utf-8")


def nomination_id(bundle):
    """Give identical nominations the same file name and review ID."""
    return _digest(bundle)


def export_nomination(bundle, directory):
    """Write a portable nomination to a local folder."""
    directory = Path(directory)
    path = directory / f"{nomination_id(bundle)}.json"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        write_atomic(path, bundle_bytes(bundle))
    except OSError as exc:
        raise StoryError(f"Could not write the nomination: {exc}") from exc
    return path


def read_nomination(path):
    """Read and validate a bounded JSON file before it enters review."""
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise StoryError("The nomination exceeds the 2 MB file limit.")
        bundle = json.loads(data)
    except StoryError:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise StoryError(f"Could not read the nomination: {exc}") from exc
    if not isinstance(bundle, dict) or type(bundle.get("format")) is not int or bundle["format"] != FORMAT:
        raise StoryError("This file does not use a supported nomination format.")
    plans = bundle.get("arcs")
    if not isinstance(plans, list) or not 1 <= len(plans) <= MAX_ARCS:
        raise StoryError("A nomination needs between 1 and 100 arcs.")
    return nomination([plan for _, plan in validate_route(plans)])


def validate_route(plans):
    """Rebuild the route from the castle and verify an independent local solution to each crossing."""
    world = fresh_world()
    direction = castle_crossing(world["Giant's Hall"])
    steps = []
    for number, plan in enumerate(plans, 1):
        try:
            arc, rooms = build_arc(_json(plan), number=number, reserved=OPPOSITE[direction], world=world,
                                   allow_missing_tools=True)
            gate = rooms[arc.challenge_room]
            if "passages" not in arc.plan:
                validate_legacy_crossing(rooms, gate)
        except PlanError as exc:
            raise StoryError(f"Arc {number}: {exc}") from exc
        steps.append((direction, arc.plan))
        world.update(rooms)
        direction = gate.challenge.blocks
    return steps


class Catalog:
    """Read published editions or explicitly publish one as a maintainer."""

    def __init__(self, path=None):
        """Use the bundled catalog unless a path is supplied."""
        self.path = Path(path) if path is not None else DEFAULT_CATALOG

    @contextmanager
    def _open(self, *, write=False):
        try:
            uri = self.path.resolve().as_uri() + ("?mode=rwc" if write else "?mode=ro")
            with closing(sqlite3.connect(uri, uri=True)) as db, db:
                db.execute("PRAGMA foreign_keys = ON")
                if write:
                    db.executescript(SCHEMA)
                    db.execute("BEGIN IMMEDIATE")
                yield db
        except sqlite3.Error as exc:
            raise StoryError(f"Could not access the story catalog: {exc}") from exc

    def editions(self):
        """List every published edition and revision."""
        with self._open() as db:
            return db.execute("SELECT name, revision, title FROM editions ORDER BY name, revision DESC").fetchall()

    def route(self, selector):
        """Load a particular revision, or the newest revision of an edition."""
        match = re.fullmatch(rf"({EDITION})(?:@([1-9][0-9]{{0,8}}))?", selector)
        if not match:
            raise StoryError("Use an edition ID such as lantern-marsh or lantern-marsh@2.")
        name, version = match.groups()
        with self._open() as db:
            revision = int(version) if version else db.execute(
                "SELECT MAX(revision) FROM editions WHERE name = ?", (name,)
            ).fetchone()[0]
            rows = db.execute("""
                SELECT plans.body FROM steps JOIN plans ON steps.plan_id = plans.id
                WHERE name = ? AND revision = ? ORDER BY position
            """, (name, revision)).fetchall()
        if not rows:
            raise StoryError(f"No published story edition matches {selector}. Run 'python main.py stories list'.")
        return f"{name}@{revision}", tuple(json.loads(body) for (body,) in rows)

    def continuations(self, parent, direction, *, skip=None):
        """Find published branches in stable order for this exact crossing."""
        if parent is None:
            return ()
        with self._open() as db:
            rows = db.execute("""
                SELECT DISTINCT plans.id, plans.body FROM steps JOIN plans ON steps.plan_id = plans.id
                WHERE parent_id = ? AND direction = ? AND plans.id != ? ORDER BY plans.id
            """, (parent, direction, plan_id(skip) or "")).fetchall()
        return tuple(json.loads(body) for _, body in rows)

    def matching_routes(self, plans):
        """Find exact duplicates and published routes that this nomination extends."""
        ids = [plan_id(plan) for plan in plans]
        with self._open() as db:
            rows = db.execute("""
                SELECT name, revision, position, plan_id FROM steps ORDER BY name, revision, position
            """).fetchall()
        routes = {}
        for name, revision, _, ident in rows:
            routes.setdefault(f"{name}@{revision}", []).append(ident)
        return [(label, len(route) == len(ids)) for label, route in routes.items() if route == ids[:len(route)]]

    def publish(self, bundle, name, revision, title):
        """Publish an immutable edition revision in a single transaction."""
        if not re.fullmatch(EDITION, name) or not 1 <= revision <= 999_999_999:
            raise StoryError("Give a lowercase edition ID and a positive revision number of up to nine digits.")
        title = " ".join(title.split())
        if not 1 <= len(title) <= 80:
            raise StoryError("The edition title must be between 1 and 80 characters.")
        steps = validate_route(bundle["arcs"])
        route_id = _digest([plan_id(plan) for _, plan in steps])
        label = f"{name}@{revision}"
        with self._open(write=True) as db:
            existing = db.execute("SELECT title, route_id FROM editions WHERE name = ? AND revision = ?",
                                  (name, revision)).fetchone()
            if existing == (title, route_id):
                return label
            if existing:
                raise StoryError(f"{label} is already published. Choose the next revision to publish changes.")
            duplicate = db.execute("SELECT name, revision FROM editions WHERE route_id = ?", (route_id,)).fetchone()
            if duplicate:
                raise StoryError(f"This route is already published as {duplicate[0]}@{duplicate[1]}.")
            latest = db.execute("SELECT MAX(revision) FROM editions WHERE name = ?", (name,)).fetchone()[0] or 0
            if revision != latest + 1:
                raise StoryError(f"The next revision of {name} is {latest + 1}.")
            db.execute("INSERT INTO editions VALUES (?, ?, ?, ?)", (name, revision, title, route_id))
            parent = ""
            for position, (direction, plan) in enumerate(steps):
                ident = plan_id(plan)
                db.execute("INSERT OR IGNORE INTO plans VALUES (?, ?)", (ident, _json(plan)))
                db.execute("INSERT INTO steps VALUES (?, ?, ?, ?, ?, ?)", (name, revision, position, parent, direction, ident))
                parent = ident
        return label
