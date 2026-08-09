import logging
import os
from io import StringIO

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

from config.roles import (
    ACTOR_RANKS,
    POINTS_DISTRIBUTION_CHANNEL_NAME,
    PROMOTION_REQUESTS_CHANNEL_NAME,
)
from database.db import (
    add_points_to_actors,
    adjust_points_and_get_actor,
    approve_promotion_request,
    close_db,
    create_promotion_request,
    deny_promotion_request,
    get_actor,
    get_actors_by_rank,
    get_existing_active_promotion_request,
    get_inactive_actors,
    get_latest_point_log,
    get_pending_promotion_requests,
    get_point_adjustments,
    get_point_logs,
    get_promotion_request_by_id,
    init_db,
    update_promotion_request_message_id,
    delete_actors_by_ids,
    get_all_actors,
    seed_actors,
)
from services.permissions import can_approve_promotion, has_lore_team_role, is_high_rank, is_senior
from services.points_parser import parse_points_message
from services.ranks import get_next_rank, get_rank_by_points, get_rank_by_name, should_request_promotion
from services.roblox import accept_join_request, get_group_members, get_group_roles, get_members_with_role, kick_group_member, lookup_users_by_usernames, remove_member_rank

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("lore_points")

TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN is None:
    raise ValueError("DISCORD_TOKEN environment variable not found")

POINTS_DISTRIBUTION_CHANNEL_ID = int(os.getenv("POINTS_DISTRIBUTION_CHANNEL_ID", "0"))
PROMOTION_REQUESTS_CHANNEL_ID = int(os.getenv("PROMOTION_REQUESTS_CHANNEL_ID", "0"))

VALID_RANKS = {"F", "E", "D", "C", "B", "A", "S"}


class LorePointsBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

        # on_ready fires on every reconnect, not just on first start.
        # This flag ensures we only sync commands and restore views once.
        self._startup_complete = False

    async def setup_hook(self) -> None:
        await init_db()

    async def close(self) -> None:
        await close_db()
        await super().close()


bot = LorePointsBot()


def get_channel_by_id_or_name(
    guild: discord.Guild,
    channel_id: int,
    channel_name: str,
) -> discord.TextChannel | None:
    """ID lookup is preferred. Falls back to name search when the env var isn't set (ID resolves to 0)."""
    if channel_id:
        channel = guild.get_channel(channel_id)

        if isinstance(channel, discord.TextChannel):
            return channel

    return discord.utils.get(guild.text_channels, name=channel_name)


async def restore_promotion_views() -> None:
    """Re-registers view objects after a restart so existing promotion buttons stay interactive."""
    pending_requests = await get_pending_promotion_requests()

    for promotion_request in pending_requests:
        bot.add_view(PromotionRequestView(request_id=promotion_request["id"]))

    log.info("Restored %d pending promotion request views", len(pending_requests))


@bot.event
async def on_ready() -> None:
    if bot._startup_complete:
        log.info("Bot reconnected as %s", bot.user)
        return

    bot._startup_complete = True

    await bot.tree.sync()
    await restore_promotion_views()

    log.info("Bot started as %s", bot.user)


@bot.command()
async def ping(ctx: commands.Context) -> None:
    await ctx.send("Pong!")


async def update_actor_rank_roles(
    actor: discord.Member,
    requested_role: discord.Role,
    approved_by: discord.Member | discord.User,
) -> None:
    """Removes all existing rank roles from the actor, then adds the new one."""
    actor_rank_role_names = {
        rank["role_name"]
        for rank in ACTOR_RANKS
    }

    old_roles = [
        role
        for role in actor.roles
        if role.name in actor_rank_role_names and role.id != requested_role.id
    ]

    if old_roles:
        await actor.remove_roles(
            *old_roles,
            reason=f"Promotion approved by {approved_by}",
        )

    await actor.add_roles(
        requested_role,
        reason=f"Actor promotion approved by {approved_by}",
    )


