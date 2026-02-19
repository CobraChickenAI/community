"""
Store — SQLite-backed persistence for community primitives.

Not the architecture. The architecture is in primitives.py.
This is the thing that makes it persist between restarts.

Replace with Firestore, Spanner, or whatever when you're ready for GCP.
The interface stays the same. The store is just a store.
"""

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

import aiosqlite

from .primitives import (
    Scope, Member, Connector, Binding, RelayMessage, Provenance
)

DB_PATH = Path(os.getenv("DB_PATH", "community.db"))


# ─── Schema ───────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scopes (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT UNIQUE NOT NULL,
    owner_id    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
    id                  TEXT PRIMARY KEY,
    scope_id            TEXT NOT NULL,
    archetype_name      TEXT NOT NULL DEFAULT 'member',
    display_name        TEXT NOT NULL,
    canonical_identity  TEXT NOT NULL,
    platform_handles    TEXT NOT NULL DEFAULT '{}',
    is_agent            INTEGER NOT NULL DEFAULT 0,
    joined_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS identity_claims (
    member_id           TEXT NOT NULL,
    platform            TEXT NOT NULL,
    handle              TEXT NOT NULL,
    verification_code   TEXT,
    verified            INTEGER NOT NULL DEFAULT 0,
    claimed_at          TEXT NOT NULL,
    PRIMARY KEY (member_id, platform)
);

