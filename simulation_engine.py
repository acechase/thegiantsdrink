# simulation_engine.py
import re

from characters import STOCK
from environment_actions import resolve_environment
from game_world import OPPOSITE, find_among, find_object, noun
from trials import FallingStairs, GiantTable


class GameState:
    """Mutable state of a game session including position, inventory, and flags.

    Tracks the player's current room, collected items, and key progression
    flags (giant defeated, potion active, book read, door unlocked). Provides
    methods for movement, item interaction, combat, and room description.

    Attributes:
        current_room: Name of the room the player is currently in.
        inventory: List of Item objects the player is carrying.
        giant_defeated: Whether the giant has been defeated.
        potion_active: Whether the magic potion buff is active.
        book_knowledge: Whether the player has read the ancient book.
        door_unlocked: Whether the Victory Chamber door is unlocked.
    """

    def __init__(self):
        """Initialize a new game state in the Starting Room with empty inventory."""
        self.character = STOCK
        self.current_room = "Starting Room"
        self.inventory = []
        self.giant_defeated = False
        self.potion_active = False
        self.book_knowledge = False
        self.door_unlocked = False
        self.history = []
        self.deaths = 0
        self.table_deaths = 0
        self.lessons = []
        self.last_lesson = ""
        self.worn = {}  # Carried item name -> how the player wears it, in one second-person sentence.
        self.trail = []  # (direction or "woke", room name) for each arrival, most recent last.
        self.breakthrough = None  # A room whose obstacle the player has just overcome.
        self.discovery = None  # A successful technique from the current command.

    def discover(self, kind, subject, action, *, tool=""):
        """Record the grounded technique and how it should look in motion."""
        self.discovery = kind, noun(subject), noun(tool), action

    def _arrive(self, how, room_name):
        self.current_room = room_name
        self.trail = [*self.trail, (how, room_name)][-12:]

    def wearing(self):
        """Return each carried item the player has on, with how it is worn."""
        return [(item, self.worn[item.name]) for item in self.inventory if item.name in self.worn]

    def prune_worn(self):
        """Forget worn state for anything that has left the player's possession."""
        held = {item.name for item in self.inventory}
        self.worn = {name: how for name, how in self.worn.items() if name in held}

    def carried_names(self):
        """Include packed items when reserving the names of carried gear."""
        return [item.name for held in self.inventory for item in (held, *(held.contents or ()))]

    def make_tapestry_bag(self):
        """Make a container from a tapestry the player has already taken."""
        tapestry = find_object("tapestry", self.inventory)
        if tapestry is None:
            return "Take a tapestry first, then turn it into a bag."
        if tapestry.contents is not None or any(item.name == "tapestry bag" for item in self.inventory):
            return "You already have a tapestry bag."
        self.convert_tapestry(tapestry)
        tapestry.description = "A roomy fabric bag with knotted corners and a shoulder strap."
        self.discover("craft", "tapestry", "Gather and knot the tapestry into a shoulder bag")
        return "You knot the tapestry into a roomy bag. Try ‘put salt in bag’ with an item you’re carrying."

    def convert_tapestry(self, tapestry):
        """Preserve the item's identity and worn state when its name changes."""
        worn = self.worn.pop(tapestry.name, None) if tapestry in self.inventory else None
        tapestry.make_tapestry_bag()
        if worn:
            self.worn[tapestry.name] = "The tapestry bag hangs from your shoulders by its knotted strap."

    def transfer_bag_item(self, item_name, bag_name, *, unpack=False):
        """Move one item between the player's inventory and a carried bag."""
        bag = find_object(bag_name, self.inventory)
        if bag is None or bag.contents is None:
            return f"You're not carrying a container called {bag_name}."
        source, destination = (bag.contents, self.inventory) if unpack else (self.inventory, bag.contents)
        item = find_object(item_name, source)
        if item is None:
            return (f"The {bag.name} doesn't contain {item_name}." if unpack else f"You're not carrying {item_name}.")
        if item.contents is not None:
            return "Keep containers separate."
        source.remove(item)
        self.worn.pop(item.name, None)
        destination.append(item)
        if unpack:
            return f"You take the {item.name} out of the {bag.name}."
        return f"You put the {item.name} in the {bag.name}."

    def appearance(self):
        """Describe the player and what they wear and carry."""
        def listed(names):
            names = [f"the {name}" for name in names]
            return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

        worn = [how for _, how in self.wearing()]
        held = [item.name for item in self.inventory if item.name not in self.worn]
        return " ".join([
            "You look yourself over.", self.character.description,
            *(worn or ["You have nothing from the castle on."]),
            f"You carry {listed(held)}." if held else "Your hands are empty.",
        ])

    def die(self, cause, lesson, checkpoint):
        """Start the next life at a checkpoint and keep the player's discoveries and possessions."""
        self.deaths += 1
        self.last_lesson = lesson
        if lesson not in self.lessons:
            self.lessons.append(lesson)
        self._arrive("woke", checkpoint)
        self.potion_active = False
        return f"{cause} You die."

    def table_death(self, world_map, cause, lesson):
        """Refill the glasses and send repeated failures farther back into the castle."""
        if world_map is not None:
            hall = world_map["Giant's Hall"]
            if isinstance(hall.trial, GiantTable):
                hall.trial.spilled.clear()
                hall.seen_details.clear()
        checkpoints = ("Giant's Hall", "Upper Landing", "Great Staircase", "Starting Room")
        checkpoint = checkpoints[min(self.table_deaths, len(checkpoints) - 1)]
        self.table_deaths += 1
        return self.die(cause, lesson, checkpoint)

    def move(self, direction, world_map):
        """Move the player in a given direction if the exit exists.

        direction (str): Cardinal direction (north, south, east, west, up, down).
        world_map (dict): Mapping of room names to Room objects.

        str: Description of the move result or error message.
        """
        room = world_map[self.current_room]
        challenge = room.challenge
        if challenge and not challenge.resolved and direction == challenge.blocks:
            if not challenge.warned:
                challenge.warned = True
                return f"{challenge.barrier} {challenge.warning}"
            return self.die(f"{challenge.barrier} You step past your shadow and the world closes over you.",
                            f"At {room.name}, I must change the {challenge.obstacle} before going {direction}.",
                            challenge.checkpoint or room.name)
        if isinstance(room.trial, FallingStairs) and direction == "up" and not room.trial.mastered:
            if self.potion_active:
                self.potion_active = False
                return "The stair drops. The potion gives you one last grip on the outer rail before its strength fades."
            return self.die("The marble under your feet becomes a white waterfall. You fall with it.",
                            "On the Great Staircase, the outer edge beside the banister held.", room.name)
        if direction in room.exits:
            target_room = room.exits[direction]
            # Victory Chamber requires unlocked door
            if target_room == "Victory Chamber" and not self.door_unlocked:
                return "The iron door is locked. You need a key."
            self._arrive(direction, target_room)
            return f"You move to the {self.current_room}."
        return "You can't go that way."

    def take_item(self, item_name, world_map, *, command=None, report=None):
        """Pick up an item from the current room and add it to inventory.

        item_name (str): Name or partial name of the item to take (case-insensitive).
        world_map (dict): Mapping of room names to Room objects.

        str: Success message or error message if item not found.
        """
        room = world_map[self.current_room]
        found = find_among(item_name, room.items, self.inventory, room.scenery)
        item = found if found in room.items else None
        if item:
            self.inventory.append(item)
            room.items.remove(item)
            item.room_text = ""
            if item not in room.departed:
                room.departed.append(item)
            return f"You take the {item.name}."
        held = found if found in self.inventory else None
        if held and self._taking_off(held, command):
            return self._take_off(held)
        if held:
            return f"You're already carrying the {held.name}."
        return resolve_environment(self, room, command, item_name, report=report,
                                   fallback="There is no such item here." if not room.landmark(item_name) else None)

    def _taking_off(self, item, command):
        return item.name in self.worn and re.search(r"\b(?:off|remove|doff)\b", (command or "").casefold())

    def _take_off(self, item):
        del self.worn[item.name]
        return f"You take off the {item.name} and carry it."

    def drop_item(self, reference, world_map):
        """Return a carried object to the current room."""
        item = find_object(reference, self.inventory)
        if not item:
            return f"You're not carrying the {reference}."
        self.inventory.remove(item)
        self.worn.pop(item.name, None)
        room = world_map[self.current_room]
        room.items.append(item)
        if item in room.departed:
            room.departed.remove(item)
        return f"You set down the {item.name}."

    def examine(self, reference, world_map, *, command=None, report=None):
        """Inspect a carried item or an object at the player's location."""
        room = world_map[self.current_room]
        obj = find_object(reference, [*self.inventory, *room.items, *room.scenery])
        if not obj:
            return resolve_environment(self, room, command, reference, report=report)
        if obj.contents is not None:
            contents = ", ".join(item.name for item in obj.contents)
            return f"{obj.description} Inside: {contents}." if contents else f"{obj.description} It is empty."
        passage = room.passage
        if room.passage_open and obj.name == passage.landmark:
            return passage.visible
        if obj in self.inventory and obj.name in self.worn:
            return f"{obj.description} {self.worn[obj.name]}"
        return obj.description

    def investigate(self, reference, world_map, command, *, search=False, tool=None, report=None):
        """Reveal authored clues or adjudicate the player's own environmental experiment."""
        room = world_map[self.current_room]
        obj = find_object(reference, [*room.scenery, *room.items, *self.inventory])
        if obj and obj.discovery and (search or (tool and tool.name in obj.discovery.tools)):
            if obj.name in room.discoveries:
                return room.discoveries[obj.name]
            room.discoveries[obj.name] = obj.discovery.detail
            obj.description = f"{obj.description} {obj.discovery.detail}"
            tool_name = tool.name if tool else ""
            action = f"Probe the {obj.name} with the {tool_name}" if tool_name else f"Search the {obj.name} closely"
            self.discover("search", obj.name, action, tool=tool_name)
            return f"{obj.discovery.response} {obj.discovery.detail}"
        fallback = f"You try the {tool.name} on the {obj.name}. Nothing happens." if tool and obj else None
        return resolve_environment(self, room, command, reference, tool=tool, report=report, fallback=fallback)

    @staticmethod
    def active_challenge(room):
        """Return a room's unresolved obstacle and its scenery object, if any."""
        challenge = room.challenge
        if not challenge or challenge.resolved:
            return None, None
        return challenge, find_object(challenge.obstacle, room.scenery)

    def overcome(self, room, outcome, *, room_after=None, obstacle_description=None, tool="", action=""):
        """Get past a room's obstacle and leave the room changed for good."""
        challenge, obstacle = self.active_challenge(room)
        challenge.resolved = True
        if obstacle:
            obstacle.description = obstacle_description or challenge.obstacle_after
        room.rewrite(room_after or challenge.room_after)
        self.breakthrough = room
        self.discover("overcome", challenge.obstacle, action or f"Overcome the {challenge.obstacle}", tool=tool)
        return outcome

    def use_on(self, tool_name, target_name, world_map, difficulty=1.0, *, command=None, report=None):
        """Resolve an attempted tool interaction against the current world."""
        room = world_map[self.current_room]
        tool = find_object(tool_name, self.inventory)
        if not tool:
            if find_object(tool_name, room.items) or room.landmark(tool_name):
                return f"You need to be carrying the {tool_name} first."
            return self.investigate(target_name, world_map, command, report=report)
        target = find_object(target_name, [*room.scenery, *room.items, *self.inventory])
        if not target:
            return self.investigate(target_name, world_map, command, tool=tool, report=report)

        challenge, obstacle = self.active_challenge(room)
        if obstacle is target:
            solution = challenge.solution_for(tool.name)
            if solution and solution.outcome:
                return self.overcome(room, solution.outcome, tool=tool.name,
                                     action=f"Use the {tool.name} on the {obstacle.name}. Approach: {solution.approach}.")
        passage = room.passage
        if passage and target.name == passage.landmark and tool.name == passage.tool:
            if room.passage_open:
                return passage.visible
            if room.generated:
                if not any(item.name == passage.tool and item.puzzle_arc == room.arc for item in self.inventory):
                    return target.description
                destination = world_map[passage.destination]
                destination.exits[OPPOSITE[passage.direction]] = room.name
            room.exits[passage.direction] = passage.destination
            self.discover("open passage", target.name, f"Use the {tool.name} on the {target.name} to open the passage",
                          tool=tool.name)
            return passage.reveal
        if target.name == "giant" and tool.name == "rusty sword":
            return self.attack("giant", difficulty, world_map=world_map)
        if (tool.name, target.name) in {("key", "iron door"), ("crystal vial", "giant")}:
            return self.use_item(tool.name, difficulty)
        return self.investigate(target.name, world_map, command, tool=tool, report=report)

    def _improvise(self, item, prompt, world_map, command, report):
        """Let the model judge a described use of a carried item that has no written effect."""
        words = noun(command or "").split()
        if world_map is None or not words or (words[0] == "use" and find_object(" ".join(words[1:]), [item]) is item):
            return prompt
        room = world_map[self.current_room]
        return resolve_environment(self, room, command, item.name, report=report, fallback=prompt)

    def use_item(self, item_name, difficulty=1.0, *, world_map=None, command=None, report=None):
        """Use an item from the player's inventory, triggering its effect.

        item_name (str): Name or partial name of the item to use (case-insensitive).
        difficulty (float, optional): Current game difficulty multiplier. Defaults to 1.0.
        world_map, command, report: When supplied, a described use with no written effect is judged by the model.

        str: Description of the item's effect or error message.
        """
        item = find_object(item_name, self.inventory)
        if not item:
            return "You can't use that item or it's not in your inventory."
        if self._taking_off(item, command):
            return self._take_off(item)
        if world_map is not None:
            room = world_map[self.current_room]
            challenge, _ = self.active_challenge(room)
            if challenge and challenge.outcome(item.name):
                return f"You hold the {item.name}. What will you use it on?"
        if item.name == "rusty sword":
            prompt = "You're holding the rusty sword. What would you like to use it on?"
            return self._improvise(item, prompt, world_map, command, report)
        if item.name == "candle":
            prompt = "You lift the candle. Its flame throws your shadow across the room."
            return self._improvise(item, prompt, world_map, command, report)
        if item.usable:
            if "potion" in item.name.lower():
                self.inventory.remove(item)
                self.potion_active = True
                return "You drink the magic potion and feel a surge of strength!"
            elif "book" in item.name.lower():
                self.book_knowledge = True
                return (
                    "You read the ancient book. In one woodcut both cups lie on their sides. The engraver has cut "
                    "a hole straight through the silver at the giant's feet. The next page shows an empty chair."
                )
            elif "key" in item.name.lower():
                if self.current_room in ("Throne Antechamber", "Giant's Hall"):
                    self.door_unlocked = True
                    return "You use the key — the iron door clicks open!"
                return "There is nothing to unlock here."
            elif "crystal vial" in item.name.lower():
                if self.current_room == "Giant's Hall" and not self.giant_defeated:
                    return ("In the crystal vial the cups appear upside down. Between their rims a tiny door opens. "
                            "When you lower the vial the giant is still waiting.")
                return "There is no one here to use this on."
            elif "shield" in item.name.lower():
                prompt = "You raise the silver shield. It might protect you in battle."
                return self._improvise(item, prompt, world_map, command, report)
            elif "torch" in item.name.lower():
                prompt = "The torch flares brightly, pushing shadows to the walls."
                return self._improvise(item, prompt, world_map, command, report)
            elif "helm" in item.name.lower():
                if item.name in self.worn:
                    return f"The {item.name} is already on your head."
                self.worn[item.name] = f"The {item.name} sits low over your brow."
                return f"You put on the {item.name}. You feel tougher."
        return self._improvise(item, f"What would you like to use the {item.name} on?", world_map, command, report)

    def attack(self, target, difficulty=1.0, *, world_map=None):
        """Resolve a direct confrontation with the keeper of the table."""
        if "giant" not in noun(target).split() or self.current_room != "Giant's Hall":
            return "There is nothing to attack here."
        if self.giant_defeated:
            return "The giant is gone. Only a drift of grey dust remains."
        return self.table_death(world_map, "The giant folds the table like a book. You vanish between its pages.",
                                "He can close the table on me. When a cup tips, he reaches to set it straight.")

    def get_current_description(self, world_map, *, description=None):
        """Get a full description of the current room including items and exits.

        world_map (dict): Mapping of room names to Room objects.

        str: Formatted room description with items visible and available exits.
        """
        room = world_map[self.current_room]
        description = description or room.prose()
        if room.items:
            description += " You see: " + ", ".join([item.name for item in room.items])
        description += f"\nExits: {', '.join(room.visible_exits)}"
        return description
