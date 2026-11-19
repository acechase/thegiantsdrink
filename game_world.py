# game_world.py
import re
from dataclasses import dataclass


def noun(text):
    """Normalize an object reference while retaining meaningful modifiers."""
    words = text.casefold().strip(" .!?").split()
    if words and words[0] in ("a", "an", "the", "my", "your", "teh"):
        words = words[1:]
    return " ".join(words)


def find_object(reference, objects):
    """Resolve a name, alias or unambiguous sequence of whole words."""
    name = noun(reference)
    if not name:
        return None
    named = [obj for obj in objects if obj.name == name]
    if named:
        return named[0]
    partial = []
    for obj in objects:
        names = (obj.name, *obj.aliases)
        if name in names:
            return obj
        if any(f" {name} " in f" {candidate} " for candidate in names):
            partial.append(obj)
    return partial[0] if len(partial) == 1 else None


@dataclass(frozen=True)
class HiddenPassage:
    """An exit a player can discover by using a carried tool on a landmark."""

    tool: str
    landmark: str
    direction: str
    destination: str
    reveal: str
    visible: str


@dataclass(frozen=True)
class Discovery:
    """A detail that a deliberate search or a suitable carried tool reveals."""

    tools: tuple
    response: str
    detail: str


class Room:
    """A location in the game world that the player can visit.

    Attributes:
        name: Unique display name of the room.
        description: Atmospheric text shown when the player enters.
        items: List of Item objects currently in the room.
        exits: Dict mapping direction strings to destination room names.
    """

    def __init__(self, name, description, items=None, exits=None, *, scenery=(), passage=None):
        """Initialize a Room with a name, description, items, and exit directions.

        name (str): Unique name of the room.
        description (str): Atmospheric text description of the room.
        items (list, optional): List of Item objects initially in the room. Defaults to [].
        exits (dict, optional): Mapping of direction -> room name. Defaults to {}.

        """
        self.name = name
        self.description = description
        self.items = items if items else []
        self.exits = exits if exits else {}
        self.scenery = list(scenery)
        self.passage = passage
        self.discoveries = {}
        self.folded = set()
        self.seen_details = []
        self.departed = []

    def remember(self, text):
        """Keep descriptive details the player has actually been shown."""
        if text and text not in self.visible_text():
            # Recent narration is enough to ground references without growing every prompt.
            self.seen_details = [*self.seen_details[-5:], text]

    def visible_text(self):
        """Include observations and generated narration when resolving references."""
        return " ".join([self.prose(), *(obj.description for obj in (*self.scenery, *self.items)), *self.seen_details])

    def fixed_text(self):
        """Describe the room's own features, excluding loose items and opened routes."""
        return " ".join([self.description, *(d for d in self.discoveries.values() if d not in self.folded)])

    def rewrite(self, description):
        """Replace the room's own features after a change, absorbing discoveries already described."""
        self.description = description
        self.folded.update(self.discoveries.values())
        self.seen_details.clear()

    def prose(self):
        """Describe the room as it currently stands."""
        extras = [item.room_text for item in self.items if item.room_text]
        if self.passage_open:
            extras.append(self.passage.visible)
        extras.extend(d for d in self.discoveries.values() if d not in self.folded)
        return " ".join([self.description, *(text for text in extras if text and text not in self.description)])

    def landmark(self, reference):
        """Find authored scenery or ground a phrase in a sentence of visible room prose."""
        fixture = find_object(reference, self.scenery)
        if fixture:
            return fixture
        if find_object(reference, self.departed):
            return None
        name = noun(reference)
        if len(name) < 2 or len(name) > 80:
            return None
        # A singular or plural reference can point to a detail with no special game rule.
        pattern = r"\b" + re.escape(name.removesuffix("s")) + r"s?\b"
        for sentence in re.split(r"(?<=[.!?])\s+", self.visible_text()):
            if re.search(pattern, sentence, re.IGNORECASE):
                return Item(name, sentence)
        return None

    @property
    def passage_open(self):
        """Whether the hidden route is already part of this room's exits."""
        return self.passage is not None and self.exits.get(self.passage.direction) == self.passage.destination


