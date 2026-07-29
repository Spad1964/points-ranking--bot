from typing import TypedDict


class ActorRank(TypedDict):
    rank: str           # single letter: F, E, D, C, B, A, S
    role_name: str      # Discord role name, e.g. "[B] Actor"
    points_required: int


LORE_TEAM_ROLES: list[str] = [
    "Trial Lore Team",
    "Lore Team",
    "Deputy Actor Manager",
    "Senior Lore Team",
    "Actor Manager",
    "Deputy Head of Lore",
    "Head of Lore",
]

# Ordered S → F. The rank lookup functions in services/ranks.py rely on this ordering.
ACTOR_RANKS: list[ActorRank] = [
    {"rank": "S", "role_name": "[S] Actor", "points_required": 2000},
    {"rank": "A", "role_name": "[A] Actor", "points_required": 1500},
    {"rank": "B", "role_name": "[B] Actor", "points_required": 1000},
    {"rank": "C", "role_name": "[C] Actor", "points_required": 700},
    {"rank": "D", "role_name": "[D] Actor", "points_required": 400},
    {"rank": "E", "role_name": "[E] Actor", "points_required": 200},
    {"rank": "F", "role_name": "[F] Actor", "points_required": 0},
]

PROMOTION_REQUESTS_CHANNEL_NAME: str = "promotion-requests"
POINTS_DISTRIBUTION_CHANNEL_NAME: str = "point-distribution"
