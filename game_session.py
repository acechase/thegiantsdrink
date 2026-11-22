"""One game session shared by the terminal and browser interfaces."""

from copy import deepcopy
from dataclasses import dataclass

from adaptation_ai import AdaptationAI
from behavior_analyzer import BehaviorAnalyzer
from command_parser import parse_command
from content_generator import ContentGenerator
from game_world import find_among, noun, world_map
from room_companion import Avatar, Cutscene, Portrait, Scene
from saga import CASTLE_HORIZON, Saga
from simulation_engine import GameState
from story_catalog import StoryError, nomination
from trials import GiantTable
from usage import Meter, metering

HELP_TEXT = """Available commands:
  LOOK          - Look around the current room
  EXAMINE <object> - Inspect something here or in your inventory
  GO <direction> - Move through a listed exit
  TAKE <item>   - Pick up an item
  DROP <item>   - Set something down here
  MAKE A BAG FROM THE TAPESTRY - Turn a carried tapestry into a bag
  PUT <item> IN <bag> / TAKE <item> FROM <bag> - Store or retrieve a carried item
  SEARCH <object> - Investigate a detail, or describe your own experiment
  USE <item>    - Use an item from your inventory
  USE <item> ON <object> - Try a tool on something nearby
  ATTACK <target> - Attack something
  REMEMBER      - Recall lessons from earlier lives
  NOMINATE      - Preview an export of the post-Cup storyline you have reached
  LOOK AT YOURSELF - See what you have on and carry (paints a portrait in the browser)
  INVENTORY     - Check your inventory
  HELP          - Show this help message
  QUIT / EXIT   - Leave the game"""


def _extend(saga, rooms, game_state, profile_summary, report):
    """Plan the arc beyond the open way and say whether it is ready."""
    _, direction = saga.gate
    if saga.plan_next(rooms, game_state, profile_summary=profile_summary, report=report):
        return f"A way {direction} has opened."
    if saga.review_complete:
        return "You have reached the end of the nominated route."
    return f"To the {direction} there is only grey mist so far. Go {direction} when you want to look again."


@dataclass(frozen=True)
class TurnEvent:
    """A player-visible message in the order it occurred."""

    kind: str
    text: str