class Item:
    """An object that can be found, carried, and optionally used by the player.

    Attributes:
        name: Display name of the item.
        description: Text description of the item's appearance or purpose.
        usable: Whether the item can be activated with the 'use' command.
    """

    def __init__(self, name, description, usable=False, *, aliases=(), room_text="", discovery=None):
        """Initialize an Item with a name, description, and usability flag.

        name (str): Display name of the item.
        description (str): Text description of the item's appearance/purpose.
        usable (bool, optional): Whether the item can be used with the 'use' command. Defaults to False.

        """
        self.name = name
        self.description = description
        self.usable = usable
        self.aliases = aliases
        self.room_text = room_text
        self.discovery = discovery


# ── Items ────────────────────────────────────────────────────────────

rusty_sword = Item(
    "rusty sword", "Rust freckles the blade. Its heavy pommel still rings clear against stone.", aliases=("blade",)
)
candle = Item(
    "candle",
    "A thumb of warm beeswax cups a wavering blue flame. A curl of wax has hardened along one side.",
    usable=True,
    aliases=("guttering candle", "wax candle"),
    room_text="A single candle gutters on the shelf, casting restless shadows across the walls.",
)
magic_potion = Item(
    "magic potion",
    "Blue light pulses in the glass. The potion might give you strength.",
    usable=True,
    room_text="On the pedestal, a magic potion pulses with an inner blue light.",
)
ancient_book = Item(
    "ancient book",
    "The cover reads 'The Legend of the Giant'. A ribbon marks a well-thumbed page.",
    usable=True,
    room_text="One volume — 'The Legend of the Giant' — lies open on the reading stand.",
)
key = Item(
    "key",
    "A heavy iron key. A clenched fist is stamped into its bow.",
    usable=True,
    room_text="A heavy key hangs on a chain around the giant's neck.",
)
silver_shield = Item(
    "silver shield",
    "A battered shield that still gleams faintly.",
    usable=True,
    room_text="A single human-sized silver shield hangs on a hook.",
)
torch = Item("torch", "A lit torch that pushes back the darkness.", usable=True)
crystal_vial = Item(
    "crystal vial",
    "A vial of luminous liquid — the Giant's own draft, distilled.",
    usable=True,
    room_text="A crystal vial filled with luminous liquid rests on the desk.",
)
iron_helm = Item(
    "iron helm",
    "A heavy helm that makes you feel invincible.",
    usable=True,
    room_text="An iron helm rests in the corner, forgotten by some earlier prisoner.",
)

# ── Rooms (15 total) ────────────────────────────────────────────────

starting_room = Room(
    name="Starting Room",
    description=(
        "A small chamber carved from grey stone, with a shelf set into one wall. The air "
        "smells of damp earth and old iron. A wooden door to the north stands ajar."
    ),
    items=[rusty_sword, candle],
    exits={"north": "Corridor"},
    scenery=(
        Item(
            "stone wall",
            "Most stones sit flush. One below the shelf has a crescent of dust around its edge.",
            aliases=("walls", "stone walls", "stones", "loose stone", "loose stones", "stonework"),
        ),
        Item("shelf", "A shallow stone ledge juts out of the wall. Old wax fills a crack along its lip."),
        Item("wooden door", "The door hangs ajar. Beyond it you can see the corridor to the north."),
    ),
    passage=HiddenPassage(
        tool="rusty sword",
        landmark="stone wall",
        direction="down",
        destination="Dungeon",
        reveal="One stone answers the sword with a hollow knock. It tips inward, revealing steps leading down.",
        visible="An opening below the shelf reveals stone steps leading down.",
    ),
)

