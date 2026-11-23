"""Immutable room and character snapshots, including their illustration prompts."""

from dataclasses import dataclass

from characters import STOCK

STYLE = (
    "Landscape environmental painting for a surreal fairy-tale castle exploration game. "
    "Paint from the player's eye level with fine gouache brushwork, restrained slate blues, "
    "earthy umber and localized warm light. Give the architecture a clear silhouette and tangible scale. "
    "Keep the same illustrated field-guide style across rooms. Show only visible clues, scenery and "
    "objects described below. Players can try to interact with anything they see, so do not add furniture, "
    "containers, rugs, props, creatures or decorations that the text does not mention. Leave out unseen "
    "destinations, the player, lettering, icons and interface elements."
)
PAINTING = (
    "in the same illustrated field-guide style as the game's room paintings: fine gouache brushwork, restrained "
    "slate blues, earthy umber and localized warm light"
)
OUTFIT_RULES = (
    "Show every listed worn and carried thing exactly as described, and add no other gear, armor, weapons, jewelry, "
    "bags or capes. No lettering anywhere, including book covers, and no icons or interface elements."
)
PORTRAIT_STYLE = (
    f"Full-length character portrait for a surreal fairy-tale castle exploration game, {PAINTING}. "
    "{figure} Pose them standing in three-quarter view at the center of a landscape frame, head and feet in view. "
    f"{OUTFIT_RULES} Behind the figure, loosely suggest the room named below in soft, unfocused shapes without "
    "distinct props."
)
AVATAR_STYLE = (
    f"Full-length character card for a surreal fairy-tale castle exploration game, {PAINTING}. "
    "{figure} Pose them standing in three-quarter view and filling a tall frame, head and feet in view. "
    f"{OUTFIT_RULES} Set the figure against a plain, softly mottled parchment-and-slate wash with no setting."
)
CUTSCENE_STYLE = (
    f"Landscape story illustration for a surreal fairy-tale castle exploration game, {PAINTING}. "
    "{figure} "
    "Use an outside camera angle and show the character in the middle of the action, with their hands, footing, "
    "and the thing they are interacting with clear. Capture one moment of motion. Use the supplied setting and "
    "only the visible objects and equipment described. The action may move an object or change how it is held. "
    f"{OUTFIT_RULES}"
)


def _figure(look):
    return (f'Show the player character, described to the player in the second person: "{look}" '
            "Paint exactly that person. Layer anything listed as worn below over their base clothes.")


def _image_request(model, prompt, size="1536x1024"):
    return {"model": model, "prompt": prompt, "size": size, "quality": "low", "output_format": "jpeg", "n": 1}


def _outfit(game):
    """Return what the player wears (name, how, look) and carries (name, look) right now."""
    worn = tuple((item.name, how, item.description[:240]) for item, how in game.wearing())
    carried = tuple((item.name, item.description[:240]) for item in game.inventory if item.name not in game.worn)
    return worn, carried


def _outfit_prompt(worn, carried):
    worn = " ".join(f"{name}: {how} ({look})" for name, how, look in worn) or "nothing from the castle."
    carried = " ".join(f"{name}: {look}" for name, look in carried) or "nothing; their hands are empty."
    return f"Worn: {worn}\nCarried in hand or at the belt: {carried}"


# Outfit fields also appear in older saved portraits and cutscenes.
WornItems = tuple[tuple[str, str, str], ...]  # name, how it is worn, description
CarriedItems = tuple[tuple[str, str], ...]  # name, description


@dataclass(frozen=True)
class Scene:
    """A snapshot of the visible world, independent of subsequent game turns."""

    name: str
    description: str
    items: tuple[str, ...]
    scenery: tuple[str, ...]
    exits: tuple[str, ...]
    kind = "room"

    @classmethod
    def from_room(cls, room):
        """Capture what a player can currently see."""
        items, scenery = tuple(i.name for i in room.items), tuple(i.name for i in room.scenery)
        return cls(room.name, room.prose(), items, scenery, tuple(room.visible_exits))

    def request(self, model):
        """Build the image request used both for generation and cache identity."""
        prompt = (
            f"{STYLE}\nRoom: {self.name}\n{self.description}\n"
            f"Visible loose items: {', '.join(self.items) or 'none'}. "
            f"Landmarks: {', '.join(self.scenery) or 'none'}. Visible exits: {', '.join(self.exits) or 'none'}."
        )
        return _image_request(model, prompt)


@dataclass(frozen=True)
class Portrait:
    """The player as they look at one moment: what they have on and what they carry."""

    place: str
    description: str
    worn: WornItems
    carried: CarriedItems
    exits: tuple[str, ...]
    look: str = STOCK.description  # Portraits from older saves have no look.
    kind = "portrait"
    name = "Your portrait"
    scenery = ()

    @classmethod
    def of(cls, game, room, description):
        """Capture the player and their current outfit."""
        return cls(room.name, description, *_outfit(game), tuple(room.visible_exits), game.character.description)

    @property
    def items(self):
        """Name everything on the figure for the read-only companion."""
        return tuple(name for name, *_ in (*self.worn, *self.carried))

    def request(self, model):
        """Build the image request used both for generation and cache identity."""
        style = PORTRAIT_STYLE.format(figure=_figure(self.look))
        prompt = f"{style}\nRoom behind the figure: {self.place}\n{_outfit_prompt(self.worn, self.carried)}"
        return _image_request(model, prompt)


@dataclass(frozen=True)
class Avatar:
    """The always-visible character card: the player's outfit without a setting, so moving never repaints it."""

    worn: WornItems
    carried: CarriedItems
    look: str = STOCK.description  # Outfits in older cutscenes have no look.
    kind = "avatar"
    name = "You"
    description = ""
    scenery = ()
    exits = ()

    @classmethod
    def of(cls, game):
        """Capture the player and their current outfit."""
        return cls(*_outfit(game), game.character.description)

    @property
    def items(self):
        """Name everything on the figure."""
        return tuple(name for name, *_ in (*self.worn, *self.carried))

    def request(self, model):
        """Build the image request used both for generation and cache identity."""
        style = AVATAR_STYLE.format(figure=_figure(self.look))
        return _image_request(model, f"{style}\n{_outfit_prompt(self.worn, self.carried)}", "1024x1536")


@dataclass(frozen=True)
class Cutscene:
    """A successful action with the setting and outfit captured when it began."""

    scene: Scene
    outfit: Avatar
    action: str
    description: str
    kind = "cutscene"
    items = scenery = exits = ()

    @property
    def name(self):
        """Identify the discovery's original location."""
        return f"Discovery in {self.scene.name}"

    def request(self, model):
        """Compose a cinematic moment from the actual action and equipment."""
        style = CUTSCENE_STYLE.format(figure=_figure(self.outfit.look))
        prompt = (
            f"{style}\nSetting: {self.scene.name}. {self.scene.description}\n"
            f"Visible landmarks: {', '.join(self.scene.scenery) or 'none'}. "
            f"Visible loose items: {', '.join(self.scene.items) or 'none'}.\n"
            f"{_outfit_prompt(self.outfit.worn, self.outfit.carried)}\n"
            f"Action in progress: {self.action}\nWhat the action achieves: {self.description}"
        )
        return _image_request(model, prompt)