class GameSession:
    """Own one player's world and run its commands in order."""

    def __init__(self, initial_world=None, *, edition="", plans=(), catalog_path=None):
        """Start at the castle entrance with a private copy of the world."""
        self.rooms = deepcopy(world_map if initial_world is None else initial_world)
        self.game = GameState()
        self.behavior = BehaviorAnalyzer()
        self.adaptation = AdaptationAI()
        self.content = ContentGenerator()
        self.saga = Saga(edition=edition, preferred_plans=plans, catalog_path=catalog_path)
        self.ended = False
        self.portrait = None  # Shown in place of the room illustration until the next turn.
        self.cutscene = None  # The latest discovery stays visible as the player continues.
        self.cutscene_turn = 0
        self.discovered_actions = set()
        self.meter = Meter()  # AI tokens this game has spent, kept with its saves.
        self.scene = Scene.from_room(self.room)
        self.description = self.scene.description

    @classmethod
    def for_story_review(cls, plans):
        """Start an isolated playtest at the crossing after the Giant's Cup."""
        session = cls(plans=tuple(plans))
        session.saga.reviewing = True
        session.game.current_room = "Giant's Hall"
        session.game.giant_defeated = True
        session.saga.begin(session.rooms, "The giant’s bargain is broken.")
        session.saga.plan_next(session.rooms, session.game)
        if not session.saga.arcs:
            raise StoryError("The nominated storyline could not be opened for playtesting.")
        session.scene = Scene.from_room(session.room)
        session.description = session.scene.description
        return session

    def story_nomination(self):
        """Return only the playable plans for the story this player has reached."""
        if self.saga.reviewing:
            raise StoryError("This game is a nomination playtest.")
        return nomination([arc.plan for arc in self.saga.reached_arcs])

    @property
    def room(self):
        """Return the room the player occupies."""
        return self.rooms[self.game.current_room]

    def public_state(self):
        """Expose only the current visible scene, directions, and carried items."""
        room = self.room
        exits = list(self.scene.exits)
        gate = self.saga.gate
        if gate and not self.saga.review_complete and gate[0] == room.name and gate[1] not in exits:
            exits.append(gate[1])
        return {
            "name": room.name, "description": self.description,
            "chapter": self.saga.arcs[room.arc - 1].title if room.arc else "The Castle",
            "items": self.scene.items, "scenery": self.scene.scenery, "exits": exits,
            "inventory": [item.name for item in self.game.inventory], "worn": list(self.game.worn),
            "containers": {item.name: [stored.name for stored in item.contents]
                           for item in self.game.inventory if item.contents is not None},
            "trail": [{"way": way, "room": name} for way, name in self.game.trail[-4:]], "ended": self.ended,
            "life": self.game.deaths + 1, "lessons": list(self.game.lessons),
            "story_edition": self.saga.edition,
            "can_nominate": (not self.saga.reviewing and bool(self.saga.reached_arcs)
                             and all(arc.plan for arc in self.saga.reached_arcs)),
            "offers": [f"Drink from the {side} cup" for side in ("left", "right") if side not in room.trial.spilled]
            if isinstance(room.trial, GiantTable) else [],
        }

    def show(self, viewer):
        """Present the current room or portrait and the latest discovery independently."""
        if self.portrait:
            viewer.show_portrait(self.portrait)
        else:
            viewer.show(self.room, description=self.description)
        if self.cutscene and getattr(viewer, "cutscenes", None):
            viewer.cutscenes.present(self.cutscene)

    def turn_art(self, viewer):
        """Use a discovery painting for its turn in the journal."""
        return viewer.journal_art(cutscene=self.cutscene_turn == len(self.game.history))

    def turn(self, command, on_event=None):
        """Resolve one command, delivering each event live and returning the ordered transcript."""
        with metering(self.meter):
            return self._turn(command, on_event)

    def _turn(self, command, on_event):
        command = command.strip()
        events = []
        if self.ended or not command:
            return events

        def emit(kind, text):
            event = TurnEvent(kind, text)
            events.append(event)
            if on_event:
                on_event(event)

        def report(text):
            emit("activity", text)

        self.portrait = None
        self.game.discovery = None
        self.game.history.append(command)
        previous = self.room
        action, target = parse_command(command, previous, self.game.inventory, report=report)
        if action:
            report(f"[Procedural] Resolving {action.replace('_', ' ')}.")
        else:
            report("[Procedural] Using the unknown-command response.")
        if action == "quit":
            self.ended = True
            emit("outcome", "Thanks for playing The Giant's Drink!")
            return events

        previous_deaths = self.game.deaths
        before, outfit = Scene.from_room(previous), Avatar.of(self.game)
        result = self._resolve(command, action, target, report)
        discovery, self.game.discovery = self.game.discovery, None
        self.game.prune_worn()
        profile = self.behavior.get_profile()
        summary = self.behavior.get_profile_summary()
        self.adaptation.adjust_difficulty(profile, summary)
        self.adaptation.adapt_world(self.rooms, profile, summary)
        if self.game.deaths != previous_deaths:
            self.scene = Scene.from_room(self.room)
            self.description = self.scene.description
            emit("death", result)
            emit("lesson", self.game.last_lesson)
            emit("respawn", f"Life {self.game.deaths + 1}. You wake at the {self.room.name}.")
            emit("arrival", self.game.get_current_description(self.rooms))
            return events
        emit("outcome", result)
        if discovery:
            kind, subject, tool, motion = discovery
            identity = (before.name, kind, subject, tool)
            if identity not in self.discovered_actions:
                self.discovered_actions.add(identity)
                self.cutscene = Cutscene(before, outfit, motion, result)
                self.cutscene_turn = len(self.game.history)
        if action == "self":
            self.portrait = Portrait.of(self.game, self.room, result)
        if self.game.giant_defeated and not self.saga.started:
            emit("milestone", "The giant’s bargain is broken.")
            self.saga.begin(self.rooms, result)
            emit("horizon", CASTLE_HORIZON)
            emit("story", _extend(self.saga, self.rooms, self.game, summary, report))
        if self.game.breakthrough:
            gate_room, self.game.breakthrough = self.game.breakthrough, None
            arc = self.saga.arcs[gate_room.arc - 1]
            self.saga.record(f"{arc.title}: {result}")
            self.saga.open_way(gate_room, gate_room.challenge.blocks, arc.horizon)
            self.behavior.update_profile("discover")
            emit("horizon", arc.horizon)
            emit("story", _extend(self.saga, self.rooms, self.game, summary, report))

        room = self.room
        scene = Scene.from_room(room)
        if scene != self.scene or (action == "look" and target in ("around", "room")):
            self.description = scene.description
        self.scene = scene
        if room is not previous:
            if room.generated:
                banner = self.saga.arrive(room.name)
                if banner:
                    emit("chapter", banner)
                report("[Cached AI] Using the planned room description.")
            elif room.trial:
                # The warning and current puzzle state must be visible before any fatal choice.
                report("[Procedural] Reading the encounter.")
            else:
                self.description = self.content.generate_description(
                    room.name, profile, description=room.prose(), items=list(scene.items),
                    profile_summary=summary, report=report,
                )
                room.remember(self.description)
            emit("arrival", self.game.get_current_description(self.rooms, description=self.description))
        return events

    def _resolve(self, command, action, target, report):
        game, rooms, room = self.game, self.rooms, self.room
        if action == "remember":
            return "\n".join(game.lessons) or "This is your first life. You have no lessons to recall yet."
        if action == "self":
            return game.appearance()
        if action == "trial" and room.trial and target in room.trial.intents:
            result = room.trial.resolve(target, game, rooms)
            if target == "follow edge":
                self.behavior.update_profile("move")
            elif game.giant_defeated:
                self.behavior.update_profile("discover")
            return result
        if action == "move" and target:
            waiting = self.saga.gate == (room.name, target)
            if waiting and self.saga.review_complete:
                return "You have reached the end of the nominated route."
            if waiting:
                waiting = not self.saga.plan_next(
                    rooms, game, profile_summary=self.behavior.get_profile_summary(), report=report
                )
            if waiting:
                result = "Only grey mist lies that way. The next part of the world is still taking shape."
            else:
                lives_lost = game.deaths
                result = game.move(target, rooms)
                if game.current_room != room.name and game.deaths == lives_lost:
                    self.behavior.update_profile("move")
        elif action == "craft_bag":
            result = game.make_tapestry_bag()
        elif action in ("pack", "unpack"):
            result = game.transfer_bag_item(target.tool, target.subject, unpack=action == "unpack")
        elif action == "take" and target:
            held = len(game.inventory)
            result = game.take_item(target, rooms, command=command, report=report)
            if len(game.inventory) > held:
                self.behavior.update_profile("take")
        elif action == "drop":
            result = game.drop_item(target, rooms)
        elif action == "use" and target:
            difficulty = self.adaptation.get_difficulty()
            held = find_among(target, game.inventory, room.items, room.scenery)
            if held in game.inventory:
                result = game.use_item(held.name, difficulty, world_map=rooms, command=command, report=report)
            else:
                result = game.investigate(target, rooms, command, report=report)
            self.behavior.update_profile("use")
            if game.book_knowledge:
                self.behavior.mark_book_read()
        elif action == "use_on":
            was_open = room.passage_open
            result = game.use_on(
                target.tool, target.subject, rooms, self.adaptation.get_difficulty(), command=command, report=report
            )
            if room.passage_open and not was_open:
                self.behavior.update_profile("discover")
        elif action == "attack" and target and "giant" in noun(target).split():
            difficulty = self.adaptation.get_difficulty()
            result = game.attack(target, difficulty, world_map=rooms)
            self.behavior.update_profile("attack")
        elif action == "check" and target == "inventory":
            result = (
                "Inventory: " + ", ".join(
                    item.name + (" (worn)" if item.name in game.worn else "")
                    + (f" [{', '.join(stored.name for stored in item.contents) or 'empty'}]"
                       if item.contents is not None else "")
                    for item in game.inventory
                )
                if game.inventory
                else "Your inventory is empty."
            )
            self.behavior.update_profile("check")
        elif action == "look" and target in ("around", "room"):
            result = game.get_current_description(rooms)
        elif action in ("examine", "look"):
            result = game.examine(target, rooms, command=command, report=report)
        elif action in ("interact", "search", "attack"):
            result = game.investigate(target, rooms, command, search=action == "search", report=report)
        elif action == "help":
            result = HELP_TEXT
        else:
            result = "I do not understand that command. Type HELP for a list of commands."
        return result
