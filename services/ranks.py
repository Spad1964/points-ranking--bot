from config.roles import ACTOR_RANKS, QUALIFICATIONS, ActorRank

RANKS_BY_NAME: dict[str, ActorRank] = {rank["rank"]: rank for rank in ACTOR_RANKS}

RANKS_ASC: list[ActorRank] = sorted(ACTOR_RANKS, key=lambda r: r["points_required"])
RANKS_DESC: list[ActorRank] = sorted(ACTOR_RANKS, key=lambda r: r["points_required"], reverse=True)

QUALIFICATION_ROLE_IDS = {qualification["name"]: qualification["role_id"] for qualification in QUALIFICATIONS}
RANK_QUALIFICATION_ROLE_IDS: dict[str, frozenset[int]] = {
    rank["rank"]: frozenset(
        QUALIFICATION_ROLE_IDS[qualification]
        for qualification in rank.get("qualifications", set())
    )
    for rank in ACTOR_RANKS
}


def get_rank_by_points(points: int) -> ActorRank:
    """Returns the highest rank the actor qualifies for by points. Always returns at least F."""
    for rank in RANKS_DESC:
        if points >= rank["points_required"]:
            return rank
    return RANKS_DESC[-1]


def has_rank_qualifications(rank: ActorRank, actor_role_ids: set[int] | frozenset[int]) -> bool:
    return RANK_QUALIFICATION_ROLE_IDS[rank["rank"]] <= actor_role_ids


def get_rank_by_points_and_qualifications(
    points: int,
    actor_role_ids: set[int] | frozenset[int],
) -> ActorRank:
    """Returns the highest rank allowed by points and qualification roles."""
    for rank in RANKS_DESC:
        if points >= rank["points_required"] and has_rank_qualifications(rank, actor_role_ids):
            return rank
    return RANKS_DESC[-1]


def get_next_rank(points: int) -> ActorRank | None:
    """Returns the next rank above the actor's current points, or None at max rank."""
    for rank in RANKS_ASC:
        if points < rank["points_required"]:
            return rank
    return None


def get_rank_by_name(rank_name: str) -> ActorRank | None:
    """Looks up a rank by its letter (F-S). Returns None for unknown names."""
    return RANKS_BY_NAME.get(rank_name)


def should_request_promotion(
    current_rank_name: str,
    points: int,
    actor_role_ids: set[int] | frozenset[int],
) -> ActorRank | None:
    """Returns the target rank if points and qualifications exceed current rank."""
    current_rank = get_rank_by_name(current_rank_name)
    qualified_rank = get_rank_by_points_and_qualifications(points, actor_role_ids)

    if current_rank is None:
        return None

    if qualified_rank["points_required"] > current_rank["points_required"]:
        return qualified_rank

    return None