corridor = Room(
    name="Corridor",
    description=(
        "A long passage stretches before you, its vaulted ceiling lost in darkness. "
        "Faded tapestries line the walls, depicting scenes of giants feasting at an "
        "enormous table. Doors branch east, west, and north, while the south leads "
        "back to where you started."
    ),
    exits={
        "south": "Starting Room",
        "east": "Treasure Room",
        "west": "Library",
        "north": "Great Staircase",
    },
    scenery=(
        Item("tapestries", "Woven giants bend over a table. There is a tiny empty chair at its head.", aliases=("tapestry",)),
    ),
)

treasure_room = Room(
    name="Treasure Room",
    description=(
        "Gold coins and faceted jewels spill across the floor in careless heaps. "
        "A marble pedestal rises from the centre of the glittering mess, and atop it "
        "the dust bears a small round impression. The air hums with latent power."
    ),
    items=[magic_potion],
    exits={"west": "Corridor"},
    scenery=(Item("marble pedestal", "The top is polished smooth around a ring of blue glass dust."),),
)

library = Room(
    name="Library",
    description=(
        "Shelves of ancient books climb to a ceiling you cannot see, their spines "
        "cracked and faded. Dust motes drift in a shaft of pale light from an unseen "
        "window onto a reading stand."
    ),
    items=[ancient_book],
    exits={"east": "Corridor", "north": "Archives"},
    scenery=(Item("reading stand", "A brass lip keeps the pages of an open book from slipping."),),
)

archives = Room(
    name="Archives",
    description=(
        "Rows of iron filing cabinets stand like sentinels in this cold, echoing room. "
        "Scrolls and maps spill from half-open drawers. A faded map on the wall marks a "
        "passage behind the Library shelves — if one knew where to look."
    ),
    exits={"south": "Library", "east": "Servants Passage"},
    scenery=(Item("faded map", "A pencilled line runs west from the Library shelves.", aliases=("wall map",)),),
)

servants_passage = Room(
    name="Servants Passage",
    description=(
        "A narrow, low-ceilinged tunnel that the castle servants once used to move unseen. "
        "The stones are worn smooth by centuries of hurried footsteps. It connects the "
        "archives to the great staircase without passing through the main halls."
    ),
    exits={"west": "Archives", "east": "Great Staircase"},
    scenery=(Item("stones", "The middle of each flagstone is worn lower than its edges.", aliases=("flagstones",)),),
)

great_staircase = Room(
    name="Great Staircase",
    description=(
        "A sweeping staircase of white marble spirals upward into shadow. Each step is "
        "wide enough for a giant's boot. Carvings of vines and serpents wind along the "
        "banisters, their stone eyes seeming to follow you as you climb."
    ),
    exits={
        "south": "Corridor",
        "west": "Servants Passage",
        "up": "Upper Landing",
    },
    scenery=(Item("banisters", "Carved stone serpents coil around vines.", aliases=("banister", "carvings")),),
)

upper_landing = Room(
    name="Upper Landing",
    description=(
        "The staircase opens onto a broad landing overlooking the hall below. Three "
        "corridors radiate outward like the spokes of a wheel — north to the Giant's "
        "Hall, east to the Armoury, and west to the Observatory."
    ),
    exits={
        "down": "Great Staircase",
        "north": "Giant's Hall",
        "east": "Armoury",
        "west": "Observatory",
    },
    scenery=(Item("landing", "From here you can look down into the curl of the staircase."),),
)

armoury = Room(
    name="Armoury",
    description=(
        "Racks of oversized weapons line the walls — halberds, maces, and swords built "
        "for hands three times your size. Cobwebs bridge the weapon racks."
    ),
    items=[silver_shield],
    exits={"west": "Upper Landing"},
    scenery=(
        Item("weapon racks", "The hooks are set so high that most of the weapons are beyond your reach.", aliases=("racks",)),
    ),
)

observatory = Room(
    name="Observatory",
    description=(
        "A domed room open to the night sky through a cracked glass ceiling. A massive "
        "brass telescope points at the stars, its lens clouded with age. Star charts "
        "cover every surface, and the wind whispers through the broken panes."
    ),
    items=[torch],
    exits={"east": "Upper Landing", "north": "Hidden Study"},
    scenery=(Item("brass telescope", "A single bright star swims in a blur behind the cloudy lens."),),
)

