from config.roles import ACTOR_RANKS, ActorRank

RANKS_BY_NAME: dict[str, ActorRank] = {rank["rank"]: rank for rank in ACTOR_RANKS}

RANKS_ASC: list[ActorRank] = sorted(ACTOR_RANKS, key=lambda r: r["points_required"])
RANKS_DESC: list[ActorRank] = sorted(ACTOR_RANKS, key=lambda r: r["points_required"], reverse=True)


def get_rank_by_points(points: int) -> ActorRank:
    """Returns the highest rank the actor qualifies for. Always returns at least F."""
    for rank in RANKS_DESC:
        if points >= rank["points_required"]:
            return rank
    return RANKS_DESC[-1]


def get_next_rank(points: int) -> ActorRank | None:
    """Returns the next rank above the actor's current points, or None at max rank."""
    for rank in RANKS_ASC:
        if points < rank["points_required"]:
            return rank
    return None


def get_rank_by_name(rank_name: str) -> ActorRank | None:
    """Looks up a rank by its letter (F–S). Returns None for unknown names."""
    return RANKS_BY_NAME.get(rank_name)


def should_request_promotion(current_rank_name: str, points: int) -> ActorRank | None:
    """Returns the target rank if the actor's points exceed their current rank, otherwise None."""
    current_rank = get_rank_by_name(current_rank_name)
    points_rank = get_rank_by_points(points)

    if current_rank is None:
        return None

    if points_rank["points_required"] > current_rank["points_required"]:
        return points_rank

    return None
