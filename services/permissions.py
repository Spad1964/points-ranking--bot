import discord

from config.roles import LORE_TEAM_ROLES

LORE_TEAM_ROLE_NAMES = frozenset(LORE_TEAM_ROLES)

PROMOTION_APPROVER_ROLE_NAMES = frozenset(
    {
        "Deputy Actor Manager",
        "Senior Lore Team",
        "Actor Manager",
        "Deputy Head of Lore",
        "Executive Department",
        "Head of Lore",
    }
)

HIGH_RANK_APPROVER_ROLE_NAMES = frozenset(
    {
        "Actor Manager",
        "Executive Department",
        "Deputy Head of Lore",
        "Head of Lore",
    }
)


def get_member_role_names(member: discord.Member | discord.User) -> set[str]:
    if not isinstance(member, discord.Member):
        return set()

    return {role.name for role in member.roles}


def has_lore_team_role(member: discord.Member | discord.User) -> bool:
    return bool(get_member_role_names(member) & LORE_TEAM_ROLE_NAMES)


def is_senior(member: discord.Member | discord.User) -> bool:
    return bool(get_member_role_names(member) & PROMOTION_APPROVER_ROLE_NAMES)


def is_high_rank(member: discord.Member | discord.User) -> bool:
    return bool(get_member_role_names(member) & HIGH_RANK_APPROVER_ROLE_NAMES)


def can_approve_promotion(
    member: discord.Member | discord.User,
    requested_rank: str,
) -> bool:
    """A and S promotions require High Rank. All others only need Senior."""
    role_names = get_member_role_names(member)

    if requested_rank in {"A", "S"}:
        return bool(role_names & HIGH_RANK_APPROVER_ROLE_NAMES)

    return bool(role_names & PROMOTION_APPROVER_ROLE_NAMES)