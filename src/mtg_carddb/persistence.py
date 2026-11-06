"""Write already-selected Oracle records into a writable CardDB v1 staging DB.

No printing records, tag facts, or set metadata are created by this module.
Each representative is inserted atomically; callers own the outer transaction.
"""

from __future__ import annotations

import sqlite3

from .models import Card
from .representatives import SelectedRepresentative

_COLOR_BITS = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16, "C": 32}


def _mask(symbols: frozenset[str] | None) -> int | None:
    if symbols is None:
        return None
    return sum(_COLOR_BITS[symbol] for symbol in symbols)


def write_representative(
    connection: sqlite3.Connection,
    selected: SelectedRepresentative,
) -> None:
    """Persist one selected Oracle card, faces, classifications and legalities.

    Preserves None versus empty colors and None versus False game_changer.
    Does not replace existing rows or commit the caller's outer transaction.
    """
    card: Card = selected.card
    connection.execute("SAVEPOINT carddb_write_card")
    try:
        connection.execute(
            "INSERT INTO cards (oracle_id, name, layout, type_line, mana_value, "
            "color_identity, colors, produced_mana, reserved, game_changer) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                card.oracle_id, card.name, card.layout, card.type_line,
                card.mana_value, _mask(card.color_identity), _mask(card.colors),
                _mask(card.produced_mana), int(card.reserved),
                None if card.game_changer is None else int(card.game_changer),
            ),
        )
        for face in card.faces:
            connection.execute(
                "INSERT INTO faces (oracle_id, face_index, name, type_line, "
                "mana_cost, oracle_text, colors, color_indicator, power, "
                "toughness, loyalty, defense, hand_modifier, life_modifier) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    card.oracle_id, face.face_index, face.name, face.type_line,
                    face.mana_cost, face.oracle_text, _mask(face.colors),
                    _mask(face.color_indicator), face.power, face.toughness,
                    face.loyalty, face.defense, face.hand_modifier,
                    face.life_modifier,
                ),
            )
            for category, values in (
                ("supertype", face.supertypes),
                ("type", face.types),
                ("subtype", face.subtypes),
            ):
                connection.executemany(
                    "INSERT INTO face_types "
                    "(oracle_id, face_index, category, value) VALUES (?, ?, ?, ?)",
                    (
                        (card.oracle_id, face.face_index, category, value)
                        for value in sorted(values)
                    ),
                )
        connection.executemany(
            "INSERT INTO card_keywords (oracle_id, keyword) VALUES (?, ?)",
            ((card.oracle_id, keyword) for keyword in sorted(card.keywords)),
        )
        connection.executemany(
            "INSERT INTO card_legalities (oracle_id, format, status) "
            "VALUES (?, ?, ?)",
            (
                (card.oracle_id, legality.format, legality.status)
                for legality in selected.legalities
            ),
        )
    except BaseException:
        connection.execute("ROLLBACK TO SAVEPOINT carddb_write_card")
        connection.execute("RELEASE SAVEPOINT carddb_write_card")
        raise
    else:
        connection.execute("RELEASE SAVEPOINT carddb_write_card")
