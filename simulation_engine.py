# simulation_engine.py
import re

from characters import STOCK
from environment_actions import resolve_environment
from game_world import OPPOSITE, find_among, find_object, noun
from game_world.gear import (
    GearError,
    WornEquipment,
    choose_item,
    default_capabilities,
    item_labels,
    visible_attachments,
    walk_items,
)
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
        self.next_item_number = 1
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
        return [(item, item.worn) for item in self.inventory if item.worn]

    @property
    def worn(self):
        """Describe worn equipment by its player-facing name for prompts and older clients."""
        self.number_items(self.inventory)
        return WornEquipment(self.inventory)

    def number_items(self, roots):
        """Give newly encountered items permanent numbers for commands and saves."""
        for item in walk_items(roots):
            if item.number is None:
                item.number = self.next_item_number
                self.next_item_number += 1

    def carried_names(self):
        """Reserve every possessed item, including gear stored or attached several levels down."""
        return [item.name for item in walk_items(self.inventory)]

    def make_tapestry_bag(self, material="tapestry"):
        """Make a container from a tapestry the player has already taken."""
        tapestry = choose_item(material, self.inventory)
        if tapestry is None:
            return "Take a tapestry first, then turn it into a bag."
        if tapestry.contents is not None:
            return "You already have a tapestry bag."
        if tapestry.puzzle_arc:
            return tapestry.description
        self.convert_tapestry(tapestry)
        tapestry.description = "A roomy fabric bag with knotted corners and a shoulder strap."
        self.discover("craft", "tapestry", "Gather and knot the tapestry into a shoulder bag")
        return "You knot the tapestry into a roomy bag. Try ‘put salt in bag’ with an item you’re carrying."

    def convert_tapestry(self, tapestry):
        """Preserve the item's identity and worn state when its name changes."""
        tapestry.make_tapestry_bag()
        if tapestry.worn:
            tapestry.worn = "The tapestry bag hangs from your shoulders by its knotted strap."

    def craft_gear(self, material, product, room, command, report):
        """Resolve familiar forms directly and adjudicate the feasibility of other designs."""
        item = choose_item(material, self.inventory)
        if item is None:
            return f"Pick up the {material} before crafting with it."
        if item.puzzle_arc:
            return item.description
        source = item.name.split()[-1]
        form = product.split()[-1]
        cloth = source in ("cloak", "scarf", "cloth", "linen", "tapestry", "tapestries")
        if ((cloth and form in ("bag", "backpack", "satchel", "pouch", "sack"))
                or (source in ("reeds", "wicker", "willow") and form == "basket")):
            storage, points = default_capabilities(product)
            original = item.name
            item.refashion(product, storage=storage, attachment_points=points)
            item.description = f"A {product} fashioned from the {original}."
            if item.worn:
                item.worn = f"You wear the {product}."
            self.discover("craft", product, command)
            return f"You fashion the {original} into a {product}."
        return resolve_environment(self, room, command, item.reference, report=report, craft_goal=product)

    def appearance(self):
        """Describe the player and what they wear and carry."""
        def listed(names):
            names = [f"the {name}" for name in names]
            return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

        worn = [how for _, how in self.wearing()]
        held = [item.name for item in self.inventory if not item.worn]
        return " ".join([
            "You look yourself over.", self.character.description,
            *(worn or ["You have nothing from the castle on."]),
            f"You carry {listed(held)}." if held else "Your hands are empty.",
            *(fact for item in self.inventory for fact in visible_attachments(item)),
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
            item = choose_item(item_name, room.items)
            self.inventory.append(item)
            room.items.remove(item)
            item.room_text = ""
            if item not in room.departed:
                room.departed.append(item)
            return f"You take the {item.name}."
        held = found if found in self.inventory else None
        if held:
            held = choose_item(item_name, self.inventory)
            if self._taking_off(held, command):
                return self._take_off(held)
            return f"You're already carrying the {held.name}."
        return resolve_environment(self, room, command, item_name, report=report,
                                   fallback="There is no such item here." if not room.landmark(item_name) else None)

    def _taking_off(self, item, command):
        return item.worn and re.search(r"\b(?:off|remove|doff)\b", (command or "").casefold())

    def _take_off(self, item):
        item.worn = ""
        return f"You take off the {item.name} and carry it."

    def drop_item(self, reference, world_map):
        """Return a carried object to the current room."""
        item = choose_item(reference, self.inventory)
        if not item:
            return f"You're not carrying the {reference}."
        self.inventory.remove(item)
        item.worn = ""
        room = world_map[self.current_room]
        room.items.append(item)
        if item in room.departed:
            room.departed.remove(item)
        return f"You set down the {item.name}."

    def examine(self, reference, world_map, *, command=None, report=None):
        """Inspect a carried item or an object at the player's location."""
        room = world_map[self.current_room]
        labels = item_labels([*self.inventory, *room.items, *room.scenery])
        obj = choose_item(reference, labels)
        if not obj:
            return resolve_environment(self, room, command, reference, report=report)
        passage = room.passage
        if room.passage_open and obj.name == passage.landmark:
            return passage.visible
        details = [obj.description]
        if obj.contents is not None:
            contents = ", ".join(labels[item] for item in obj.contents)
            details.append(f"Inside: {contents}." if contents else "It is empty.")
        if obj.attachments is not None:
            attached = ", ".join(labels[item] for item in obj.attachments)
            details.append(f"Attached: {attached}." if attached else "There is room to fasten something outside.")
        if obj.worn:
            details.append(obj.worn)
        return " ".join(details)

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
        tool = choose_item(tool_name, self.inventory)
        if not tool:
            if find_object(tool_name, room.items) or room.landmark(tool_name):
                return f"You need to be carrying the {tool_name} first."
            return self.investigate(target_name, world_map, command, report=report)
        target = choose_item(target_name, [*room.scenery, *room.items, *self.inventory])
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
        return resolve_environment(self, room, command, item.reference, report=report, fallback=prompt)

    def use_item(self, item_name, difficulty=1.0, *, world_map=None, command=None, report=None):
        """Use an item from the player's inventory, triggering its effect.

        item_name (str): Name or partial name of the item to use (case-insensitive).
        difficulty (float, optional): Current game difficulty multiplier. Defaults to 1.0.
        world_map, command, report: When supplied, a described use with no written effect is judged by the model.

        str: Description of the item's effect or error message.
        """
        item = choose_item(item_name, self.inventory)
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
                if item.contents or item.attachments:
                    raise GearError(f"Remove everything from the {item.name} first.")
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
                if item.worn:
                    return f"The {item.name} is already on your head."
                item.worn = f"The {item.name} sits low over your brow."
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
            labels = item_labels([*self.inventory, *room.items, *room.scenery])
            description += " You see: " + ", ".join(labels[item] for item in room.items)
        description += f"\nExits: {', '.join(room.visible_exits)}"
        return description