class PromotionRequestView(discord.ui.View):
    def __init__(self, request_id: int):
        super().__init__(timeout=None)

        self.request_id = int(request_id)

        approve_button = discord.ui.Button(
            label="Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"promotion_approve:{self.request_id}",
        )

        deny_button = discord.ui.Button(
            label="Deny",
            style=discord.ButtonStyle.danger,
            custom_id=f"promotion_deny:{self.request_id}",
        )

        approve_button.callback = self.approve_button
        deny_button.callback = self.deny_button

        self.add_item(approve_button)
        self.add_item(deny_button)

    async def approve_button(self, interaction: discord.Interaction) -> None:
        # Must be first — Discord requires a response within 3 seconds and the role
        # API calls below can exceed that. On component interactions, defer() sends type 6
        # (DEFERRED_UPDATE_MESSAGE) by default — no loading indicator, message edited later.
        await interaction.response.defer()

        promotion_request = await get_promotion_request_by_id(self.request_id)

        if promotion_request is None:
            await interaction.followup.send(
                "Promotion request not found.",
                ephemeral=True,
            )
            return

        if promotion_request["status"] != "pending":
            await interaction.followup.send(
                "This promotion request is no longer pending.",
                ephemeral=True,
            )
            return

        if not can_approve_promotion(
            interaction.user,
            promotion_request["requested_rank"],
        ):
            await interaction.followup.send(
                "You do not have permission to approve this promotion.",
                ephemeral=True,
            )
            return

        guild = interaction.guild

        if guild is None:
            await interaction.followup.send(
                "Guild not found.",
                ephemeral=True,
            )
            return

        actor = guild.get_member(promotion_request["discord_id"])

        if actor is None:
            await interaction.followup.send(
                "Actor not found in this server.",
                ephemeral=True,
            )
            return

        requested_role = discord.utils.get(
            guild.roles,
            name=promotion_request["requested_role_name"],
        )

        if requested_role is None:
            await interaction.followup.send(
                f"Role {promotion_request['requested_role_name']} not found.",
                ephemeral=True,
            )
            return

        await update_actor_rank_roles(
            actor=actor,
            requested_role=requested_role,
            approved_by=interaction.user,
        )

        await approve_promotion_request(
            request_id=self.request_id,
            approved_by=interaction.user.id,
        )

        log.info(
            "Promotion approved: actor=%s rank=%s approved_by=%s (request_id=%d)",
            actor.id,
            requested_role.name,
            interaction.user.id,
            self.request_id,
        )

        for child in self.children:
            child.disabled = True

        await interaction.edit_original_response(
            content=(
                f"✅ Promotion approved by {interaction.user.mention}\n"
                f"Actor: {actor.mention}\n"
                f"New Rank: {requested_role.mention}"
            ),
            view=self,
        )

    async def deny_button(self, interaction: discord.Interaction) -> None:
        # Same reason as approve_button — defer first to stay within Discord's 3-second window.
        await interaction.response.defer()

        promotion_request = await get_promotion_request_by_id(self.request_id)

        if promotion_request is None:
            await interaction.followup.send(
                "Promotion request not found.",
                ephemeral=True,
            )
            return

        if promotion_request["status"] != "pending":
            await interaction.followup.send(
                "This promotion request is no longer pending.",
                ephemeral=True,
            )
            return

        if not can_approve_promotion(
            interaction.user,
            promotion_request["requested_rank"],
        ):
            await interaction.followup.send(
                "You do not have permission to deny this promotion.",
                ephemeral=True,
            )
            return

        await deny_promotion_request(
            request_id=self.request_id,
            denied_by=interaction.user.id,
        )

        log.info(
            "Promotion denied: request_id=%d denied_by=%s",
            self.request_id,
            interaction.user.id,
        )

        for child in self.children:
            child.disabled = True

        await interaction.edit_original_response(
            content=f"❌ Promotion denied by {interaction.user.mention}",
            view=self,
        )


async def send_promotion_request(
    guild: discord.Guild,
    actor_id: int,
    requested_rank: dict,
    points: int,
) -> bool:
    """Posts a promotion request embed with buttons. Returns False if the channel is missing or a request already exists."""
    promotion_channel = get_channel_by_id_or_name(
        guild=guild,
        channel_id=PROMOTION_REQUESTS_CHANNEL_ID,
        channel_name=PROMOTION_REQUESTS_CHANNEL_NAME,
    )

    if promotion_channel is None:
        return False

    existing_request = await get_existing_active_promotion_request(
        discord_id=actor_id,
        requested_rank=requested_rank["rank"],
    )

    if existing_request is not None:
        return False

    request_id = await create_promotion_request(
        discord_id=actor_id,
        requested_rank=requested_rank["rank"],
        requested_role_name=requested_rank["role_name"],
        points=points,
    )

    embed = discord.Embed(
        title="Actor Promotion Request",
        description=(
            f"<@{actor_id}> is eligible for promotion.\n\n"
            f"Requested Rank: **{requested_rank['role_name']}**\n"
            f"Current Points: **{points}**\n"
            f"Request ID: `{request_id}`"
        ),
        color=discord.Color.gold(),
    )

    sent_message = await promotion_channel.send(
        embed=embed,
        view=PromotionRequestView(request_id=request_id),
    )

    await update_promotion_request_message_id(
        request_id=request_id,
        message_id=sent_message.id,
    )

    log.info(
        "Promotion request created: actor=%d rank=%s points=%d (request_id=%d)",
        actor_id,
        requested_rank["rank"],
        points,
        request_id,
    )

    return True


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    await bot.process_commands(message)

    if message.content.startswith(str(bot.command_prefix)):
        return

    if message.guild is None:
        return

    if POINTS_DISTRIBUTION_CHANNEL_ID:
        if message.channel.id != POINTS_DISTRIBUTION_CHANNEL_ID:
            return
    elif message.channel.name != POINTS_DISTRIBUTION_CHANNEL_NAME:
        return

    if not has_lore_team_role(message.author):
        log.warning(
            "Unauthorized points attempt: user=%s (%d) in channel=%s",
            message.author,
            message.author.id,
            message.channel.name,
        )
        await message.add_reaction("❌")
        await message.reply(
            "You don't have permission to award points.\n"
            "Only members of Lore Team can award points to actors."
        )
        return

    try:
        parsed_message = parse_points_message(message.content)
    except ValueError:
        log.warning(
            "Invalid points message format from user=%s (%d)",
            message.author,
            message.author.id,
        )
        await message.add_reaction("❌")
        await message.reply(
            "Message format invalid.\n"
            "Use:\n"
            "```txt\n"
            "Names: @Actor1 @Actor2\n"
            "Length: #\n"
            "Roleplay: #\n"
            "Combat: #\n"
            "```"
        )
        return

    actors = await add_points_to_actors(
        discord_ids=parsed_message["user_ids"],
        length_points=parsed_message["length_points"],
        roleplay_points=parsed_message["roleplay_points"],
        combat_points=parsed_message["combat_points"],
        awarded_by=message.author.id,
    )

    actors_by_id = {
        int(actor["discord_id"]): actor
        for actor in actors
    }

    users_eligible_for_promotion: list[str] = []

    for user_id in parsed_message["user_ids"]:
        actor_data = actors_by_id.get(int(user_id))

        if actor_data is None:
            continue

        requested_rank = should_request_promotion(
            current_rank_name=actor_data["current_rank"],
            points=actor_data["points"],
        )

        if requested_rank is None:
            continue

        created = await send_promotion_request(
            guild=message.guild,
            actor_id=user_id,
            requested_rank=requested_rank,
            points=actor_data["points"],
        )

        if created:
            users_eligible_for_promotion.append(
                f"<@{user_id}> -> {requested_rank['role_name']}"
            )

    actors_text = " ".join(
        f"<@{user_id}>"
        for user_id in parsed_message["user_ids"]
    )

    total_per_actor = (
        parsed_message["length_points"]
        + parsed_message["roleplay_points"]
        + parsed_message["combat_points"]
    )

    promotion_text = ""

    if users_eligible_for_promotion:
        promotion_text = (
            "\nPromotion request created for:\n"
            + "\n".join(f"- {request}" for request in users_eligible_for_promotion)
        )

    log.info(
        "Points awarded by %s (%d) to %s: L=%d R=%d C=%d total=%d",
        message.author,
        message.author.id,
        parsed_message["user_ids"],
        parsed_message["length_points"],
        parsed_message["roleplay_points"],
        parsed_message["combat_points"],
        total_per_actor,
    )

    await message.add_reaction("✅")
    await message.reply(
        f"Points added successfully.\n"
        f"Actors: {actors_text}\n"
        f"Total per actor: {total_per_actor}"
        f"{promotion_text}",
    )


