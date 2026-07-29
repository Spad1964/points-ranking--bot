import asyncio
import logging
import os
from collections.abc import Sequence
from typing import Any

import aiosqlite

DATABASE_NAME = os.getenv("DATABASE_NAME", "lore_points.db")

log = logging.getLogger("lore_points.db")

_db: aiosqlite.Connection | None = None

# SQLite WAL mode allows concurrent reads but only one writer at a time.
# Without this lock, concurrent Discord events can produce "database is locked" errors.
_db_write_lock = asyncio.Lock()


async def get_db() -> aiosqlite.Connection:
    """Returns the shared DB connection, opening it on first call."""
    global _db

    if _db is None:
        _db = await aiosqlite.connect(DATABASE_NAME)
        _db.row_factory = aiosqlite.Row

        await _db.execute("PRAGMA foreign_keys = ON")
        await _db.execute("PRAGMA journal_mode = WAL")
        await _db.execute("PRAGMA synchronous = NORMAL")

    return _db


async def close_db() -> None:
    global _db

    if _db is not None:
        await _db.close()
        _db = None


async def init_db() -> None:
    log.info("Initializing database: %s", DATABASE_NAME)
    db = await get_db()

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS actors (
            discord_id INTEGER PRIMARY KEY,
            points INTEGER NOT NULL DEFAULT 0,
            current_rank TEXT NOT NULL DEFAULT 'F'
        )
        """
    )

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS point_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id INTEGER NOT NULL,
            length_points INTEGER NOT NULL DEFAULT 0,
            roleplay_points INTEGER NOT NULL DEFAULT 0,
            combat_points INTEGER NOT NULL DEFAULT 0,
            total_points INTEGER NOT NULL DEFAULT 0,
            awarded_by INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS promotion_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id INTEGER NOT NULL,
            requested_rank TEXT NOT NULL,
            requested_role_name TEXT NOT NULL,
            points INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_by INTEGER,
            message_id INTEGER
        )
        """
    )

    # Backward-compat migration: adds message_id to databases created before that column existed.
    # Fails silently on databases that already have it.
    try:
        await db.execute(
            """
            ALTER TABLE promotion_requests
            ADD COLUMN message_id INTEGER
            """
        )
    except aiosqlite.OperationalError:
        pass

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS point_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            reason TEXT NOT NULL,
            adjusted_by INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_point_logs_discord_created
        ON point_logs(discord_id, created_at DESC, id DESC)
        """
    )

    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_point_adjustments_discord_created
        ON point_adjustments(discord_id, created_at DESC, id DESC)
        """
    )

    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_promotion_requests_active
        ON promotion_requests(discord_id, requested_rank, status)
        """
    )

    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_actors_current_rank_points
        ON actors(current_rank, points DESC)
        """
    )

    await db.commit()
    log.info("Database initialized successfully")


