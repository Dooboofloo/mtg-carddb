
"""Source-verified printed type exceptions from the 2026-11-06 audit.

The normal Scryfall catalogs remain the primary type vocabulary.
These additions cover historical, supplemental, and novelty printings.
New unknown type strings must still be reviewed explicitly.
"""

SUPPLEMENTAL_SUPERTYPES = frozenset({"Host"})

SUPPLEMENTAL_CARD_TYPES = frozenset({
    "Elemental",
    "Universewalker",
    "Phenome-nom",
})

SUPPLEMENTAL_SUBTYPES = frozenset(
    line.strip()
    for line in """
Advisor
Alicorn
Angel
Ape
Armored
Art
Artist
Assassin
Athlete
Aura
Autobot
Barbarian
Barnyard
Bat
Bear
Beast
Beaver
Bird
Boxer
Bureaucrat
Byode
Camel
Cat
Cephalid
Chameleon
Champion
Chef
Clamfolk
Clown
Construct
Control Point
Cow
Crocodile
Deer
Demon
Designer
Dinosaur
Dog
Donkey
Dragon
Druid
Elder
Elemental
Elemental?
Elephant
Elf
Elves
Fire
Fish
Gamer
Goblin
God
Grandchild
Gus
Hatificer
Hawk
Heartwood
Horror
Horse
Human
Human?
Igpay
Insect
Jaguar
Jellyfish
Judge
Kangaroo
Key
Killbot
Knight
Knights
Koala
Lady of Proper Etiquette
Lander
Legend
Licid
Lizard
Mammoth
Mime
Mummy
Mutagen
Octopus
Omen
Omenpath
Orc
Ox
Pangolin
Paratrooper
Penguin
Performer
Pony
Power-Plant
Quest
Rat
Realm
Rebel
Rigger
Robot
Rogue
Sable
Scorpion
Secret Mission
Shaman
Ship
Snake
Sorcerer
Specter
Spuzzem
The Biggest, Baddest, Nastiest,
Townsfolk
Tree
Turtle
Turtle and/or Elephant
Ulamog's Power-Plant
Undercity
Vampyre
Vengeance
Vibranium
Villain
Waiter
Warrior
Wizard
Wolf
Wombat
Wrestler
You
Zombie
Zonian
""".splitlines()
    if line.strip()
)


def parse_line_alias(type_line: str) -> str:
    """Return an indexing interpretation of known printed conventions.

    This never replaces the original CardFace.type_line.
    """

    if type_line == "Poly Artifact":
        return "Artifact"

    if type_line == "Scariest Creature You'll Ever See":
        return "Creature"

    if type_line == "Eaturecray — Igpay":
        return "Creature — Igpay"

    if type_line.casefold() == "instant":
        return "Instant"

    if type_line.startswith("Summon — "):
        return "Creature — " + type_line[len("Summon — "):]

    if type_line.startswith("Summon "):
        return "Creature — " + type_line[len("Summon "):]

    left, divider, right = type_line.partition(" — ")

    if divider and left.casefold() == "plane":
        return "Plane — " + right

    return type_line