@bot.tree.command(name="profile", description="Show an actor points profile")
async def profile(
    interaction: discord.Interaction,
    actor: discord.Member,
) -> None:
    actor_data = await get_actor(actor.id)

    if actor_data is None:
        await interaction.response.send_message(
            f"{actor.mention} does not have points yet.",
            ephemeral=True,
        )
        return

    points = actor_data["points"]
    current_rank = get_rank_by_points(points)
    next_rank = get_next_rank(points)
    latest_log = await get_latest_point_log(actor.id)

    if next_rank is None:
        next_rank_text = "Max rank reached"
        points_needed_text = "0"
        progress_text = f"{points} / Max"
    else:
        next_rank_text = next_rank["role_name"]
        points_needed = next_rank["points_required"] - points
        points_needed_text = str(points_needed)
        progress_text = f"{points} / {next_rank['points_required']}"

    embed = discord.Embed(
        title=f"Actor Profile — {actor.display_name}",
        color=discord.Color.purple(),
    )

    embed.add_field(name="Points", value=str(points), inline=True)
    embed.add_field(name="Current Rank", value=current_rank["role_name"], inline=True)
    embed.add_field(name="Next Rank", value=next_rank_text, inline=True)
    embed.add_field(name="Progress", value=progress_text, inline=True)
    embed.add_field(name="Points Needed", value=points_needed_text, inline=True)

    if latest_log is not None:
        embed.add_field(
            name="Last Points",
            value=(
                f"+{latest_log['total_points']} points\n"
                f"Length: {latest_log['length_points']}\n"
                f"Roleplay: {latest_log['roleplay_points']}\n"
                f"Combat: {latest_log['combat_points']}\n"
                f"Awarded by: <@{latest_log['awarded_by']}>"
            ),
            inline=False,
        )

    embed.set_thumbnail(url=actor.display_avatar.url)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="adjustpoints", description="Manually adjust actor points")
async def adjustpoints(
    interaction: discord.Interaction,
    actor: discord.Member,
    amount: int,
    reason: str,
) -> None:
    if not is_senior(interaction.user):
        await interaction.response.send_message(
            "You do not have permission to adjust actor points.\n"
            "This command is restricted to Deputy Actor Manager and above.",
            ephemeral=True,
        )
        return

    if amount == 0:
        await interaction.response.send_message(
            "Amount cannot be 0.",
            ephemeral=True,
        )
        return

    actor_data = await adjust_points_and_get_actor(
        discord_id=actor.id,
        amount=amount,
        reason=reason,
        adjusted_by=interaction.user.id,
    )

    if actor_data is None:
        await interaction.response.send_message(
            "Something went wrong while adjusting points.",
            ephemeral=True,
        )
        return

    requested_rank = should_request_promotion(
        current_rank_name=actor_data["current_rank"],
        points=actor_data["points"],
    )

    promotion_text = ""

    if requested_rank is not None and interaction.guild is not None:
        created = await send_promotion_request(
            guild=interaction.guild,
            actor_id=actor.id,
            requested_rank=requested_rank,
            points=actor_data["points"],
        )

        if created:
            promotion_text = (
                f"\n\nPromotion request created for "
                f"{actor.mention} → {requested_rank['role_name']}"
            )

    log.info(
        "Points adjusted: actor=%d amount=%+d new_total=%d reason=%r adjusted_by=%s (%d)",
        actor.id,
        amount,
        actor_data["points"],
        reason,
        interaction.user,
        interaction.user.id,
    )

    await interaction.response.send_message(
        f"Points adjusted successfully.\n"
        f"Actor: {actor.mention}\n"
        f"Adjustment: {amount:+d}\n"
        f"New Total: {actor_data['points']}\n"
        f"Reason: {reason}"
        f"{promotion_text}",
    )


