"""
Community — entrypoint.

Starts two things concurrently:
  1. The registration surface (FastAPI on PORT)
  2. The Discord relay agent

To add more platform agents (Google Chat, Substack, etc.):
  - Instantiate the agent
  - Register it in dispatch_registry with a send function
  - Add it to asyncio.gather()
"""

import asyncio
import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

load_dotenv()

from core.store import init_db
from surface.app import app as surface_app
from agents.discord_agent import DiscordAgent

DB_PATH = Path(os.getenv("DB_PATH", "community.db"))
PORT = int(os.getenv("PORT", "8000"))
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")


async def main():
    await init_db(DB_PATH)

    # Build dispatch registry.
    # When the Discord agent needs to relay to Google Chat, it calls
    # dispatch_registry["google_chat"](channel_id, content).
    # As you add platform agents, add their .post methods here.
    dispatch_registry = {
        # "google_chat": google_chat_agent.post,
    }

    discord_agent = DiscordAgent(dispatch_registry=dispatch_registry)

    surface_config = uvicorn.Config(
        surface_app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )
    surface_server = uvicorn.Server(surface_config)

    if not DISCORD_TOKEN:
        print("[main] DISCORD_BOT_TOKEN not set — running surface only")
        await surface_server.serve()
    else:
        await asyncio.gather(
            surface_server.serve(),
            discord_agent.start(DISCORD_TOKEN),
        )


if __name__ == "__main__":
    asyncio.run(main())
