"""
Community registration surface.

"Come here. Register. This is mine. You trust me. Now go back to wherever you came from."

This is the Scope made physical — the one URL the creator owns.
Members register here once. Then they go back to Discord, Google Chat, wherever.
The community travels to them from here on out.
"""

import os
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from core import store

DB_PATH = Path(os.getenv("DB_PATH", "community.db"))

app = FastAPI(
    title="Community",
    description="The registration surface. Owned by the creator. Not by Discord. Not by anyone else.",
    version="0.1.0",
)


@app.on_event("startup")
async def startup():
    await store.init_db(DB_PATH)


# ─── Request schemas ──────────────────────────────────────────────────────────

class CreateCommunity(BaseModel):
    name: str
    slug: str           # e.g., "cobrachicken" — becomes the canonical identifier
    owner_email: str


class RegisterMember(BaseModel):
    scope_slug: str
    email: str
    display_name: str
    platform_handles: dict = {}
    # Self-declared handles by platform, e.g.:
    # {"discord": "username#1234", "google_chat": "james@company.com"}
    # We issue verification codes for each. Member confirms from the platform.


class VerifyHandle(BaseModel):
    member_id: str
    platform: str
    code: str           # The code they received via the platform agent


class RegisterBinding(BaseModel):
    """
    Tell the community that a platform is a Binding —
    a surface where relayed messages should appear.
    """
    scope_slug: str
    platform: str                   # "discord", "google_chat", etc.
    default_channel_id: str         # Where to post relays on this platform
    owner_email: str                # Must match scope owner to register


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.post("/communities", status_code=status.HTTP_201_CREATED)
async def create_community(body: CreateCommunity):
    """
    Set up the community. The creator claims their Scope.

    Run this once. The slug is your canonical identifier — it travels with you
    across every platform your community touches.
    """
    existing = await store.get_scope_by_slug(body.slug, DB_PATH)
    if existing:
        raise HTTPException(status_code=409, detail="Slug already taken.")

    scope = await store.create_scope(
        name=body.name,
        slug=body.slug,
        owner_id=body.owner_email,
        path=DB_PATH,
    )
    await store.emit_provenance(
        scope_id=scope.id,
        action="scope.created",
        source_identity=body.owner_email,
        detail={"name": body.name, "slug": body.slug},
        path=DB_PATH,
    )

    return {
        "id": str(scope.id),
        "slug": scope.slug,
        "name": scope.name,
        "message": f"Community '{scope.name}' created. Set COMMUNITY_SCOPE_ID={scope.id} in your .env.",
    }


@app.get("/communities/{slug}")
async def get_community(slug: str):
    scope = await store.get_scope_by_slug(slug, DB_PATH)
    if not scope:
        raise HTTPException(status_code=404, detail="Community not found.")
    return {"id": str(scope.id), "name": scope.name, "slug": scope.slug}


@app.post("/members", status_code=status.HTTP_201_CREATED)
async def register_member(body: RegisterMember):
    """
    Register a member.

    They bring their canonical identity (email) and optionally self-declare
    their platform handles. We issue a verification code for each handle —
    the member sends that code from within the platform to confirm ownership.

    They don't need to verify to join. Verification just stitches their
    platform identity to their community identity so the agent knows it's them.
    """
    scope = await store.get_scope_by_slug(body.scope_slug, DB_PATH)
    if not scope:
        raise HTTPException(status_code=404, detail="Community not found.")

    member, verification_codes = await store.create_member(
        scope_id=scope.id,
        display_name=body.display_name,
        canonical_identity=body.email,
        platform_handles=body.platform_handles,
        path=DB_PATH,
    )
    await store.emit_provenance(
        scope_id=scope.id,
        action="member.registered",
        source_identity=body.email,
        subject_id=str(member.id),
        detail={
            "display_name": body.display_name,
            "platforms_claimed": list(body.platform_handles.keys()),
        },
        path=DB_PATH,
    )

    response = {
        "id": str(member.id),
        "display_name": member.display_name,
        "community": body.scope_slug,
    }

    if verification_codes:
        instructions = {
            platform: f"On {platform.title()}, send this to the community bot: VERIFY {code}"
            for platform, code in verification_codes.items()
        }
        response["next_step"] = {
            "message": "Send your verification code from each platform to link your identity.",
            "instructions": instructions,
        }

    return response


@app.post("/members/verify")
async def verify_handle(body: VerifyHandle):
    """
    Confirm a platform identity claim.

    The member sends their verification code from within the platform.
    The agent receives it and calls this endpoint.
    Once verified, the agent knows exactly who they are when they speak.
    """
    verified = await store.verify_identity_claim(
        member_id=UUID(body.member_id),
        platform=body.platform,
        code=body.code,
        path=DB_PATH,
    )
    if not verified:
        raise HTTPException(status_code=400, detail="Invalid or already-used code.")

    return {"verified": True, "platform": body.platform}


@app.post("/bindings", status_code=status.HTTP_201_CREATED)
async def register_binding(body: RegisterBinding):
    """
    Register a platform as a Binding — a surface where relays get posted.

    When someone speaks on Discord, the relay goes to all active Bindings
    (except back to Discord). Add a Binding for each platform you're on.
    """
    scope = await store.get_scope_by_slug(body.scope_slug, DB_PATH)
    if not scope:
        raise HTTPException(status_code=404, detail="Community not found.")

    if scope.owner_id != body.owner_email:
        raise HTTPException(status_code=403, detail="Only the community owner can register bindings.")

    binding = await store.upsert_binding(
        scope_id=scope.id,
        platform=body.platform,
        config={"default_channel_id": body.default_channel_id},
        path=DB_PATH,
    )
    await store.emit_provenance(
        scope_id=scope.id,
        action="binding.registered",
        source_identity=body.owner_email,
        subject_id=str(binding.id),
        detail={"platform": body.platform, "channel": body.default_channel_id},
        path=DB_PATH,
    )

    return {
        "id": str(binding.id),
        "platform": body.platform,
        "channel": body.default_channel_id,
        "message": f"Messages from other platforms will now appear in {body.platform} channel {body.default_channel_id}.",
    }