@bot.tree.command(name="pendingpromotions", description="List all pending actor promotion requests")
async def pendingpromotions(interaction: discord.Interaction) -> None:
    if not is_senior(interaction.user):
        await interaction.response.send_message(
            "You do not have permission to use this command.\n"
            "This command is restricted to Deputy Actor Manager and above.",
            ephemeral=True,
        )
        return

    pending = await get_pending_promotion_requests()

    if not pending:
        await interaction.response.send_message(
            "There are no pending promotion requests.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    promotion_channel = None

    if guild is not None:
        promotion_channel = get_channel_by_id_or_name(
            guild=guild,
            channel_id=PROMOTION_REQUESTS_CHANNEL_ID,
            channel_name=PROMOTION_REQUESTS_CHANNEL_NAME,
        )

    lines = []

    for req in pending:
        line = (
            f"<@{req['discord_id']}> → **{req['requested_role_name']}** "
            f"({req['points']} pts) `#{req['id']}`"
        )

        if guild is not None and promotion_channel is not None and req["message_id"]:
            message_url = (
                f"https://discord.com/channels/{guild.id}"
                f"/{promotion_channel.id}/{req['message_id']}"
            )
            line += f" — [Jump]({message_url})"

        lines.append(line)

    embed = discord.Embed(
        title="Pending Promotion Requests",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )

    embed.set_footer(text=f"{len(pending)} request(s) pending")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="history", description="Show recent actor point history")
@discord.app_commands.describe(
    see_full_history="Export the actor's full point change history as a text file.",
)
async def history(
    interaction: discord.Interaction,
    actor: discord.Member,
    see_full_history: bool = False,
) -> None:
    actor_data = await get_actor(actor.id)

    if actor_data is None:
        await interaction.response.send_message(
            f"{actor.mention} does not have points yet.",
            ephemeral=True,
        )
        return

    point_logs = await get_point_logs(
        actor.id,
        limit=None if see_full_history else 5,
    )
    adjustments = await get_point_adjustments(
        actor.id,
        limit=None if see_full_history else 5,
    )

    if see_full_history:
        history_entries = []

        for log_entry in point_logs:
            history_entries.append(
                (
                    str(log_entry["created_at"]),
                    int(log_entry["id"]),
                    (
                        f"[{log_entry['created_at']}] "
                        f"PD +{log_entry['total_points']} points "
                        f"(Length {log_entry['length_points']}, "
                        f"Roleplay {log_entry['roleplay_points']}, "
                        f"Combat {log_entry['combat_points']}) "
                        f"by <@{log_entry['awarded_by']}>"
                    ),
                )
            )

        for adjustment in adjustments:
            history_entries.append(
                (
                    str(adjustment["created_at"]),
                    int(adjustment["id"]),
                    (
                        f"[{adjustment['created_at']}] "
                        f"Adjustment {adjustment['amount']:+d} points "
                        f"by <@{adjustment['adjusted_by']}> "
                        f"- {adjustment['reason']}"
                    ),
                )
            )

        history_entries.sort(
            key=lambda entry: (entry[0], entry[1]),
            reverse=True,
        )

        lines = [
            f"Full Point History - {actor.display_name}",
            f"Actor: {actor} ({actor.id})",
            f"Current Points: {actor_data['points']}",
            f"Total Entries: {len(history_entries)}",
            "",
        ]

        if history_entries:
            lines.extend(entry_text for _, _, entry_text in history_entries)
        else:
            lines.append("No point history found.")

        file_buffer = StringIO("\n".join(lines))
        file = discord.File(
            file_buffer,
            filename=f"{actor.id}_point_history.txt",
        )

        await interaction.response.send_message(file=file)
        return

    embed = discord.Embed(
        title=f"Recent History — {actor.display_name}",
        description=f"Current Points: **{actor_data['points']}**",
        color=discord.Color.blue(),
    )

    if point_logs:
        pd_history_text = ""

        for entry in point_logs:
            pd_history_text += (
                f"**+{entry['total_points']} points** "
                f"— Length {entry['length_points']}, "
                f"Roleplay {entry['roleplay_points']}, "
                f"Combat {entry['combat_points']}\n"
                f"By: <@{entry['awarded_by']}> • {entry['created_at']}\n\n"
            )

        embed.add_field(
            name="PD Points",
            value=pd_history_text[:1024],
            inline=False,
        )
    else:
        embed.add_field(
            name="PD Points",
            value="No PD point logs found.",
            inline=False,
        )

    if adjustments:
        adjustments_text = ""

        for adjustment in adjustments:
            amount = adjustment["amount"]
            adjustments_text += (
                f"**{amount:+d} points** "
                f"— {adjustment['reason']}\n"
                f"By: <@{adjustment['adjusted_by']}> • {adjustment['created_at']}\n\n"
            )

        embed.add_field(
            name="Adjustments",
            value=adjustments_text[:1024],
            inline=False,
        )
    else:
        embed.add_field(
            name="Adjustments",
            value="No manual adjustments found.",
            inline=False,
        )

    embed.set_thumbnail(url=actor.display_avatar.url)

    await interaction.response.send_message(embed=embed)


def split_lines_for_embed_fields(
    lines: list[str],
    max_length: int = 1000,
) -> list[str]:
    """Packs lines into chunks that fit within Discord's embed field character limit."""
    chunks: list[str] = []
    current_chunk = ""

    for line in lines:
        next_line = f"{line}\n"

        if len(current_chunk) + len(next_line) > max_length:
            if current_chunk:
                chunks.append(current_chunk)

            current_chunk = next_line
        else:
            current_chunk += next_line

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


