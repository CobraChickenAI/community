"""
Relay logic — the agent's working process.

This is where "agent judgment" gets encoded.

Three questions the relay answers every time:
  1. Is this worth crossing the boundary? (filter)
  2. Who said it, in community terms? (resolve)
  3. Where does it go, and in what form? (dispatch)

Tune the filter. Remix the format. The structure stays the same.
"""

import re
from typing import Callable, Awaitable
from uuid import UUID

from core import store
from core.primitives import RelayMessage


# ─── Filter ───────────────────────────────────────────────────────────────────
# Not every message deserves to cross a platform boundary.
# These are defaults. Encode your own judgment here.

MIN_RELAY_LENGTH = 40
# Messages shorter than this are likely reactions, acks, or noise.
# 40 chars is roughly "I think this is worth talking about." — a complete thought.

_EMOJI_ONLY = re.compile(
    r"^[\U0001F300-\U0001F9FF\U0001FA00-\U0001FA9F"
    r"\u2600-\u26FF\u2700-\u27BF\s]+$"
)

_RELAY_PREFIX = "📡"    # Our attribution marker — prevents relay loops


def should_relay(content: str) -> bool:
    """
    Return True if this message is worth carrying across platforms.

    Default rules:
    - Long enough to be a thought (not a reaction or one-word reply)
    - Not emoji-only
    - Not already a relay (prevents loops back through the same agent)

    This is the judgment call. Adjust MIN_RELAY_LENGTH to taste.
    """
    content = content.strip()

    if len(content) < MIN_RELAY_LENGTH:
        return False

    if _EMOJI_ONLY.match(content):
        return False

    if content.startswith(_RELAY_PREFIX):
        return False

    return True


# ─── Format ───────────────────────────────────────────────────────────────────

MAX_RELAY_CONTENT = 500     # Characters before we truncate with a note


def format_relay(content: str, author: str, source_platform: str) -> str:
    """
    Format a message for relay to another platform.
    Always includes attribution — the community sees who said it and where.

    Example output:
        📡 from Discord — @cobraChicken:
        > This is the thing I was saying about communities owning their own data.
    """
    if len(content) > MAX_RELAY_CONTENT:
        content = content[:MAX_RELAY_CONTENT - 3] + "..."

    lines = content.split("\n")
    quoted = "\n".join(f"> {line}" for line in lines)

    return f"{_RELAY_PREFIX} from {source_platform.title()} — @{author}:\n{quoted}"


def format_summary(messages: list[dict], source_platform: str, channel: str) -> str:
    """
    Format a thread activity summary.

    The agent's way of saying: "A lot happened over there.
    Here's the shape of it. If you want the full thing, go there."
    This fires when a relayed message generates SUMMARY_THRESHOLD replies
    on the source platform — not every reply gets re-relayed.
    """
    count = len(messages)
    authors = list({m["author"] for m in messages})

    if len(authors) == 1:
        author_str = f"@{authors[0]}"
    elif len(authors) <= 3:
        author_str = ", ".join(f"@{a}" for a in authors)
    else:
        author_str = f"@{authors[0]}, @{authors[1]}, and {len(authors) - 2} others"

    return (
        f"{_RELAY_PREFIX} {count} {'reply' if count == 1 else 'replies'} "
        f"on {source_platform.title()} #{channel} from {author_str}. "
        f"Head there for the full thread."
    )


# ─── Pipeline ─────────────────────────────────────────────────────────────────

DispatchFn = Callable[[str, str, str], Awaitable[None]]
# async (platform, channel_id, content) -> None


async def relay_message(
    scope_id: UUID,
    content: str,
    source_platform: str,
    source_channel: str,
    source_message_id: str,
    author_handle: str,
    dispatch: DispatchFn,
) -> RelayMessage | None:
    """
    Full relay pipeline for a single message.

    Steps:
    1. Filter — should this cross the boundary?
    2. Resolve — do we know who this person is in the community?
    3. Emit provenance — non-optional
    4. Save canonical form
    5. Get active bindings and dispatch

    Returns the saved RelayMessage, or None if filtered out.
    """

    # 1. Filter
    if not should_relay(content):
        return None

    # 2. Resolve member identity (best-effort — doesn't block relay if unknown)
    member = await store.get_member_by_handle(source_platform, author_handle, scope_id)
    resolved_id = member.id if member else None
    display_name = member.display_name if member else author_handle

    # 3. Provenance — record that we received this
    prov = await store.emit_provenance(
        scope_id=scope_id,
        action="message.received",
        source_platform=source_platform,
        source_identity=author_handle,
        subject_id=source_message_id,
        detail={
            "channel": source_channel,
            "content_length": len(content),
            "resolved_member": str(resolved_id) if resolved_id else None,
        },
    )

    # 4. Save canonical form
    msg = RelayMessage(
        scope_id=scope_id,
        provenance_id=prov.id,
        content=content,
        source_platform=source_platform,
        source_channel=source_channel,
        source_message_id=source_message_id,
        author_handle=author_handle,
        resolved_member_id=resolved_id,
    )
    msg = await store.save_relay_message(msg)

    # 5. Dispatch to all active bindings (skip source platform)
    bindings = await store.get_active_bindings(scope_id)
    relay_text = format_relay(content, display_name, source_platform)

    dispatched_to = []
    for binding in bindings:
        if binding.platform == source_platform:
            continue    # Don't echo back to where it came from

        channel_id = binding.config.get("default_channel_id")
        if not channel_id:
            continue

        try:
            await dispatch(binding.platform, channel_id, relay_text)
            dispatched_to.append(binding.platform)
        except Exception as e:
            # Log but don't fail the whole relay — one bad binding shouldn't block others
            print(f"[relay] dispatch to {binding.platform} failed: {e}")

    # Record that the relay happened
    if dispatched_to:
        await store.emit_provenance(
            scope_id=scope_id,
            action="message.relayed",
            source_platform=source_platform,
            source_identity=author_handle,
            subject_id=str(msg.id),
            detail={"dispatched_to": dispatched_to},
        )

    return msg


async def handle_verification(
    scope_id: UUID,
    platform: str,
    author_handle: str,
    content: str,
    surface_base_url: str,
) -> str | None:
    """
    Check if a message is a verification code submission.

    Members send "VERIFY <code>" to the bot from their platform
    after registering at the surface. We call the surface to confirm.
    Returns a response string to send back, or None if not a verify command.
    """
    import re
    import httpx

    match = re.match(r"^VERIFY\s+([A-F0-9]{8})\s*$", content.strip().upper())
    if not match:
        return None

    code = match.group(1)

    # Find the member who claimed this handle
    member = await store.get_member_by_handle(platform, author_handle, scope_id)
    if not member:
        return "I don't recognize this handle. Register at the community surface first."

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{surface_base_url}/members/verify",
            json={"member_id": str(member.id), "platform": platform, "code": code},
        )

    if resp.status_code == 200:
        return f"Verified! You're now linked as {member.display_name} in this community."
    else:
        return "That code didn't work. Double-check it or re-register at the surface."
