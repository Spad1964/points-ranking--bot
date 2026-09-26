from typing import TypedDict


class ActorRank(TypedDict, total=False):
    rank: str           # single letter: F, E, D, C, B, A, S
    role_name: str      # Discord role name, e.g. "[B] Actor"
    points_required: int
    qualifications: set[str]


class Qualification(TypedDict):
    name: str
    role_id: int


LORE_TEAM_ROLES: list[str] = [
    "Trial Lore Team",
    "Lore Team",
    "Deputy Actor Manager",
    "Senior Lore Team",
    "Actor Manager",
    "Deputy Head of Lore",
    "Head of Lore",
]

# Ordered S -> F. The rank lookup functions in services/ranks.py rely on this ordering.
ACTOR_RANKS: list[ActorRank] = [
    {"rank": "S", "role_name": "[S] Actor", "points_required": 2000, "qualifications": {"Politics", "CIS Pilot"}},
    {"rank": "A", "role_name": "[A] Actor", "points_required": 1500, "qualifications": {"Melee"}},
    {"rank": "B", "role_name": "[B] Actor", "points_required": 1000, "qualifications": {"Pilot"}},
    {"rank": "C", "role_name": "[C] Actor", "points_required": 700,  "qualifications": {"Medical", "Marksman", "Inventive"}},
    {"rank": "D", "role_name": "[D] Actor", "points_required": 400},
    {"rank": "E", "role_name": "[E] Actor", "points_required": 200},
    {"rank": "F", "role_name": "[F] Actor", "points_required": 0}
]

QUALIFICATIONS: list[Qualification] = [
    {"name": "Medical", "role_id": 1543417142605516931},
    {"name": "Marksman", "role_id": 1543417400991547402},
    {"name": "Inventive", "role_id": 1543416265144533122},
    {"name": "Melee", "role_id": 1543416524134416464},
    {"name": "Pilot", "role_id": 1543416932957692005},
    {"name": "CIS Pilot", "role_id": 1552920140369109114},
    {"name": "Politics", "role_id": 1543417324168544309},
    {"name": "Sith", "role_id": 1552920106714275870},
    {"name": "OPSEC", "role_id": 1543417044970774658}
]

PROMOTION_REQUESTS_CHANNEL_NAME: str = "promotion-requests"
POINTS_DISTRIBUTION_CHANNEL_NAME: str = "point-distribution"