@bot.tree.command(name="inactiveactors", description="Export actors inactive for a number of days")
async def inactiveactors(
    interaction: discord.Interaction,
    days: int,
    with_lore_team: bool = False
) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)

    if not is_senior(interaction.user):
        await interaction.followup.send(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    if days <= 0:
        await interaction.followup.send(
            "Days must be greater than 0.",
            ephemeral=True,
        )
        return

    try:
        inactive_actors = await get_inactive_actors(days)
    except Exception:
        await interaction.followup.send(
            "Error while fetching inactive actors.",
            ephemeral=True,
        )
        raise

    lines = []
    
    if not with_lore_team:
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("This command can only be used inside a server.", ephemeral=True)
            return
        
        for actor in inactive_actors:
            serverUser = guild.get_member(int(actor["discord_id"]))
            if serverUser is None:
                continue
            
            if has_lore_team_role(serverUser):
                print("\nFound Lore Team: ", serverUser.display_name)
                inactive_actors.remove(actor)
        

    for actor in inactive_actors:
        
        last_activity = actor["last_activity_at"]
        last_activity_text = (
            "No activity recorded"
            if last_activity is None
            else str(last_activity)
        )

        lines.append(
            f"<@{actor['discord_id']}> | "
            f"{actor['points']} pts | "
            f"{actor['current_rank']} | "
            f"{last_activity_text}"
        )

    file_buffer = StringIO(
        "\n".join(lines)
        if lines
        else "No inactive actors found."
    )

    file = discord.File(
        file_buffer,
        filename="inactive_actors.txt",
    )

    embed = discord.Embed(
        title="Inactive Actors Report",
        description=f"Actors inactive for **{days} days**",
        color=discord.Color.orange(),
    )

    embed.add_field(
        name="Total Inactive",
        value=str(len(lines)),
        inline=True,
    )

    embed.add_field(
        name="Export",
        value="Full list attached as a file.",
        inline=True,
    )

    await interaction.followup.send(
        embed=embed,
        file=file,
    )


@bot.tree.command(name="actorsbyrank", description="List all actors at a given rank")
async def actorsbyrank(
    interaction: discord.Interaction,
    rank: str,
    as_file: bool,
) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)

    if not is_senior(interaction.user):
        await interaction.followup.send(
            "You do not have permission to use this command.\n"
            "This command is restricted to Deputy Actor Manager and above.",
            ephemeral=True,
        )
        return

    clean_rank = rank.strip().upper()

    if clean_rank not in VALID_RANKS:
        await interaction.followup.send(
            "Invalid rank.\n"
            "Use one of: F, E, D, C, B, A, S.",
            ephemeral=True,
        )
        return

    try:
        actors = await get_actors_by_rank(clean_rank)
    except Exception:
        await interaction.followup.send(
            "Error. Something went wrong while getting actors by rank.",
            ephemeral=True,
        )
        raise

    embed = discord.Embed(
        title=f"Actors by Rank - [{clean_rank}] Actor",
        color=discord.Color.green(),
    )

    if not actors:
        embed.add_field(
            name="Results",
            value=f"No actors found with rank [{clean_rank}] Actor.",
            inline=False,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    lines = [
        f"<@{actor['discord_id']}> - **{actor['points']} points**"
        for actor in actors
    ]

    if as_file:
        file_buffer = StringIO(
            "\n".join(lines)
            if lines
            else "No actors in that rank were found."
        )

        file = discord.File(
            file_buffer,
            filename=f"actors_in_{rank}.txt",
        )

        await interaction.followup.send(file=file)
        return

    chunks = split_lines_for_embed_fields(lines)

    for index, chunk in enumerate(chunks[:25], start=1):
        field_name = "Actors" if index == 1 else f"Actors continued {index}"

        embed.add_field(
            name=field_name,
            value=chunk,
            inline=False,
        )

    if len(chunks) > 25:
        embed.set_footer(
            text=f"And {len(chunks) - 25} more chunks not shown..."
        )

    await interaction.followup.send(embed=embed)

async def is_member_missing_from_guild(guild: discord.Guild, discord_id: int) -> bool:
    """Returns True only if the member is confirmed absent. A cache miss triggers an API fetch before concluding."""
    cached_member = guild.get_member(discord_id)
    
    if cached_member is not None:
        return False
    
    try:
        await guild.fetch_member(discord_id)
        return False
        
    except discord.NotFound:
        return True
    
    except discord.HTTPException:
        raise
    

@bot.tree.command(name="prunemissingactors", description="Find and remove actors who have left the Discord server")
async def prunemissingactors(
    interaction: discord.Interaction,
    apply: bool = False,
    purge_roblox: bool = False,
) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)

    if not is_high_rank(interaction.user):
        await interaction.followup.send(
            "You do not have permission to use this command.\n"
            "This command is restricted to Actor Manager and above.",
            ephemeral=True,
        )
        return
    
    guild = interaction.guild

    if guild is None:
        await interaction.followup.send(
            "This command can only be used inside a server.",
            ephemeral=True,
        )
        return
    
    actors = await get_all_actors()

    if not actors:
        await interaction.followup.send(
            "There are no actors in the database.",
            ephemeral=True,
        )
        return
    
    missing_actors = []

    for actor in actors:
        discord_id = int(actor["discord_id"])

        try:
            is_missing = await is_member_missing_from_guild(guild, discord_id)
        
        except discord.HTTPException as error:
            await interaction.followup.send(
                "Discord API error while checking server members. "
                f"Stopped while checking `{discord_id}`.\n\n"
                f"Error: `{error}`",
                ephemeral=True,
            )
            return
        
        if is_missing:
            missing_actors.append(actor)

    if not missing_actors:
        log.info(
            "prunemissingactors: no missing actors found (checked %d total)",
            len(actors),
        )
        await interaction.followup.send(
            "No missing actors found. Everyone in the database is still in the server.",
            ephemeral=True,
        )
        return
    
    lines = [
        (
            f"<@{actor['discord_id']}> | "
            f"{actor['points']} pts | "
            f"Rank {actor['current_rank']}"
        )
        for actor in missing_actors
    ]

    file_buffer = StringIO("\n".join(lines))
    file = discord.File(
        file_buffer,
        filename="missing_actors.txt"
    )

    log.info(
        "prunemissingactors: found %d missing actors (apply=%s) triggered_by=%s (%d)",
        len(missing_actors),
        apply,
        interaction.user,
        interaction.user.id,
    )

    if not apply:
        embed = discord.Embed(
            title="Missing Actors Preview",
            description=(
                "These actors are in the database but no longer appear to be in the server.\n\n"
                "No data was deleted.\n"
                "Run `/prunemissingactors apply:true` to delete them.\n"
                "Add `purge_roblox:true` to also kick them from the Roblox group."
            ),
            color=discord.Color.orange(),
        )

        embed.add_field(
            name="Found",
            value=str(len(missing_actors)),
            inline=True,
        )

        await interaction.followup.send(
            embed=embed,
            file=file,
            ephemeral=True,
        )
        return

    deleted_count = await delete_actors_by_ids(
        [int(actor["discord_id"]) for actor in missing_actors]
    )

    roblox_kicked = 0
    roblox_configured = bool(os.getenv("ROBLOX_GROUP_ID") and os.getenv("ROBLOX_COOKIE"))

    if roblox_configured and purge_roblox:
        try:
            roblox_members = await get_group_members()
            roblox_by_username = {m["username"].lower(): m for m in roblox_members}

            for actor in missing_actors:
                try:
                    discord_user = await bot.fetch_user(int(actor["discord_id"]))
                    roblox_member = roblox_by_username.get(discord_user.name.lower())
                    if roblox_member:
                        kicked = await kick_group_member(roblox_member["user_id"])
                        if kicked:
                            roblox_kicked += 1
                except discord.HTTPException:
                    pass
        except Exception as roblox_error:
            log.error("prunemissingactors: Roblox kick step failed: %s", roblox_error)

    log.info(
        "prunemissingactors: deleted %d actors from DB, roblox_kicked=%d, purge_roblox=%s",
        deleted_count,
        roblox_kicked,
        purge_roblox,
    )

    embed = discord.Embed(
        title="Missing Actors Removed",
        description=(
            "Actors who were no longer in the server were removed from the database."
        ),
        color=discord.Color.red(),
    )

    embed.add_field(
        name="Removed",
        value=str(deleted_count),
        inline=True,
    )

    if roblox_configured and purge_roblox:
        embed.add_field(
            name="Roblox Kicked",
            value=str(roblox_kicked),
            inline=True,
        )

    await interaction.followup.send(
        embed=embed,
        file=file,
        ephemeral=True,
    )


