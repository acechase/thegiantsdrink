"""Authored rules for the castle's two repeatable encounters."""

import re


class FallingStairs:
    """The loose center falls away while the outer edge carries weight."""

    intents = {"follow edge": "climb along the outside edge, keeping to the banister or wall"}
    fixtures = ("banisters",)

    def __init__(self):
        """Start before the player has found the safe crossing."""
        self.mastered = False

    def prose(self):
        """Show the danger or the route the player has learned."""
        if self.mastered:
            return "You know the narrow tread beside the banister. You can climb it again."
        return ("A chip of marble rolls onto the center tread. The whole flight drops into the dark, then forms again. "
                "A thin seam of dust beside the banister stays level.")

    def interpret(self, text):
        """Recognize an explicit attempt to follow the surviving footing."""
        if re.fullmatch(r"(?:follow|hug|hold|use|take|climb|walk|go|keep|stick) "
                        r"(?:(?:up|along|to|by|beside|against|next to|close to|the|outer|outside|narrow|safe) )*"
                        r"(?:edge|ledge|wall|rail|banisters?|handrail)(?: up)?", text):
            return "follow edge"

    def resolve(self, intent, game, rooms):
        """Cross and remember the route for subsequent lives."""
        if not self.mastered:
            game.discover("cross", "banister", "Edge up along the banister as the center of the staircase falls away")
        self.mastered = True
        return "You press close to the carved rail. The center drops away under empty air. " + game.move("up", rooms)


class GiantTable:
    """Two losing choices and a way through the bargain itself."""

    intents = {
        "drink left": "commit to drinking or choosing the left cup",
        "drink right": "commit to drinking or choosing the right cup",
        "spill left": "deliberately spill, overturn or empty the left cup",
        "spill right": "deliberately spill, overturn or empty the right cup",
        "spill both": "deliberately overturn both drinks or upend the tray",
        "enter reflection": "cross into the reflected eye, or climb into the giant's eye while his gaze is in the spill",
        "refuse": "decline the bargain without doing anything to the cups",
    }
    fixtures = ("left cup", "right cup", "silver tray", "giant")

    def __init__(self):
        """Set two full cups on the tray."""
        self.spilled = set()

    def prose(self):
        """Describe only the current arrangement of the drinks."""
        if len(self.spilled) == 2:
            return ("Both cups lie empty. The two spills meet. In the silver tray the giant's reflected eye swings "
                    "open like a door. Wind blows up from a country on its other side.")
        if self.spilled:
            side = next(iter(self.spilled))
            return (f"The {side} cup lies on its side. The giant reaches across the silver tray to set it upright. "
                    "His reflection is a beat slower than his hand. The other cup is still full.")
        return ('On a silver tray the left cup holds a white cloud. A black tide rises inside the right cup. '
                '“One will take you beyond the castle,” the giant says. “Choose.” '
                'He watches your hands. In the tray his reflection is watching the cups.')

    def interpret(self, text):
        """Recognize ordinary ways of choosing, spilling, or stepping outside the bargain."""
        cup = re.fullmatch(r"(drink(?: from)?|sip(?: from)?|taste|choose|pick|tip(?: over)?|knock over|spill|empty|overturn) "
                           r"(?:the |my )?(left|right|both|two)(?:-hand)?(?: (?:cups?|glass(?:es)?|drinks?|one|liquids?))?"
                           r"(?: over)?", text)
        if cup:
            verb, side = cup.groups()
            if verb.split()[0] in {"drink", "sip", "taste", "choose", "pick"}:
                return "drink right" if side == "right" else "drink left"
            return "spill " + ("both" if side == "two" else side)
        if re.fullmatch(r"(?:tip(?: over)?|knock over|overturn|upend|spill|empty) (?:the )?"
                        r"(?:silver tray|tray|cups|drinks|table)(?: over)?", text):
            return "spill both"
        if re.fullmatch(r"(?:enter|step (?:into|through)|walk through|jump into|climb (?:in|into)|go through|dive into) "
                        r"(?:the |his )?(?:giant['’]s )?(?:reflected eye|reflection|eye|door in the tray)", text):
            return "enter reflection"
        if text in ("refuse", "refuse to choose", "choose neither", "neither", "refuse both cups"):
            return "refuse"

    def resolve(self, intent, game, rooms):
        """Apply an intentional action to the current table."""
        if intent.startswith("drink "):
            side = intent.split()[1]
            if side in self.spilled:
                return f"The {side} cup is empty. A drop runs down the rim."
            if side == "left":
                return game.table_death(rooms, "The cloud takes your shape. It floats away wearing your life.",
                                        "The white drink took my life. The giant was watching for my choice.")
            return game.table_death(rooms, "One sip sends a black tide over the rim. The castle goes under with you.",
                                    "The black drink took my life. I could move the cup before I drank.")
        if intent.startswith("spill "):
            side = intent.split()[1]
            sides = {"left", "right"} if side == "both" else {side}
            if sides <= self.spilled:
                return "The empty glass rolls against your foot."
            changed = sides - self.spilled
            cups = "both cups" if len(changed) == 2 else f"the {next(iter(changed))} cup"
            game.discover("spill", "giant's cups", f"Tip over {cups} on the giant's silver tray")
            self.spilled.update(sides)
            rooms[game.current_room].seen_details.clear()
            return self.prose()
        if intent == "enter reflection":
            if len(self.spilled) == 2:
                game.giant_defeated = True
                game.discover("enter", "giant's reflection", "Step through the giant's reflected eye in the silver tray")
                return ("You step through the eye in the silver. For one instant you see yourself from his side of "
                        "the table, small and terribly determined. When you turn back, the giant's chair is empty.")
            return "Your hand meets cold silver. Each upright cup pins part of the giant's reflection to the tray."
        return "The giant waits. His hands hover over the cups as though the glasses themselves could disobey."
