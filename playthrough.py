"""Render a game's journal as one self-contained HTML page that can be shared and read anywhere."""

import base64
import re
from datetime import datetime
from html import escape
from pathlib import Path

ART_KEY = re.compile(r"[0-9a-f]{64}")
KINDS = {"outcome", "arrival", "death", "lesson", "respawn", "chapter", "horizon", "milestone", "story", "error"}
STYLE = """
:root { --paper: #f5f0e5; --pine: #203b33; --ink: #2b3029; --quiet: #585f51; --line: #c7c5b5; --rust: #874430;
  --serif: Baskervville, "Iowan Old Style", Palatino, Georgia, serif;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--paper); color: var(--ink); font: 17px/1.5 var(--serif); }
main { max-width: 760px; margin: 0 auto; padding: 40px 22px 80px; }
h1 { color: var(--pine); font: normal clamp(34px, 6vw, 54px)/1.05 var(--serif); letter-spacing: -.04em; margin: 0 0 8px; }
h2 { color: var(--rust); font: 600 12px/1.5 var(--sans); letter-spacing: .12em; text-transform: uppercase;
  margin: 46px 0 10px; padding-top: 14px; border-top: 1px solid var(--line); }
.meta, .byline span { color: var(--quiet); font: 14px/1.5 var(--sans); }
.art { aspect-ratio: 3/2; margin: 22px 0 14px; border: 4px solid #283b32; background: #283b32 center / cover no-repeat; }
.art.portrait { background-size: contain; }
.turn { margin: 14px 0 18px; }
.byline { margin: 0 0 3px; font: 14px/1.5 var(--sans); }
.byline strong { color: var(--rust); margin-right: 8px; }
.turn p { margin: 4px 0; white-space: pre-line; }
.arrival { color: var(--quiet); }
.death { color: var(--rust); font-weight: 600; }
.lesson, .horizon { font-style: italic; }
.chapter, .milestone { color: var(--pine); font-weight: 600; }
.error { color: var(--quiet); font-style: italic; }
ul { padding-left: 20px; }
"""


def _art(entry):
    art = entry.get("art")
    if isinstance(art, dict) and isinstance(art.get("key"), str) and ART_KEY.fullmatch(art["key"]):
        return art["key"], art.get("kind") == "portrait"
    return None, False


def _started(record):
    try:
        started = datetime.fromisoformat(record["started"])
        return f"{started:%B} {started.day}, {started.year}"
    except (KeyError, TypeError, ValueError):
        return ""


def render(record, art_dir):
    """Return the playthrough page for a game record: its journal, illustrations and lessons."""
    journal = [entry for entry in record.get("journal", []) if isinstance(entry, dict)]
    images, body, chapters = {}, [], []
    chapter = shown = None
    for entry in journal:
        if entry.get("chapter") != chapter:
            chapter = entry.get("chapter")
            chapters.append(str(chapter or "The Castle"))
            body.append(f"<h2>{escape(chapters[-1])}</h2>")
        events = []
        for event in entry.get("events", []):
            if isinstance(event, dict):
                kind = event.get("kind") if event.get("kind") in KINDS else "outcome"
                events.append(f'<p class="{kind}">{escape(str(event.get("text", "")))}</p>')
        command = str(entry.get("command", ""))
        body.append(f'<section class="turn"><p class="byline"><strong>{escape(command)}</strong>'
                    f'<span>{escape(str(entry.get("place", "")))}</span></p>{"".join(events)}</section>')
        # The illustration shows the scene as this turn left it, so it follows the turn's text.
        key, portrait = _art(entry)
        if key and key != shown:
            if key not in images:
                try:
                    images[key] = base64.b64encode((Path(art_dir) / f"{key}.jpg").read_bytes()).decode("ascii")
                except OSError:
                    images[key] = None
            if images[key]:
                shown = key
                label = "The player's portrait" if portrait else f"The scene after “{command}”"
                css = "art portrait" if portrait else "art"
                body.append(f'<div class="{css} a{key}" role="img" aria-label="{escape(label)}"></div>')

    life = record.get("life", 1) if isinstance(record.get("life"), int) else 1
    places = len(set(chapters))
    tokens = (record.get("tokens") or {}).get("total") if isinstance(record.get("tokens"), dict) else None
    facts = [_started(record), f"{len(journal)} turns", f"{life - 1} {'death' if life == 2 else 'deaths'}",
             f"{places} {'chapter' if places == 1 else 'chapters'}",
             f"{tokens:,} AI tokens" if isinstance(tokens, int) and tokens else ""]
    lessons = [str(lesson) for lesson in record.get("lessons", []) if lesson]
    learned = ("<h2>What this player learned</h2><ul>" + "".join(f"<li>{escape(x)}</li>" for x in lessons) + "</ul>"
               if lessons else "")
    art_css = "".join(f".a{key}{{background-image:url(data:image/jpeg;base64,{data})}}"
                      for key, data in images.items() if data)
    title = "The Giant’s Drink"
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title} · a playthrough</title>'
            f"<style>{STYLE}{art_css}</style></head><body><main><h1>{title}</h1>"
            f'<p class="meta">{escape(" · ".join(fact for fact in facts if fact))}</p>'
            f'{"".join(body) or "<p>No turns yet.</p>"}{learned}</main></body></html>')