CREATE TABLE IF NOT EXISTS connectors (
    id          TEXT PRIMARY KEY,
    scope_id    TEXT NOT NULL,
    platform    TEXT NOT NULL,
    config      TEXT NOT NULL DEFAULT '{}',
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS bindings (
    id          TEXT PRIMARY KEY,
    scope_id    TEXT NOT NULL,
    platform    TEXT NOT NULL,
    config      TEXT NOT NULL DEFAULT '{}',
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS relay_messages (
    id                  TEXT PRIMARY KEY,
    scope_id            TEXT NOT NULL,
    provenance_id       TEXT NOT NULL,
    content             TEXT NOT NULL,
    source_platform     TEXT NOT NULL,
    source_channel      TEXT NOT NULL,
    source_message_id   TEXT NOT NULL,
    author_handle       TEXT NOT NULL,
    resolved_member_id  TEXT,
    is_summary          INTEGER NOT NULL DEFAULT 0,
    relay_count         INTEGER NOT NULL DEFAULT 0,
    timestamp           TEXT NOT NULL,
    UNIQUE (source_platform, source_message_id)
);

CREATE TABLE IF NOT EXISTS provenance (
    id                  TEXT PRIMARY KEY,
    scope_id            TEXT NOT NULL,
    action              TEXT NOT NULL,
    source_platform     TEXT,
    source_identity     TEXT,
    subject_id          TEXT,
    detail              TEXT NOT NULL DEFAULT '{}',
    timestamp           TEXT NOT NULL
);
"""


async def init_db(path: Path = DB_PATH) -> None:
    async with aiosqlite.connect(path) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Scope ────────────────────────────────────────────────────────────────────

async def create_scope(name: str, slug: str, owner_id: str, path: Path = DB_PATH) -> Scope:
    scope = Scope(name=name, slug=slug, owner_id=owner_id)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO scopes (id, name, slug, owner_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(scope.id), scope.name, scope.slug, scope.owner_id, scope.created_at.isoformat())
        )
        await db.commit()
    return scope


async def get_scope_by_slug(slug: str, path: Path = DB_PATH) -> Optional[Scope]:
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM scopes WHERE slug = ?", (slug,)) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return Scope(
        id=UUID(row["id"]),
        name=row["name"],
        slug=row["slug"],
        owner_id=row["owner_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


async def get_scope_by_id(scope_id: UUID, path: Path = DB_PATH) -> Optional[Scope]:
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM scopes WHERE id = ?", (str(scope_id),)) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return Scope(
        id=UUID(row["id"]),
        name=row["name"],
        slug=row["slug"],
        owner_id=row["owner_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Member ───────────────────────────────────────────────────────────────────

async def create_member(
    scope_id: UUID,
    display_name: str,
    canonical_identity: str,
    platform_handles: dict = None,
    is_agent: bool = False,
    path: Path = DB_PATH,
) -> tuple[Member, dict]:
    """
    Create a member and issue verification codes for each claimed platform handle.
    Returns (member, {platform: code}) — the codes get sent to the member
    so they can prove ownership of each handle from within that platform.
    """
    member = Member(
        scope_id=scope_id,
        display_name=display_name,
        canonical_identity=canonical_identity,
        platform_handles=platform_handles or {},
        is_agent=is_agent,
    )
    verification_codes: dict[str, str] = {}

    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT INTO members
               (id, scope_id, archetype_name, display_name, canonical_identity,
                platform_handles, is_agent, joined_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(member.id), str(member.scope_id), member.archetype_name,
                member.display_name, member.canonical_identity,
                json.dumps(member.platform_handles), int(member.is_agent),
                member.joined_at.isoformat(),
            )
        )

        for platform, handle in (platform_handles or {}).items():
            code = secrets.token_hex(4).upper()     # e.g., "A3F9"
            verification_codes[platform] = code
            await db.execute(
                """INSERT OR REPLACE INTO identity_claims
                   (member_id, platform, handle, verification_code, verified, claimed_at)
                   VALUES (?, ?, ?, ?, 0, ?)""",
                (str(member.id), platform, handle, code, _now())
            )

        await db.commit()

    return member, verification_codes


async def get_member_by_handle(
    platform: str,
    handle: str,
    scope_id: UUID,
    path: Path = DB_PATH,
) -> Optional[Member]:
    """
    Resolve a platform handle to a community member.
    Checks identity_claims (verified or self-declared).
    Best-effort — doesn't block relay if no match.
    """
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT m.* FROM members m
               JOIN identity_claims ic ON ic.member_id = m.id
               WHERE ic.platform = ? AND ic.handle = ? AND m.scope_id = ?""",
            (platform, handle, str(scope_id))
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return _row_to_member(row)


async def verify_identity_claim(
    member_id: UUID,
    platform: str,
    code: str,
    path: Path = DB_PATH,
) -> bool:
    """
    Check and mark a verification code as used.
    Called when the agent receives the code from the platform.
    """
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM identity_claims
               WHERE member_id = ? AND platform = ? AND verification_code = ? AND verified = 0""",
            (str(member_id), platform, code.upper())
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return False
        await db.execute(
            "UPDATE identity_claims SET verified = 1 WHERE member_id = ? AND platform = ?",
            (str(member_id), platform)
        )
        await db.commit()
    return True


def _row_to_member(row) -> Member:
    return Member(
        id=UUID(row["id"]),
        scope_id=UUID(row["scope_id"]),
        archetype_name=row["archetype_name"],
        display_name=row["display_name"],
        canonical_identity=row["canonical_identity"],
        platform_handles=json.loads(row["platform_handles"]),
        is_agent=bool(row["is_agent"]),
        joined_at=datetime.fromisoformat(row["joined_at"]),
    )


# ─── Connectors & Bindings ────────────────────────────────────────────────────

async def upsert_connector(
    scope_id: UUID,
    platform: str,
    config: dict = None,
    path: Path = DB_PATH,
) -> Connector:
    connector = Connector(scope_id=scope_id, platform=platform, config=config or {})
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT OR REPLACE INTO connectors (id, scope_id, platform, config, active)
               VALUES (?, ?, ?, ?, 1)""",
            (str(connector.id), str(scope_id), platform, json.dumps(connector.config))
        )
        await db.commit()
    return connector


async def upsert_binding(
    scope_id: UUID,
    platform: str,
    config: dict = None,
    path: Path = DB_PATH,
) -> Binding:
    """
    Register a platform as a Binding for this Scope.
    config must include 'default_channel_id' — where relays get posted.
    """
    binding = Binding(scope_id=scope_id, platform=platform, config=config or {})
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT OR REPLACE INTO bindings (id, scope_id, platform, config, active)
               VALUES (?, ?, ?, ?, 1)""",
            (str(binding.id), str(scope_id), platform, json.dumps(binding.config))
        )
        await db.commit()
    return binding


async def get_active_bindings(scope_id: UUID, path: Path = DB_PATH) -> list[Binding]:
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM bindings WHERE scope_id = ? AND active = 1",
            (str(scope_id),)
        ) as cur:
            rows = await cur.fetchall()
    return [
        Binding(
            id=UUID(r["id"]),
            scope_id=UUID(r["scope_id"]),
            platform=r["platform"],
            config=json.loads(r["config"]),
            active=bool(r["active"]),
        )
        for r in rows
    ]


# ─── Relay Messages ───────────────────────────────────────────────────────────

async def save_relay_message(msg: RelayMessage, path: Path = DB_PATH) -> RelayMessage:
    async with aiosqlite.connect(path) as db:
        try:
            await db.execute(
                """INSERT INTO relay_messages
                   (id, scope_id, provenance_id, content, source_platform, source_channel,
                    source_message_id, author_handle, resolved_member_id, is_summary,
                    relay_count, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(msg.id), str(msg.scope_id), str(msg.provenance_id),
                    msg.content, msg.source_platform, msg.source_channel,
                    msg.source_message_id, msg.author_handle,
                    str(msg.resolved_member_id) if msg.resolved_member_id else None,
                    int(msg.is_summary), msg.relay_count, msg.timestamp.isoformat(),
                )
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            pass    # Already seen this source_message_id — skip
    return msg


# ─── Provenance ───────────────────────────────────────────────────────────────

async def emit_provenance(
    scope_id: UUID,
    action: str,
    source_platform: str = None,
    source_identity: str = None,
    subject_id: str = None,
    detail: dict = None,
    path: Path = DB_PATH,
) -> Provenance:
    """Emit a provenance record. Non-optional for significant actions."""
    prov = Provenance(
        scope_id=scope_id,
        action=action,
        source_platform=source_platform,
        source_identity=source_identity,
        subject_id=subject_id,
        detail=detail or {},
    )
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT INTO provenance
               (id, scope_id, action, source_platform, source_identity, subject_id, detail, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(prov.id), str(prov.scope_id), prov.action,
                prov.source_platform, prov.source_identity, prov.subject_id,
                json.dumps(prov.detail), prov.timestamp.isoformat(),
            )
        )
        await db.commit()
    return prov
