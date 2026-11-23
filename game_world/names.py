"""Resolve player references to visible or carried objects."""

import re

BAG_NOUNS = ("bag", "backpack", "pouch", "sack")
BAG_NOUN_PATTERN = "(?:" + "|".join(BAG_NOUNS) + ")"
TAPESTRY_BAG_NAMES = tuple(prefix + name for prefix in ("", "makeshift ", "tapestry ") for name in BAG_NOUNS)

# Resolve these by canonical name so items in older saves gain the same vocabulary.
ITEM_ALIASES = {"tapestry bag": TAPESTRY_BAG_NAMES, "iron helm": ("helmet", "iron helmet")}


def _names(obj):
    names = (obj.name, *obj.aliases, *ITEM_ALIASES.get(obj.name, ()))
    if obj.contents is not None:
        names += ("container",)
        if obj.name.split()[-1] in (*BAG_NOUNS, "satchel"):
            names += ("bag",)
    return names


def noun(text):
    """Normalize an object reference while retaining meaningful modifiers."""
    words = text.casefold().strip(" .!?").split()
    if words and words[0] in ("a", "an", "the", "my", "your", "teh"):
        words = words[1:]
    return " ".join(words)


def _heads(name):
    """Shorter ways to name a thing: "rusty sword" -> "sword"; "chip of marble" -> "chip", never "marble"."""
    words = name.split(" of ")[0].split()
    return {" ".join(words[start:]) for start in range(len(words))}


def matching_objects(reference, objects):
    """List the closest matches, honoring an optional persistent item number."""
    name = noun(reference)
    qualified = re.fullmatch(r"(.+) #(\d+)", name)
    if qualified:
        name, number = qualified.groups()
        objects = [obj for obj in objects if str(obj.number) == number]
    if not name:
        return []
    objects = list(objects)
    named = [obj for obj in objects if obj.name == name]
    aliases = [obj for obj in objects if name in _names(obj)]
    return named or aliases or [obj for obj in objects if any(name in _heads(n) for n in _names(obj))]


def find_object(reference, objects):
    """Resolve a name, an alias, or an unambiguous shortening that keeps the thing's main noun.

    A modifier alone never matches: "glass" does not mean the "glass bell", and "marble" does not mean
    the "chip of marble". Unmatched references go to the adjudicator, which reads them in context.
    """
    matches = matching_objects(reference, objects)
    if len(matches) == 1 or (matches and noun(reference) in _names(matches[0])):
        return matches[0]
    return None


def find_among(reference, *groups):
    """Prefer an exact name or alias in any group, then the first group with one shortened match.

    Taking "marble" picks the room's "marble" over a carried "chip of marble", while a lone partial
    match in an earlier group still beats one in a later group.
    """
    name = noun(reference)
    everything = [obj for group in groups for obj in group]
    exact = [obj for obj in everything if name and name in _names(obj)]
    if exact:
        return find_object(reference, exact)
    for group in groups:
        found = find_object(reference, group)
        if found:
            return found
    return None
