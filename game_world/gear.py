"""Item capabilities, inventory references, and movement of whole item trees."""

from collections import Counter
from collections.abc import MutableMapping

from .names import matching_objects

STORAGE_FORMS = {"bag", "backpack", "basket", "box", "case", "chest", "pouch", "sack", "satchel", "sheath", "quiver"}
ATTACHMENT_FORMS = {"backpack", "belt", "harness", "keyring", "lanyard", "rack", "bandolier"}
STORAGE_FEATURES = {"pocket", "compartment"}
ATTACHMENT_FEATURES = {"loop", "loops", "hook", "hooks", "clip", "clips"}


def default_capabilities(name):
    """Give recognizable finished gear its ordinary uses; crafting can specify other forms."""
    head = name.rsplit(" ", 1)[-1]
    return head in STORAGE_FORMS, head in ATTACHMENT_FORMS or name == "tapestry bag"


def walk_items(roots):
    """Visit each actual item once, including anything packed or fastened to it."""
    pending = list(reversed(roots))
    seen = set()
    while pending:
        item = pending.pop()
        if item in seen:
            continue
        seen.add(item)
        yield item
        pending.extend(reversed(item.attachments or ()))
        pending.extend(reversed(item.contents or ()))


class GearError(ValueError):
    """A player-readable reason an inventory action could not be applied."""


class WornEquipment(MutableMapping):
    """Expose the game's name-based outfit mapping while storing wear on individual items."""

    def __init__(self, inventory):
        """Refer to the live inventory."""
        self.inventory = inventory

    def __iter__(self):
        """Yield distinct references for equipped gear."""
        counts = Counter(item.name for item in self.inventory)
        return (item.reference if counts[item.name] > 1 else item.name for item in self.inventory if item.worn)

    def __len__(self):
        """Count equipped items."""
        return sum(bool(item.worn) for item in self.inventory)

    def __getitem__(self, ref):
        """Read the wear description for one item."""
        item = choose_item(ref, self.inventory)
        if not item or not item.worn:
            raise KeyError(ref)
        return item.worn

    def __setitem__(self, ref, how):
        """Equip an existing carried item."""
        item = choose_item(ref, self.inventory)
        if item is None:
            raise KeyError(ref)
        item.worn = how

    def __delitem__(self, ref):
        """Take off one equipped item."""
        if ref not in self:
            raise KeyError(ref)
        self[ref] = ""


def choose_item(reference, objects):
    """Resolve a name or qualified reference, asking the player to distinguish matches."""
    matches = matching_objects(reference, objects)
    if len(matches) > 1:
        choices = ", ".join(item.reference for item in matches)
        raise GearError(f"Which {reference} do you mean? Choose {choices}.")
    return matches[0] if matches else None


def transfer(inventory, nearby, action, reference, host_reference):
    """Validate both ends and move exactly one object, together with all its children."""
    hosts = list(walk_items([*inventory, *nearby]))
    host = choose_item(host_reference, hosts)
    if host is None:
        raise GearError(f"You can't reach a {host_reference} here.")
    if action == "retrieve":
        item = choose_item(reference, [*(host.contents or ()), *(host.attachments or ())])
        action = "unfasten" if item in (host.attachments or ()) or host.contents is None else "unpack"
    outside = action in ("attach", "unfasten")
    retrieving = action in ("unpack", "unfasten")
    children = host.attachments if outside else host.contents
    if children is None:
        if outside:
            raise GearError(f"The {host.name} has nowhere to fasten anything.")
        raise GearError(f"The {host.name} has no space to store anything inside.")
    source, destination = (children, inventory) if retrieving else (inventory, children)
    item = choose_item(reference, source)
    if item is None:
        if not retrieving:
            raise GearError(f"You're not carrying {reference} loose.")
        if outside:
            raise GearError(f"There is no {reference} attached to the {host.name}.")
        raise GearError(f"The {host.name} doesn't contain {reference}.")
    if not retrieving and host in walk_items([item]):
        raise GearError("An item cannot hold itself or one of its own parents.")
    source.remove(item)
    item.worn = ""
    destination.append(item)
    if action == "pack":
        return f"You put the {item.name} in the {host.name}."
    if action == "unpack":
        return f"You take the {item.name} out of the {host.name}."
    if action == "attach":
        return f"You attach the {item.name} to the {host.name}."
    return f"You detach the {item.name} from the {host.name}."


def item_labels(roots):
    """Qualify duplicate names within reach with their permanent item numbers."""
    items = list(walk_items(roots))
    counts = Counter(item.name for item in items)
    return {item: item.reference if counts[item.name] > 1 else item.name for item in items}


def inventory_tree(inventory, labels=None):
    """Project the two relationships into player-facing nodes with unambiguous command references."""
    if labels is None:
        labels = item_labels(inventory)

    def node(item):
        return {
            "id": item.number, "name": item.name, "label": labels[item], "worn": bool(item.worn),
            "contents": None if item.contents is None else [node(child) for child in item.contents],
            "attachments": None if item.attachments is None else [node(child) for child in item.attachments],
        }

    return [node(item) for item in inventory]


def describe_node(node):
    """Render one inventory entry, including its children's entries and their relationships."""
    text = node["label"] + (" (worn)" if node["worn"] else "")
    if node["contents"] is not None:
        text += " [" + (", ".join(map(describe_node, node["contents"])) or "empty") + "]"
    if node["attachments"]:
        text += " (attached: " + ", ".join(map(describe_node, node["attachments"])) + ")"
    return text


def visible_attachments(item):
    """Describe only gear visible on the outside, including attachments to attachments."""
    facts = []
    for child in item.attachments or ():
        facts.append(f"The {child.name} is attached to the {item.name}.")
        facts.extend(visible_attachments(child))
    return facts