@bot.tree.command(name="seedactors", description="Add all members with a role to the database with 0 points")
async def seedactors(
    interaction: discord.Interaction,
    role: discord.Role,
) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)

    if not is_high_rank(interaction.user):
        await interaction.followup.send(
            "You do not have permission to use this command.\n"
            "This command is restricted to Actor Manager and above.",
            ephemeral=True,
        )
        return

    members_with_role = [m for m in role.members if not m.bot]

    if not members_with_role:
        await interaction.followup.send(
            f"No members found with role {role.mention}.",
            ephemeral=True,
        )
        return

    existing_ids = {int(a["discord_id"]) for a in await get_all_actors()}
    new_members = [m for m in members_with_role if m.id not in existing_ids]
    skipped = len(members_with_role) - len(new_members)

    added = await seed_actors([m.id for m in new_members])

    roles_applied = 0
    if new_members and interaction.guild:
        f_role_name = next(r["role_name"] for r in ACTOR_RANKS if r["rank"] == "F")
        f_role = discord.utils.get(interaction.guild.roles, name=f_role_name)

        if f_role:
            for member in new_members:
                try:
                    await update_actor_rank_roles(
                        actor=member,
                        requested_role=f_role,
                        approved_by=interaction.user,
                    )
                    roles_applied += 1
                except discord.HTTPException as e:
                    log.warning("seedactors: failed to apply role to %d: %s", member.id, e)

    log.info(
        "seedactors: role=%s total=%d added=%d skipped=%d roles_applied=%d by=%s (%d)",
        role.name,
        len(members_with_role),
        added,
        skipped,
        roles_applied,
        interaction.user,
        interaction.user.id,
    )

    await interaction.followup.send(
        f"Seed complete for role **{role.name}**.\n"
        f"Total members: {len(members_with_role)}\n"
        f"Added to DB: {added}\n"
        f"Roles applied: {roles_applied}\n"
        f"Already in DB: {skipped}",
        ephemeral=True,
    )


