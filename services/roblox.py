import logging
import os

import aiohttp

log = logging.getLogger("lore_points.roblox")

_bot_roblox_id: int | None = None


async def get_bot_roblox_id() -> int | None:
    """Fetches and caches the bot account's own Roblox user ID to prevent self-kicks."""
    global _bot_roblox_id
    if _bot_roblox_id is not None:
        return _bot_roblox_id

    cookie = os.getenv("ROBLOX_COOKIE", "")
    if not cookie:
        return None

    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://users.roblox.com/v1/users/authenticated",
            headers={"Cookie": f".ROBLOSECURITY={cookie}"},
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            _bot_roblox_id = int(data["id"])
            return _bot_roblox_id


async def get_group_members() -> list[dict]:
    """Returns all members in the group. Roblox has no single endpoint for this, so we paginate per role."""
    group_id = os.getenv("ROBLOX_GROUP_ID", "")
    cookie = os.getenv("ROBLOX_COOKIE", "")
    if not group_id:
        raise ValueError("ROBLOX_GROUP_ID not configured")

    headers = {}
    if cookie:
        headers["Cookie"] = f".ROBLOSECURITY={cookie}"

    members: list[dict] = []
    seen_ids: set[int] = set()

    async with aiohttp.ClientSession(headers=headers) as session:
        # Step 1: fetch all roles in the group
        async with session.get(
            f"https://groups.roblox.com/v1/groups/{group_id}/roles"
        ) as resp:
            resp.raise_for_status()
            roles_data = await resp.json()

        role_ids = [
            role["id"]
            for role in roles_data.get("roles", [])
            if role.get("memberCount", 1) > 0
        ]

        # Step 2: paginate members for each role
        for role_id in role_ids:
            cursor = ""

            while True:
                params: dict = {"limit": 100, "sortOrder": "Asc"}
                if cursor:
                    params["cursor"] = cursor

                async with session.get(
                    f"https://groups.roblox.com/v1/groups/{group_id}/roles/{role_id}/users",
                    params=params,
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()

                for entry in data.get("data", []):
                    uid = entry["userId"]
                    if uid in seen_ids:
                        continue
                    seen_ids.add(uid)
                    members.append(
                        {
                            "user_id": uid,
                            "username": entry["username"],
                            "display_name": entry["displayName"],
                        }
                    )

                cursor = data.get("nextPageCursor") or ""
                if not cursor:
                    break

    return members


async def get_group_roles() -> list[dict]:
    group_id = os.getenv("ROBLOX_GROUP_ID", "")
    cookie = os.getenv("ROBLOX_COOKIE", "")
    if not group_id:
        raise ValueError("ROBLOX_GROUP_ID not configured")

    headers = {}
    if cookie:
        headers["Cookie"] = f".ROBLOSECURITY={cookie}"

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(
            f"https://groups.roblox.com/v1/groups/{group_id}/roles"
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("roles", [])


async def get_members_with_role(role_id: int) -> list[dict]:
    """Returns all members holding a specific role, paginating until exhausted."""
    group_id = os.getenv("ROBLOX_GROUP_ID", "")
    cookie = os.getenv("ROBLOX_COOKIE", "")
    if not group_id:
        raise ValueError("ROBLOX_GROUP_ID not configured")

    headers = {}
    if cookie:
        headers["Cookie"] = f".ROBLOSECURITY={cookie}"

    members: list[dict] = []
    cursor = ""

    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            params: dict = {"limit": 100, "sortOrder": "Asc"}
            if cursor:
                params["cursor"] = cursor

            async with session.get(
                f"https://groups.roblox.com/v1/groups/{group_id}/roles/{role_id}/users",
                params=params,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

            for entry in data.get("data", []):
                members.append({
                    "user_id": entry["userId"],
                    "username": entry["username"],
                    "display_name": entry["displayName"],
                })

            cursor = data.get("nextPageCursor") or ""
            if not cursor:
                break

    return members


async def set_member_rank(user_id: int, role_id: int) -> bool:
    group_id = os.getenv("ROBLOX_GROUP_ID", "")
    cookie = os.getenv("ROBLOX_COOKIE", "")

    if not group_id or not cookie:
        return False

    url = f"https://groups.roblox.com/v1/groups/{group_id}/users/{user_id}"
    headers = {"Cookie": f".ROBLOSECURITY={cookie}"}

    # Roblox write endpoints require a CSRF token. The first request always returns 403
    # with the token in the response headers, so we retry immediately with it.
    async with aiohttp.ClientSession() as session:
        async with session.patch(url, headers=headers, json={"roleId": role_id}) as resp:
            body = await resp.read()
            if resp.status == 200:
                return True
            csrf = resp.headers.get("x-csrf-token")
            if resp.status != 403 or not csrf:
                log.warning(
                    "set_member_rank: user=%d role=%d status=%d body=%s",
                    user_id, role_id, resp.status, body[:300],
                )
                return False
            headers["X-CSRF-TOKEN"] = csrf

        async with session.patch(url, headers=headers, json={"roleId": role_id}) as resp:
            body = await resp.read()
            if resp.status != 200:
                log.warning(
                    "set_member_rank retry: user=%d role=%d status=%d body=%s",
                    user_id, role_id, resp.status, body[:300],
                )
            return resp.status == 200


async def remove_member_rank(user_id: int, role_id: int) -> bool:
    group_id = os.getenv("ROBLOX_GROUP_ID", "")
    cookie = os.getenv("ROBLOX_COOKIE", "")

    if not group_id or not cookie:
        return False

    url = f"https://groups.roblox.com/v1/groups/{group_id}/roles/{role_id}/users/{user_id}"
    headers = {"Cookie": f".ROBLOSECURITY={cookie}"}

    async with aiohttp.ClientSession() as session:
        async with session.delete(url, headers=headers) as resp:
            body = await resp.read()
            if resp.status == 200:
                return True
            csrf = resp.headers.get("x-csrf-token")
            if resp.status != 403 or not csrf:
                log.warning(
                    "remove_member_rank: user=%d role=%d status=%d body=%s",
                    user_id, role_id, resp.status, body[:300],
                )
                return False
            headers["X-CSRF-TOKEN"] = csrf

        async with session.delete(url, headers=headers) as resp:
            body = await resp.read()
            if resp.status != 200:
                log.warning(
                    "remove_member_rank retry: user=%d role=%d status=%d body=%s",
                    user_id, role_id, resp.status, body[:300],
                )
            return resp.status == 200


async def lookup_users_by_usernames(usernames: list[str]) -> list[dict]:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": usernames, "excludeBannedUsers": False},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("data", [])


async def accept_join_request(user_id: int) -> bool:
    group_id = os.getenv("ROBLOX_GROUP_ID", "")
    cookie = os.getenv("ROBLOX_COOKIE", "")

    if not group_id or not cookie:
        return False

    url = f"https://groups.roblox.com/v1/groups/{group_id}/join-requests/users/{user_id}"
    headers = {"Cookie": f".ROBLOSECURITY={cookie}"}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers) as resp:
            if resp.status == 200:
                return True
            if resp.status != 403:
                return False
            csrf = resp.headers.get("x-csrf-token")
            if not csrf:
                return False
            headers["X-CSRF-TOKEN"] = csrf

        async with session.post(url, headers=headers) as resp:
            return resp.status == 200


async def kick_group_member(user_id: int) -> bool:
    """Removes a member from the group. Returns False without making a request if the user is the bot itself."""
    group_id = os.getenv("ROBLOX_GROUP_ID", "")
    cookie = os.getenv("ROBLOX_COOKIE", "")

    if not group_id or not cookie:
        return False

    bot_id = await get_bot_roblox_id()
    if bot_id is not None and user_id == bot_id:
        return False

    url = f"https://groups.roblox.com/v1/groups/{group_id}/users/{user_id}"
    headers = {"Cookie": f".ROBLOSECURITY={cookie}"}

    async with aiohttp.ClientSession() as session:
        async with session.delete(url, headers=headers) as resp:
            if resp.status == 200:
                return True
            if resp.status != 403:
                return False
            csrf = resp.headers.get("x-csrf-token")
            if not csrf:
                return False
            headers["X-CSRF-TOKEN"] = csrf

        async with session.delete(url, headers=headers) as resp:
            return resp.status == 200