hidden_study = Room(
    name="Hidden Study",
    description=("Behind a revolving bookcase you find a small, secret study. A desk stands beneath a low arch."),
    items=[crystal_vial],
    exits={"south": "Observatory"},
    scenery=(Item("desk", "A pale stain in the shape of a vial marks the writing surface."),),
)

giants_hall = Room(
    name="Giant's Hall",
    description=(
        "A cavernous hall stretches before you, its ceiling lost in darkness high above. "
        "An enormous throne of twisted iron dominates the far end. The giant sits upon it, "
        "chest rising and falling in uneasy sleep. "
        "The floor trembles faintly with each of his breaths."
    ),
    items=[key],
    exits={"south": "Upper Landing", "east": "Throne Antechamber"},
    scenery=(
        Item("giant", "He sleeps fitfully on the twisted iron throne."),
        Item("iron throne", "Its feet have sunk into the floor."),
    ),
)

throne_antechamber = Room(
    name="Throne Antechamber",
    description=(
        "A side chamber adjoining the Giant's Hall. Tattered banners hang from the rafters "
        "bearing the giant's crest — a fist clutching a goblet. A heavy iron door to the "
        "north has a keyhole that glints in the torchlight."
    ),
    exits={"west": "Giant's Hall", "north": "Victory Chamber"},
    scenery=(Item("iron door", "The lock bears the same clenched fist as the banners.", aliases=("lock", "keyhole")),),
)

victory_chamber = Room(
    name="Victory Chamber",
    description=(
        "Beyond the iron door lies a small, luminous chamber. Sunlight streams through a "
        "high window, illuminating a golden goblet on a stone altar — the Giant's Drink "
        "itself. Whoever claims it commands the castle."
    ),
    exits={"south": "Throne Antechamber"},
    scenery=(Item("stone altar", "The altar is carved with a pair of open hands."),),
)

dungeon = Room(
    name="Dungeon",
    description=(
        "Damp stone walls press close in this underground cell. A row of iron rings runs along one wall. "
        "The only daylight comes from a grate far above."
    ),
    items=[
        iron_helm,
        Item("iron chain", "Its last link is polished bright. One end is open like a hook.",
             aliases=("chains", "iron chains", "chain links"), room_text="An iron chain hangs in loose loops from one ring."),
    ],
    exits={"up": "Starting Room"},
    scenery=(
        Item("iron rings", "Three rings sit side by side at shoulder height. The middle one is worn smooth.",
             aliases=("ring", "iron ring", "wall rings"),
             discovery=Discovery(("rusty sword", "iron helm", "iron chain"),
                                 "The rings answer in three different notes. Flakes of rust fall away from the middle ring.",
                                 "A tiny bell is stamped into the middle iron ring.")),
        Item("stone walls", "Moisture beads on the stones. One patch below the rings is strangely dry.",
             aliases=("wall", "stone wall", "cell walls", "damp walls", "masonry", "stones"),
             discovery=Discovery(("rusty sword", "iron helm", "iron chain"),
                                 "Pale grit falls from the dry patch. Underneath it, someone scratched a row of marks.",
                                 "Seven tally marks cross the dungeon wall; the last curves into the shape of a bell.")),
        Item("overhead grate", "A thin square of daylight falls through iron bars high above your head.",
             aliases=("iron grate", "grating", "bars")),
    ),
)

# ── World Map ────────────────────────────────────────────────────────

world_map = {
    "Starting Room": starting_room,
    "Corridor": corridor,
    "Treasure Room": treasure_room,
    "Library": library,
    "Archives": archives,
    "Servants Passage": servants_passage,
    "Great Staircase": great_staircase,
    "Upper Landing": upper_landing,
    "Armoury": armoury,
    "Observatory": observatory,
    "Hidden Study": hidden_study,
    "Giant's Hall": giants_hall,
    "Throne Antechamber": throne_antechamber,
    "Victory Chamber": victory_chamber,
    "Dungeon": dungeon,
}