@bot.tree.command(name="refreshroles", description="Sync Discord rank roles for all actors in the database")
async def refreshroles(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)

    if not is_senior(interaction.user):
        await interaction.followup.send(
            "You do not have permission to use this command.\n"
            "This command is restricted to Deputy Actor Manager and above.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("This command can only be used inside a server.", ephemeral=True)
        return

    actors = await get_all_actors()
    if not actors:
        await interaction.followup.send("No actors in the database.", ephemeral=True)
        return

    updated = 0
    skipped = 0
    failed = 0

    for actor in actors:
        member = guild.get_member(int(actor["discord_id"]))
        if member is None:
            skipped += 1
            continue

        correct_rank = get_rank_by_points(int(actor["points"]))
        current_rank_data = get_rank_by_name(actor["current_rank"])

        if current_rank_data is not None and correct_rank["points_required"] <= current_rank_data["points_required"]:
            skipped += 1
            continue

        role = discord.utils.get(guild.roles, name=correct_rank["role_name"])

        if role is None:
            log.warning("refreshroles: role %r not found in guild", correct_rank["role_name"])
            failed += 1
            continue

        try:
            await update_actor_rank_roles(
                actor=member,
                requested_role=role,
                approved_by=interaction.user,
            )
            updated += 1
        except discord.HTTPException as e:
            log.warning("refreshroles: failed to update roles for %d: %s", member.id, e)
            failed += 1

    log.info(
        "refreshroles: updated=%d skipped=%d failed=%d by=%s (%d)",
        updated, skipped, failed, interaction.user, interaction.user.id,
    )

    await interaction.followup.send(
        f"Role refresh complete.\n"
        f"Updated: {updated}\n"
        f"Not in server: {skipped}\n"
        f"Failed: {failed}",
        ephemeral=True,
    )


@bot.tree.command(
    name="prunerobloxmembers",
    description="Remove Roblox group members who are not in the Discord server",
)
async def prunerobloxmembers(
    interaction: discord.Interaction,
    apply: bool = False,
) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)

    if not is_senior(interaction.user):
        await interaction.followup.send(
            "You do not have permission to use this command.\n"
            "This command is restricted to Deputy Actor Manager and above.",
            ephemeral=True,
        )
        return

    guild = interaction.guild

    if guild is None:
        await interaction.followup.send(
            "This command can only be used inside a server.",
            ephemeral=True,
        )
        return

    if not os.getenv("ROBLOX_GROUP_ID"):
        await interaction.followup.send(
            "ROBLOX_GROUP_ID is not configured.",
            ephemeral=True,
        )
        return

    if apply and not os.getenv("ROBLOX_COOKIE"):
        await interaction.followup.send(
            "ROBLOX_COOKIE is not configured. Cannot kick members without authentication.",
            ephemeral=True,
        )
        return

    try:
        roblox_members = await get_group_members()
    except Exception as error:
        log.error("prunerobloxmembers: failed to fetch Roblox group members: %s", error)
        await interaction.followup.send(
            f"Failed to fetch Roblox group members.\nError: `{error}`",
            ephemeral=True,
        )
        return

    if not roblox_members:
        await interaction.followup.send(
            "No members found in the Roblox group.",
            ephemeral=True,
        )
        return

    if not guild.chunked:
        await guild.chunk()

    discord_names = {
        member.nick.lower()
        for member in guild.members
        if member.nick is not None
    }

    not_in_discord = [
        m for m in roblox_members
        if m["username"].lower() not in discord_names
    ]

    if not not_in_discord:
        log.info(
            "prunerobloxmembers: all %d Roblox members matched Discord (apply=%s)",
            len(roblox_members),
            apply,
        )
        await interaction.followup.send(
            "All Roblox group members have a matching username in the Discord server.",
            ephemeral=True,
        )
        return

    lines = [
        f"{m['username']} (ID: {m['user_id']})"
        for m in not_in_discord
    ]

    file_buffer = StringIO("\n".join(lines))
    file = discord.File(file_buffer, filename="roblox_not_in_discord.txt")

    log.info(
        "prunerobloxmembers: %d/%d Roblox members not in Discord (apply=%s) triggered_by=%s (%d)",
        len(not_in_discord),
        len(roblox_members),
        apply,
        interaction.user,
        interaction.user.id,
    )

    if not apply:
        embed = discord.Embed(
            title="Roblox Members Not in Discord — Preview",
            description=(
                "These Roblox group members have no matching username in the Discord server.\n\n"
                "No action was taken.\n"
                "Run `/prunerobloxmembers apply:true` to kick them from the group."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(name="Found", value=str(len(not_in_discord)), inline=True)
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)
        return

    kicked = 0
    failed = 0

    for member in not_in_discord:
        try:
            success = await kick_group_member(member["user_id"])
            if success:
                kicked += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    log.info(
        "prunerobloxmembers: kicked=%d failed=%d from Roblox group",
        kicked,
        failed,
    )

    embed = discord.Embed(
        title="Roblox Members Kicked",
        description="Roblox group members without a matching Discord username have been removed from the group.",
        color=discord.Color.red(),
    )
    embed.add_field(name="Kicked", value=str(kicked), inline=True)

    if failed:
        embed.add_field(name="Failed", value=str(failed), inline=True)

    await interaction.followup.send(embed=embed, file=file, ephemeral=True)


@bot.tree.command(
    name="acceptrobloxmembers",
    description="Accept pending Roblox group join requests by username",
)
async def acceptrobloxmembers(
    interaction: discord.Interaction,
    usernames: str,
) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)

    if not is_high_rank(interaction.user):
        await interaction.followup.send(
            "You do not have permission to use this command.\n"
            "This command is restricted to Actor Manager and above.",
            ephemeral=True,
        )
        return

    roblox_configured = bool(os.getenv("ROBLOX_GROUP_ID") and os.getenv("ROBLOX_COOKIE"))
    if not roblox_configured:
        await interaction.followup.send(
            "Roblox integration is not configured (missing `ROBLOX_GROUP_ID` or `ROBLOX_COOKIE`).",
            ephemeral=True,
        )
        return

    name_list = [n.strip() for n in usernames.replace(",", " ").split() if n.strip()]

    if not name_list:
        await interaction.followup.send(
            "No usernames provided.",
            ephemeral=True,
        )
        return

    try:
        resolved = await lookup_users_by_usernames(name_list)
    except Exception as error:
        await interaction.followup.send(
            f"Failed to look up Roblox usernames: `{error}`",
            ephemeral=True,
        )
        return

    resolved_by_requested = {r["requestedUsername"].lower(): r for r in resolved}

    not_found = [n for n in name_list if n.lower() not in resolved_by_requested]
    accepted: list[str] = []
    failed: list[str] = []

    for entry in resolved:
        try:
            success = await accept_join_request(entry["id"])
            if success:
                accepted.append(entry["name"])
            else:
                failed.append(entry["name"])
        except Exception:
            failed.append(entry["name"])

    log.info(
        "acceptrobloxmembers: accepted=%d failed=%d not_found=%d triggered_by=%s (%d)",
        len(accepted),
        len(failed),
        len(not_found),
        interaction.user,
        interaction.user.id,
    )

    embed = discord.Embed(
        title="Roblox Group Join Requests",
        color=discord.Color.green() if not failed and not not_found else discord.Color.orange(),
    )

    if accepted:
        embed.add_field(
            name=f"Accepted ({len(accepted)})",
            value="\n".join(accepted),
            inline=False,
        )

    if failed:
        embed.add_field(
            name=f"Failed ({len(failed)})",
            value="\n".join(failed),
            inline=False,
        )

    if not_found:
        embed.add_field(
            name=f"Not Found ({len(not_found)})",
            value="\n".join(not_found),
            inline=False,
        )

    await interaction.followup.send(embed=embed, ephemeral=True)


