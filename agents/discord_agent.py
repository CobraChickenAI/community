"""
Discord relay agent.

Acts as both Connector and Binding for Discord:
  Connector: watches configured channels for messages worth relaying
  Binding: receives relays from other platforms and posts them here

This is the first agent. Add more for Google Chat, Substack, etc.
Each new agent follows the same pattern — watch, filter (via relay.py), dispatch.
"""

import os
from uuid import UUID
from typing import Callable, Awaitable

import discord

from .relay import relay_message, handle_verification

SCOPE_ID = UUID(os.getenv("COMMUNITY_SCOPE_ID", "00000000-0000-0000-0000-000000000000"))
SURFACE_URL = os.getenv("SURFACE_URL", "http://localhost:8000")

# Comma-separated channel IDs to watch (as Connector)
# Leave empty to watch all channels the bot can see
_WATCH_ENV = os.getenv("DISCORD_WATCH_CHANNELS", "")
WATCH_CHANNEL_IDS: set[int] = {
    int(c.strip()) for c in _WATCH_ENV.split(",") if c.strip()
}


# DispatchFn signature: async (platform, channel_id, content) -> None
DispatchFn = Callable[[str, str, str], Awaitable[None]]


class DiscordAgent(discord.Client):
    """
    The Discord agent.

    On construction, receives a dispatch_registry — a dict of
    {platform: async_send_fn} for other platforms the community is connected to.
    When a message relays out, it calls the right function for each platform.

    To add Google Chat: add "google_chat": google_chat_agent.post to the registry.
    """

    def __init__(self, dispatch_registry: dict[str, DispatchFn] = None, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True     # Privileged — enable in Discord Developer Portal
        super().__init__(intents=intents, **kwargs)
        self._registry = dispatch_registry or {}

    async def on_ready(self):
        print(f"[discord] online as {self.user} | watching {len(WATCH_CHANNEL_IDS) or 'all'} channels")

    async def on_message(self, message: discord.Message):
        # Ignore our own messages and other bots
        if message.author.bot:
            return

        # Scope to watched channels if configured
        if WATCH_CHANNEL_IDS and message.channel.id not in WATCH_CHANNEL_IDS:
            return

        author_handle = message.author.name

        # Check for verification command first
        verify_response = await handle_verification(
            scope_id=SCOPE_ID,
            platform="discord",
            author_handle=author_handle,
            content=message.content,
            surface_base_url=SURFACE_URL,
        )
        if verify_response is not None:
            await message.reply(verify_response)
            return

        # Run the relay pipeline
        await relay_message(
            scope_id=SCOPE_ID,
            content=message.content,
            source_platform="discord",
            source_channel=str(message.channel.id),
            source_message_id=str(message.id),
            author_handle=author_handle,
            dispatch=self._dispatch,
        )

    async def _dispatch(self, platform: str, channel_id: str, content: str):
        """
        Route an outbound relay to the right platform.

        For Discord itself (inbound from another platform):
          post to the given channel_id.
        For other platforms:
          call the registered send function.
        """
        if platform == "discord":
            channel = self.get_channel(int(channel_id))
            if channel:
                await channel.send(content)
        elif platform in self._registry:
            await self._registry[platform](channel_id, content)
        else:
            print(f"[discord] no dispatch handler for platform: {platform}")

    async def post(self, channel_id: str, content: str):
        """
        Binding entry point — called by other platform agents to post here.
        Registered in the dispatch_registry of sibling agents.
        """
        channel = self.get_channel(int(channel_id))
        if channel:
            await channel.send(content)
        else:
            print(f"[discord] channel {channel_id} not found or not accessible")