async def fetch_one(query: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
    db = await get_db()
    cursor = await db.execute(query, params)
    return await cursor.fetchone()


async def fetch_all(query: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
    db = await get_db()
    cursor = await db.execute(query, params)
    return await cursor.fetchall()


async def get_actor(discord_id: int) -> aiosqlite.Row | None:
    return await fetch_one(
        """
        SELECT discord_id, points, current_rank
        FROM actors
        WHERE discord_id = ?
        """,
        (int(discord_id),),
    )


async def get_all_actors() -> list[aiosqlite.Row]:
    return await fetch_all(
        """
        SELECT
            discord_id,
            points,
            current_rank
        FROM actors
        ORDER BY points DESC
        """
    )


async def add_points_to_actors(
    discord_ids: Sequence[int],
    length_points: int = 0,
    roleplay_points: int = 0,
    combat_points: int = 0,
    awarded_by: int = 0,
) -> list[aiosqlite.Row]:
    """Awards points to all listed actors, creating DB rows for any who don't exist yet."""
    unique_discord_ids = list(dict.fromkeys(int(discord_id) for discord_id in discord_ids))

    if not unique_discord_ids:
        return []

    total_points = int(length_points) + int(roleplay_points) + int(combat_points)
    db = await get_db()

    async with _db_write_lock:
        try:
            await db.execute("BEGIN")

            await db.executemany(
                """
                INSERT INTO actors (discord_id, points, current_rank)
                VALUES (?, ?, 'F')
                ON CONFLICT(discord_id)
                DO UPDATE SET points = points + excluded.points
                """,
                [(discord_id, total_points) for discord_id in unique_discord_ids],
            )

            await db.executemany(
                """
                INSERT INTO point_logs (
                    discord_id,
                    length_points,
                    roleplay_points,
                    combat_points,
                    total_points,
                    awarded_by
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        discord_id,
                        int(length_points),
                        int(roleplay_points),
                        int(combat_points),
                        total_points,
                        int(awarded_by),
                    )
                    for discord_id in unique_discord_ids
                ],
            )

            placeholders = ", ".join("?" for _ in unique_discord_ids)

            cursor = await db.execute(
                f"""
                SELECT discord_id, points, current_rank
                FROM actors
                WHERE discord_id IN ({placeholders})
                """,
                unique_discord_ids,
            )

            actors = await cursor.fetchall()

            await db.commit()
            return actors

        except Exception:
            await db.rollback()
            log.error("add_points_to_actors: transaction rolled back", exc_info=True)
            raise


async def get_existing_active_promotion_request(
    discord_id: int,
    requested_rank: str,
) -> aiosqlite.Row | None:
    """Checks both pending and approved so a second request isn't fired if the actor re-qualifies."""
    return await fetch_one(
        """
        SELECT id, discord_id, requested_rank, requested_role_name, points, status
        FROM promotion_requests
        WHERE discord_id = ?
          AND requested_rank = ?
          AND status IN ('pending', 'approved')
        """,
        (int(discord_id), requested_rank),
    )


async def create_promotion_request(
    discord_id: int,
    requested_rank: str,
    requested_role_name: str,
    points: int,
) -> int:
    db = await get_db()

    async with _db_write_lock:
        cursor = await db.execute(
            """
            INSERT INTO promotion_requests (
                discord_id,
                requested_rank,
                requested_role_name,
                points
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                int(discord_id),
                requested_rank,
                requested_role_name,
                int(points),
            ),
        )

        await db.commit()
        return int(cursor.lastrowid)


async def get_promotion_request_by_id(request_id: int) -> aiosqlite.Row | None:
    return await fetch_one(
        """
        SELECT
            id,
            discord_id,
            requested_rank,
            requested_role_name,
            points,
            status,
            approved_by
        FROM promotion_requests
        WHERE id = ?
        """,
        (int(request_id),),
    )


async def approve_promotion_request(
    request_id: int,
    approved_by: int,
) -> dict[str, int | str] | None:
    """Marks the request approved and updates the actor's current_rank in one transaction."""
    db = await get_db()

    async with _db_write_lock:
        try:
            await db.execute("BEGIN")

            cursor = await db.execute(
                """
                SELECT discord_id, requested_rank
                FROM promotion_requests
                WHERE id = ?
                  AND status = 'pending'
                """,
                (int(request_id),),
            )

            promotion_request = await cursor.fetchone()

            if promotion_request is None:
                await db.rollback()
                return None

            discord_id = int(promotion_request["discord_id"])
            requested_rank = str(promotion_request["requested_rank"])

            await db.execute(
                """
                UPDATE promotion_requests
                SET status = 'approved',
                    approved_by = ?
                WHERE id = ?
                """,
                (int(approved_by), int(request_id)),
            )

            await db.execute(
                """
                UPDATE actors
                SET current_rank = ?
                WHERE discord_id = ?
                """,
                (requested_rank, discord_id),
            )

            await db.commit()

            return {
                "discord_id": discord_id,
                "requested_rank": requested_rank,
            }

        except Exception:
            await db.rollback()
            log.error("approve_promotion_request: transaction rolled back", exc_info=True)
            raise


async def deny_promotion_request(request_id: int, denied_by: int) -> bool:
    """Marks the request denied. The denier's ID is stored in approved_by — the column serves both outcomes."""
    db = await get_db()

    async with _db_write_lock:
        cursor = await db.execute(
            """
            UPDATE promotion_requests
            SET status = 'denied',
                approved_by = ?
            WHERE id = ?
              AND status = 'pending'
            """,
            (int(denied_by), int(request_id)),
        )

        await db.commit()
        return cursor.rowcount > 0


async def update_promotion_request_message_id(
    request_id: int,
    message_id: int,
) -> None:
    db = await get_db()

    async with _db_write_lock:
        await db.execute(
            """
            UPDATE promotion_requests
            SET message_id = ?
            WHERE id = ?
            """,
            (int(message_id), int(request_id)),
        )

        await db.commit()


async def get_pending_promotion_requests() -> list[aiosqlite.Row]:
    """Returns pending requests that have a message_id — requests without one have no Discord message to restore."""
    return await fetch_all(
        """
        SELECT
            id,
            discord_id,
            requested_rank,
            requested_role_name,
            points,
            status,
            message_id
        FROM promotion_requests
        WHERE status = 'pending'
          AND message_id IS NOT NULL
        """
    )


async def get_latest_point_log(discord_id: int) -> aiosqlite.Row | None:
    return await fetch_one(
        """
        SELECT
            id,
            discord_id,
            length_points,
            roleplay_points,
            combat_points,
            total_points,
            awarded_by,
            created_at
        FROM point_logs
        WHERE discord_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (int(discord_id),),
    )



async def adjust_points_and_get_actor(
    discord_id: int,
    amount: int,
    reason: str,
    adjusted_by: int,
) -> aiosqlite.Row | None:
    """Upserts the actor row, records the adjustment, and returns the updated actor."""
    db = await get_db()

    async with _db_write_lock:
        try:
            await db.execute("BEGIN")

            await db.execute(
                """
                INSERT INTO actors (discord_id, points, current_rank)
                VALUES (?, ?, 'F')
                ON CONFLICT(discord_id)
                DO UPDATE SET points = points + excluded.points
                """,
                (int(discord_id), int(amount)),
            )

            await db.execute(
                """
                INSERT INTO point_adjustments (
                    discord_id,
                    amount,
                    reason,
                    adjusted_by
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    int(discord_id),
                    int(amount),
                    reason,
                    int(adjusted_by),
                ),
            )

            cursor = await db.execute(
                """
                SELECT discord_id, points, current_rank
                FROM actors
                WHERE discord_id = ?
                """,
                (int(discord_id),),
            )

            actor = await cursor.fetchone()

            await db.commit()
            return actor

        except Exception:
            await db.rollback()
            log.error("adjust_points_and_get_actor: transaction rolled back", exc_info=True)
            raise


async def get_point_logs(
    discord_id: int,
    limit: int | None = 5,
) -> list[aiosqlite.Row]:
    if limit is None:
        return await fetch_all(
            """
            SELECT
                id,
                length_points,
                roleplay_points,
                combat_points,
                total_points,
                awarded_by,
                created_at
            FROM point_logs
            WHERE discord_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (int(discord_id),),
        )

    return await fetch_all(
        """
        SELECT
            id,
            length_points,
            roleplay_points,
            combat_points,
            total_points,
            awarded_by,
            created_at
        FROM point_logs
        WHERE discord_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (int(discord_id), int(limit)),
    )


async def get_point_adjustments(
    discord_id: int,
    limit: int | None = 5,
) -> list[aiosqlite.Row]:
    if limit is None:
        return await fetch_all(
            """
            SELECT
                id,
                amount,
                reason,
                adjusted_by,
                created_at
            FROM point_adjustments
            WHERE discord_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (int(discord_id),),
        )

    return await fetch_all(
        """
        SELECT
            id,
            amount,
            reason,
            adjusted_by,
            created_at
        FROM point_adjustments
        WHERE discord_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (int(discord_id), int(limit)),
    )


async def get_inactive_actors(days: int) -> list[aiosqlite.Row]:
    """Returns actors whose last activity across point_logs and point_adjustments is older than `days`."""
    return await fetch_all(
        """
        SELECT
            actors.discord_id,
            actors.points,
            actors.current_rank,
            MAX(activity.created_at) AS last_activity_at
        FROM actors
        LEFT JOIN (
            SELECT discord_id, created_at FROM point_logs
            UNION ALL
            SELECT discord_id, created_at FROM point_adjustments
        ) AS activity
            ON actors.discord_id = activity.discord_id
        GROUP BY actors.discord_id
        HAVING last_activity_at IS NULL
            OR last_activity_at <= datetime('now', '-' || ? || ' days')
        ORDER BY last_activity_at ASC, actors.points DESC
        """,
        (int(days),),
    )


async def get_actors_by_rank(rank: str) -> list[aiosqlite.Row]:
    return await fetch_all(
        """
        SELECT discord_id, points, current_rank
        FROM actors
        WHERE current_rank = ?
        ORDER BY points DESC
        """,
        (rank.upper(),),
    )


async def delete_actors_by_ids(discord_ids: Sequence[int]) -> int:
    """Deletes actors and all their related rows (point_logs, point_adjustments, promotion_requests)."""
    unique_discord_ids = list(
        dict.fromkeys(int(discord_id) for discord_id in discord_ids)
    )

    if not unique_discord_ids:
        return 0
    
    placeholders = ", ".join("?" for _ in unique_discord_ids)

    db = await get_db()

    async with _db_write_lock:
        try:
            await db.execute("BEGIN")

            await db.execute(
                f"""
                DELETE FROM point_logs
                WHERE discord_id IN ({placeholders})
                """,
                unique_discord_ids,
            )

            await db.execute(
                f"""
                DELETE FROM point_adjustments
                WHERE discord_id IN ({placeholders})
                """,
                unique_discord_ids,
            )

            await db.execute(
                f"""
                DELETE FROM promotion_requests
                WHERE discord_id IN ({placeholders})
                """,
                unique_discord_ids,
            )

            cursor = await db.execute(
                f"""
                DELETE FROM actors
                WHERE discord_id IN ({placeholders})
                """,
                unique_discord_ids,
            )

            await db.commit()

            return int(cursor.rowcount)

        except Exception:
            await db.rollback()
            log.error("delete_actors_by_ids: transaction rolled back", exc_info=True)
            raise


async def seed_actors(discord_ids: Sequence[int]) -> int:
    """Inserts actors at rank F with 0 points, silently skipping IDs already in the database."""
    unique_ids = list(dict.fromkeys(int(did) for did in discord_ids))

    if not unique_ids:
        return 0

    db = await get_db()

    async with _db_write_lock:
        cursor = await db.executemany(
            "INSERT OR IGNORE INTO actors (discord_id, points, current_rank) VALUES (?, 0, 'F')",
            [(did,) for did in unique_ids],
        )
        await db.commit()
        return int(cursor.rowcount)