LORE_ACTOR_ROBLOX_RANK = "Lore Actor"
MAP_BYPASS_ROBLOX_RANK = "Map Bypass Rank"


@bot.tree.command(
    name="cleanserobloxgroupranks",
    description="Remove Map Bypass Rank from Roblox group members who have the Lore Actor rank",
)
async def cleanserobloxgroupranks(
    interaction: discord.Interaction,
    apply: bool = False,
) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)

    if not is_high_rank(interaction.user):
        await interaction.followup.send(
            "You do not have permission to use this command.\n"
            "This command is restricted to Actor Manager and above.",
            ephemeral=True,
        )
        return

    roblox_configured = bool(os.getenv("ROBLOX_GROUP_ID") and os.getenv("ROBLOX_COOKIE"))
    if not roblox_configured:
        await interaction.followup.send(
            "Roblox integration is not configured (missing `ROBLOX_GROUP_ID` or `ROBLOX_COOKIE`).",
            ephemeral=True,
        )
        return

    try:
        roles = await get_group_roles()
    except Exception as error:
        await interaction.followup.send(
            f"Failed to fetch Roblox group roles: `{error}`",
            ephemeral=True,
        )
        return

    roles_by_name = {r["name"].lower(): r for r in roles}
    lore_actor_role = roles_by_name.get(LORE_ACTOR_ROBLOX_RANK.lower())
    map_bypass_role = roles_by_name.get(MAP_BYPASS_ROBLOX_RANK.lower())

    if lore_actor_role is None:
        await interaction.followup.send(
            f"Could not find a Roblox group role named **{LORE_ACTOR_ROBLOX_RANK}**.",
            ephemeral=True,
        )
        return

    if map_bypass_role is None:
        await interaction.followup.send(
            f"Could not find a Roblox group role named **{MAP_BYPASS_ROBLOX_RANK}**.",
            ephemeral=True,
        )
        return

    try:
        map_bypass_members = await get_members_with_role(map_bypass_role["id"])
    except Exception as error:
        await interaction.followup.send(
            f"Failed to fetch members with **{MAP_BYPASS_ROBLOX_RANK}**: `{error}`",
            ephemeral=True,
        )
        return

    try:
        lore_actor_members = await get_members_with_role(lore_actor_role["id"])
    except Exception as error:
        await interaction.followup.send(
            f"Failed to fetch members with **{LORE_ACTOR_ROBLOX_RANK}**: `{error}`",
            ephemeral=True,
        )
        return

    # Find users who hold both ranks simultaneously
    lore_actor_ids = {m["user_id"] for m in lore_actor_members}
    to_cleanse = [m for m in map_bypass_members if m["user_id"] in lore_actor_ids]

    log.info(
        "cleanserobloxgroupranks: %d with %s, %d with %s, %d to cleanse (apply=%s) triggered_by=%s (%d)",
        len(map_bypass_members),
        MAP_BYPASS_ROBLOX_RANK,
        len(lore_actor_members),
        LORE_ACTOR_ROBLOX_RANK,
        len(to_cleanse),
        apply,
        interaction.user,
        interaction.user.id,
    )

    if not to_cleanse:
        await interaction.followup.send(
            f"No **{LORE_ACTOR_ROBLOX_RANK}** members currently have **{MAP_BYPASS_ROBLOX_RANK}**. Nothing to cleanse.",
            ephemeral=True,
        )
        return

    lines = [f"{m['username']} (ID: {m['user_id']})" for m in to_cleanse]
    file_buffer = StringIO("\n".join(lines))
    file = discord.File(file_buffer, filename="cleanse_targets.txt")

    if not apply:
        embed = discord.Embed(
            title="Roblox Group Rank Cleanse — Preview",
            description=(
                f"These **{LORE_ACTOR_ROBLOX_RANK}** members also hold **{MAP_BYPASS_ROBLOX_RANK}**.\n\n"
                "No changes were made.\n"
                f"Run `/cleanserobloxgroupranks apply:true` to remove **{MAP_BYPASS_ROBLOX_RANK}** "
                "from them (their Lore Actor rank will not be touched)."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(name="Found", value=str(len(to_cleanse)), inline=True)
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)
        return

    cleansed = 0
    failed = 0

    for member in to_cleanse:
        try:
            success = await remove_member_rank(member["user_id"], map_bypass_role["id"])
            if success:
                cleansed += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    log.info(
        "cleanserobloxgroupranks: cleansed=%d failed=%d",
        cleansed,
        failed,
    )

    embed = discord.Embed(
        title="Roblox Group Rank Cleanse Complete",
        description=(
            f"**{MAP_BYPASS_ROBLOX_RANK}** has been removed from {cleansed} "
            f"**{LORE_ACTOR_ROBLOX_RANK}** member(s). Their Lore Actor rank is unchanged."
        ),
        color=discord.Color.green(),
    )
    embed.add_field(name="Cleansed", value=str(cleansed), inline=True)

    if failed:
        embed.add_field(name="Failed", value=str(failed), inline=True)

    await interaction.followup.send(embed=embed, file=file, ephemeral=True)


bot.run(TOKEN)
