"""Sample arc plans and the commands that traverse them."""


def arc_plan(prefix="", horizon="Beyond the pool, a stair of cloud climbs toward a dark lighthouse."):
    """A small, valid arc: three rooms, a bell placed before a glass heron that bars the way north."""
    return {
        "title": f"{prefix}The Glass Orchard",
        "premise": "Glass trees chime in a wind you cannot feel. Something at the orchard's heart will not let you pass.",
        "start": f"{prefix}Orchard Gate",
        "rooms": [
            {"name": f"{prefix}Orchard Gate", "description": "Glass trees ring a cold clearing. A path winds north.",
             "exits": {"north": f"{prefix}Chiming Grove"}, "items": [],
             "scenery": [{"name": "glass trees", "description": "Their leaves chime faintly."}]},
            {"name": f"{prefix}Chiming Grove", "description": "Glass trees crowd close and chime. A path leads east.",
             "exits": {"east": f"{prefix}Still Pool"},
             "items": [{"name": f"{prefix}silver bell".strip(), "description": "A small bell with a clear tone.",
                        "room_text": "A silver bell hangs from a low glass branch."}],
             "scenery": []},
            {"name": f"{prefix}Still Pool", "description": "A pool lies perfectly still beneath a hush of glass leaves.",
             "exits": {}, "items": [],
             "scenery": [{"name": "glass heron", "description": "A glass heron stands with its beak across the path."}]},
        ],
        "challenge": {
            "room": f"{prefix}Still Pool", "obstacle": "glass heron", "blocks": "north",
            "barrier": "A glass heron stands across the northern path and will not move.",
            "solutions": [{"tool": f"{prefix}silver bell".strip(), "approach": "ring a matching note",
                           "outcome": "The heron answers the bell's note and steps aside."}],
            "room_after": "A pool lies still beneath glass leaves. The northern path is clear.",
            "obstacle_after": "The glass heron stands aside, beak raised.",
        },
        "horizon": horizon,
    }


def expanded_arc_plan(prefix="", **kwargs):
    plan = arc_plan(prefix, **kwargs)
    hub, grove, pool = plan["rooms"]
    bell = grove["items"].pop()
    hub["exits"].update(east=prefix + "Reed Bank", west=prefix + "Weather Porch")
    grove["exits"].clear()
    grove["description"] = "A glass shutter catches the sound from the trees."
    grove["scenery"].append({"name": "glass shutter", "description": "A shallow wedge-shaped notch interrupts the frame."})
    plan["rooms"].extend([
        {"name": prefix + "Reed Bank", "description": "Reeds tap a thin rhythm along the bank.",
         "exits": {"north": prefix + "Map Room"},
         "items": [{"name": prefix + "reed wedge", "description": "A wedge rubbed smooth by a glass frame."}]},
        {"name": prefix + "Weather Porch", "description": "A weather dial trembles in an open stone arch.",
         "exits": {}, "scenery": [{"name": "weather dial", "description": "The spindle is missing from its empty hub."}]},
        {"name": prefix + "Map Room", "description": "A map shows the orchard as a collection of musical instruments.",
         "exits": {}, "scenery": [{"name": "map", "description": "The instruments face opposite sides of the orchard."}]},
        {"name": prefix + "Bell Foundry", "description": "A bench stands where metal once ran molten.",
         "exits": {}, "scenery": [{"name": "bench", "description": "Old circular tools have left marks on the wood."}],
         "items": [{"name": prefix + "brass spindle", "description": "A brass shaft etched with the points of a compass."}]},
        {"name": prefix + "Echo Vault", "description": "The air rings with the echo of a single note.",
         "exits": {"east": pool["name"]}, "items": [bell]},
    ])
    for room in plan["rooms"]:
        room.setdefault("items", [])
        room.setdefault("scenery", [])
    plan["passages"] = [
        {"room": grove["name"], "landmark": "glass shutter", "tool": prefix + "reed wedge", "direction": "east",
         "destination": prefix + "Bell Foundry", "reveal": "The shutter slides aside.",
         "visible": "An open shutter leads east."},
        {"room": prefix + "Weather Porch", "landmark": "weather dial", "tool": prefix + "brass spindle",
         "direction": "north", "destination": prefix + "Echo Vault", "reveal": "The dial swings back into the arch.",
         "visible": "The arch opens north."},
    ]
    return plan


# From the Giant's Hall through both passages to the final obstacle.
EXPLORE_ORCHARD = (
    "go north", "go east", "take reed wedge", "go west", "go north",
    "use reed wedge on glass shutter", "go east", "take brass spindle", "go west", "go south", "go west",
    "use brass spindle on weather dial", "go north", "take silver bell", "go east",
)


TO_THE_GIANT = ["take sword", "go north", "go north", "follow the banister", "go north", "upend the tray", "enter reflection"]
