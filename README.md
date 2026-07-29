# Lore Points Bot

A Discord bot that manages a point-based rank progression system for Lore Actors. It tracks session points, automates promotion requests with interactive Discord buttons, and includes Roblox group management tools.

## What It Does

- **Points tracking** — Post a structured message after each roleplay session. The bot parses it, credits all mentioned actors, and logs the breakdown by category (Length, Roleplay, Combat).
- **Automatic promotion requests** — When an actor's total crosses a rank threshold, the bot posts a request in the promotions channel with Approve and Deny buttons. Approving updates the actor's Discord roles and database rank automatically.
- **Actor management** — Commands to view profiles, point history, inactive actors, and bulk-manage records.
- **Roblox group tools** — Optional integration to manage Roblox group membership alongside Discord roles.

## Setup

### Requirements

- Python 3.13+
- A Discord bot token with **Message Content** and **Server Members** intents enabled in the Discord Developer Portal

### Installation

```bash
git clone <repo-url>
cd lore-points-bot
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in the project root (see Environment Variables below), then run:

```bash
python bot.py
```

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | Yes | Your Discord bot token |
| `DATABASE_NAME` | No | SQLite filename (default: `lore_points.db`) |
| `POINTS_DISTRIBUTION_CHANNEL_ID` | No | Channel ID where points messages are posted. Falls back to a channel named `point-distribution`. |
| `PROMOTION_REQUESTS_CHANNEL_ID` | No | Channel ID where promotion requests are posted. Falls back to a channel named `promotion-requests`. |
| `ROBLOX_GROUP_ID` | No | Roblox group ID. Required to use any Roblox command. |
| `ROBLOX_COOKIE` | No | `.ROBLOSECURITY` cookie for the bot's Roblox account. Required for write operations (kick, accept, set rank). |

Using channel IDs is preferred over channel names — IDs are stable even if the channel is renamed.

## Points System

### Awarding Points

Points are awarded by posting a message in the configured points channel. Only members with a Lore Team role can do this.

```
Names: @Actor1 @Actor2
Length: 3
Roleplay: 2
Combat: 1
```

All four fields are required. Each score field accepts a whole number or `N/A` / `-` for zero. All mentioned actors receive the same point total.

### Rank Thresholds

| Rank | Discord Role | Points Required |
|---|---|---|
| F | [F] Actor | 0 |
| E | [E] Actor | 200 |
| D | [D] Actor | 400 |
| C | [C] Actor | 700 |
| B | [B] Actor | 1,000 |
| A | [A] Actor | 1,500 |
| S | [S] Actor | 2,000 |

When an actor's total crosses a threshold, a promotion request is automatically posted in the promotions channel. If an identical pending or approved request already exists for that actor and rank, a duplicate is not created.

## Permission Levels

| Level | Who | Used For |
|---|---|---|
| **Lore Team** | Trial Lore Team, Lore Team, Deputy Actor Manager, Senior Lore Team, Actor Manager, Deputy Head of Lore, Head of Lore | Awarding points via message |
| **Senior** | Deputy Actor Manager, Senior Lore Team, Actor Manager, Executive Department, Deputy Head of Lore, Head of Lore | Management commands; approving D, C, B promotions |
| **High Rank** | Actor Manager, Executive Department, Deputy Head of Lore, Head of Lore | Destructive commands; approving A and S promotions |

## Commands

### Actor Commands

| Command | Permission | Description |
|---|---|---|
| `/profile <actor>` | Anyone | Shows total points, current rank, progress to next rank, and the most recent point entry. |
| `/history <actor> [see_full_history]` | Anyone | Shows the 5 most recent point awards and manual adjustments. Set `see_full_history: True` to download the full history as a text file. |

### Staff Commands

| Command | Permission | Description |
|---|---|---|
| `/adjustpoints <actor> <amount> <reason>` | Senior | Manually add or subtract points. A promotion request is triggered automatically if the new total qualifies. Use a negative amount to deduct. |
| `/pendingpromotions` | Senior | Lists all pending promotion requests with actor, target role, points, and a jump link to each message. |
| `/inactiveactors <days>` | Senior | Exports actors with no point activity in the last N days as a text file. |
| `/actorsbyrank <rank> <as_file>` | Senior | Lists all actors at a given rank (F–S) sorted by points. Set `as_file: True` for a text export. |
| `/refreshroles` | Senior | Applies the correct rank role to any actor whose Discord role is lower than their database points warrant. Does not demote. |
| `/seedactors <role>` | High Rank | Bulk-adds all non-bot members with a given Discord role to the database at rank F with 0 points, and assigns the `[F] Actor` Discord role to each. Skips members already in the database. |
| `/prunemissingactors [apply] [purge_roblox]` | High Rank | Finds actors in the database who have left the Discord server. Previews the list by default. Set `apply: True` to delete them. Add `purge_roblox: True` to also kick them from the Roblox group. |

### Roblox Commands

Require `ROBLOX_GROUP_ID`. Write operations (`apply: True`, accepting members) also require `ROBLOX_COOKIE`.

| Command | Permission | Description |
|---|---|---|
| `/prunerobloxmembers [apply]` | Senior | Finds Roblox group members whose username does not match any Discord server nickname. Previews by default; `apply: True` kicks them from the group. |
| `/acceptrobloxmembers <usernames>` | High Rank | Accepts pending Roblox group join requests by username. Accepts a comma- or space-separated list. |
| `/cleanserobloxgroupranks [apply]` | High Rank | Finds members who hold both Lore Actor and Map Bypass Rank simultaneously. Previews by default; `apply: True` removes Map Bypass Rank without touching their Lore Actor rank. |

## Architecture

```
lore-points-bot/
├── bot.py                  # Bot setup, event handlers, all slash commands and views
├── config/
│   └── roles.py            # Role names, rank thresholds, channel name constants
├── database/
│   └── db.py               # SQLite connection, schema initialisation, all DB queries
├── services/
│   ├── permissions.py      # Role-based permission checks
│   ├── points_parser.py    # Parses the structured points message format
│   ├── ranks.py            # Rank lookup and promotion eligibility logic
│   └── roblox.py           # Roblox API calls via aiohttp
└── .github/
    └── workflows/
        └── deploy.yml      # CD: git fetch + reset --hard + systemctl restart on push to main
```

The database uses SQLite in WAL mode with a write lock to prevent concurrent write conflicts. Business logic lives entirely in `services/` and `database/`; `bot.py` handles Discord interactions and delegates to them.

## Deployment

The bot is deployed on a self-hosted runner (Raspberry Pi) via GitHub Actions. Every push to `main` triggers:

1. `git fetch origin main && git reset --hard origin/main` on the server
2. `sudo systemctl restart lorebot`

The bot runs as a systemd service, which handles restarts on failure and automatic start on boot.

## Documentation

The documentation in this repository was generated with the assistance of a large language model (LLM). If you rely on the docs, please verify any implementation details against the source code. To report errors or suggest improvements, open an issue.
