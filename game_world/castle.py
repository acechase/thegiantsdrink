"""The authored castle, starting inventory items, and encounters."""

from copy import deepcopy
from typing import Optional

from trials import FallingStairs, GiantTable

from .models import Discovery, HiddenPassage, Item, Room

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
    trial=FallingStairs(),
    scenery=(Item("banisters", "The stone serpents are worn smooth where tiny hands have held them. "
                  "Small footprints overlap along the outer edge.", aliases=("banister", "carvings", "rail", "ledge")),),
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
        "You stand on a table at the foot of a twisted iron throne. The giant leans down until you can see "
        "your whole body reflected in one of his eyes."
    ),
    trial=GiantTable(),
    exits={"south": "Upper Landing", "east": "Throne Antechamber"},
    scenery=(
        Item("giant", "He leans closer whenever a glass tilts. His gaze lingers in the silver."),
        Item("left cup", "Clouds are etched into the pale glass. Two narrow marks run from its rim to the tray.",
             aliases=("left glass", "white cup", "white drink")),
        Item("right cup", "A thin black ring marks the silver where this glass once fell.",
             aliases=("right glass", "black cup", "black drink")),
        Item("silver tray", "The giant blinks. The eye in the metal stays open a moment longer.",
             aliases=("tray", "reflection", "reflected eye")),
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

world_map = {room.name: room for room in (
    starting_room, corridor, treasure_room, library, archives, servants_passage, great_staircase,
    upper_landing, armoury, observatory, hidden_study, giants_hall, throne_antechamber, victory_chamber, dungeon,
)}


def fresh_world(initial_world: Optional[dict[str, Room]] = None) -> dict[str, Room]:
    """Copy a complete room graph for play, preserving references within that world."""
    return deepcopy(world_map if initial_world is None else initial_world)
