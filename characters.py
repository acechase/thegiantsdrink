"""Starting descriptions for the player character."""

from dataclasses import dataclass
from unicodedata import category

MAX_DESCRIPTION = 600
PRESETS = {
    "child": (
        "The child",
        "You are a small, slight child of about ten, with a mop of dark brown hair, a pale freckled face and steady grey "
        "eyes. Nothing about you says whether you are a boy or a girl. You wear a plain charcoal tunic, dark trousers "
        "and worn brown boots.",
    ),
    "girl": (
        "The girl",
        "You are a girl of about ten, small for your age but quick and sure-footed, with a long dark-brown braid, warm "
        "brown skin, a scatter of freckles across your nose and sharp hazel eyes. You wear a plain charcoal tunic, "
        "dark trousers and worn brown boots.",
    ),
}
DEFAULT = "child"


class CharacterError(ValueError):
    """A character choice the player can correct."""


@dataclass(frozen=True)
class Character:
    """The player's starting description and the preset it came from."""

    preset: str
    description: str

    @property
    def edited(self):
        """Say whether the description differs from the preset."""
        return self.description != PRESETS[self.preset][1]

    @property
    def name(self):
        """Name the preset or the player's own character."""
        return "Your own character" if self.edited else PRESETS[self.preset][0]

    def public(self):
        """Return the character as public game state."""
        return {"preset": self.preset, "name": self.name, "description": self.description, "edited": self.edited}


STOCK = Character(DEFAULT, PRESETS[DEFAULT][1])


def clean(text) -> str:
    """Normalize and validate a player description."""
    if not isinstance(text, str):
        raise CharacterError("Enter a character description.")
    text = "".join(char for char in text if char.isspace() or category(char) not in ("Cc", "Cf", "Cs"))
    text = " ".join(text.split())
    if not text:
        raise CharacterError("Enter a character description.")
    if len(text) > MAX_DESCRIPTION:
        raise CharacterError(f"Keep your character description to {MAX_DESCRIPTION} characters or fewer.")
    return text


def make(preset=DEFAULT, description=None) -> Character:
    """Choose a preset and optionally replace its description."""
    if not isinstance(preset, str) or preset not in PRESETS:
        raise CharacterError("Choose a known character preset.")
    return Character(preset, PRESETS[preset][1] if description is None else clean(description))


def presets_public():
    """List the available presets in chooser order."""
    return [make(preset).public() for preset in PRESETS]
