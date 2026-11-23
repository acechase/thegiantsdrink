"""Resolve player references to visible or carried objects."""


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


def find_object(reference, objects):
    """Resolve a name, an alias, or an unambiguous shortening that keeps the thing's main noun.

    A modifier alone never matches: "glass" does not mean the "glass bell", and "marble" does not mean
    the "chip of marble". Unmatched references go to the adjudicator, which reads them in context.
    """
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
        if any(name in _heads(candidate) for candidate in names):
            partial.append(obj)
    return partial[0] if len(partial) == 1 else None


def find_among(reference, *groups):
    """Prefer an exact name or alias in any group, then the first group with one shortened match.

    Taking "marble" picks the room's "marble" over a carried "chip of marble", while a lone partial
    match in an earlier group still beats one in a later group.
    """
    name = noun(reference)
    everything = [obj for group in groups for obj in group]
    exact = [obj for obj in everything if name and (name == obj.name or name in obj.aliases)]
    if exact:
        return find_object(reference, exact)
    for group in groups:
        found = find_object(reference, group)
        if found:
            return found
    return None
