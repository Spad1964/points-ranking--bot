import re
from typing import TypedDict

FIELD_PATTERNS = {
    "names": re.compile(r"^Names:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "length": re.compile(r"^Length:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "roleplay": re.compile(r"^Roleplay:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "combat": re.compile(r"^Combat:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
}

MENTION_PATTERN = re.compile(r"<@!?(\d+)>")


class ParsedPointsMessage(TypedDict):
    user_ids: list[int]
    length_points: int
    roleplay_points: int
    combat_points: int


def parse_score(value: str) -> int:
    """Accepts an integer or N/A / NA / None / -. Raises ValueError for anything else or negatives."""
    clean_value = value.strip().upper()

    if clean_value in {"N/A", "NA", "NONE", "-"}:
        return 0

    try:
        score = int(clean_value)
    except ValueError as exc:
        raise ValueError(f"Invalid score: {value}") from exc

    if score < 0:
        raise ValueError("Scores cannot be negative.")

    return score


def parse_points_message(content: str) -> ParsedPointsMessage:
    """Parses a points message into user IDs and scores. Raises ValueError if any field is missing or malformed."""
    matches = {
        field_name: pattern.search(content)
        for field_name, pattern in FIELD_PATTERNS.items()
    }

    if any(match is None for match in matches.values()):
        raise ValueError(
            "Message format is incorrect. Use Names, Length, Roleplay and Combat."
        )

    names_match = matches["names"]
    length_match = matches["length"]
    roleplay_match = matches["roleplay"]
    combat_match = matches["combat"]

    user_ids = [
        int(user_id)
        for user_id in MENTION_PATTERN.findall(names_match.group(1))
    ]

    if not user_ids:
        raise ValueError("No valid user mentions found in the Names field.")

    return {
        "user_ids": user_ids,
        "length_points": parse_score(length_match.group(1)),
        "roleplay_points": parse_score(roleplay_match.group(1)),
        "combat_points": parse_score(combat_match.group(1)),
    }