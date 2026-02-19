"""
TML Primitives — the nine shapes everything else is made of.

Source: github.com/CobraChickenAI/themissinglayer

This is not a service. This is not a framework.
These are the primitives that define coherence — the minimum viable structure
required for intent to remain intelligible as complexity increases.

Nine primitives. No more.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─── Context ──────────────────────────────────────────────────────────────────
# Where things live. Ownership, meaning, and the unit of truth.


@dataclass
class Scope:
    """
    Bounded ownership context. The community itself.

    This is what the creator owns — not a Discord server, not a Substack,
    not a Google Chat space. Those are Views. This is the thing.
    Every other primitive declares its Scope.
    """
    id: UUID = field(default_factory=uuid4)
    name: str = ""
    slug: str = ""          # URL-safe canonical identifier
    owner_id: str = ""      # The creator's canonical identity
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class Domain:
    """
    Outcome-based accountability for a functional area.
    Owns Capabilities. e.g., "Conversations", "Membership", "Moderation".
    """
    id: UUID = field(default_factory=uuid4)
    scope_id: UUID = None
    name: str = ""
    outcome: str = ""       # What this domain is accountable for delivering


@dataclass
class Capability:
    """
    Atomic unit of value, defined by the outcome it delivers.
    Not a service. Not a function. The locus of logic.

    A Capability exists independently of whatever implements it.
    A service may implement a Capability, but the Capability exists
    even when implemented by a spreadsheet and a human process.
    """
    id: UUID = field(default_factory=uuid4)
    scope_id: UUID = None
    domain_id: UUID = None
    name: str = ""
    outcome: str = ""       # The outcome this Capability delivers


# ─── Control ──────────────────────────────────────────────────────────────────
# How capabilities are exposed, constrained, and enforced.


@dataclass
class View:
    """
    Filtered projection of capabilities for a specific surface.

    Discord is a View. Google Chat is a View. Substack is a View.
    The community is not any of these. The community is the Scope.
    If Discord shuts down, you lose a View. You do not lose the community.
    """
    id: UUID = field(default_factory=uuid4)
    scope_id: UUID = None
    name: str = ""
    platform: str = ""      # "discord", "google_chat", "substack", etc.
    config: dict = field(default_factory=dict)


@dataclass
class Archetype:
    """
    Caller role definition. Constrains what actions may be taken.
    Not a pattern. Not a template. A role.

    Callers include humans AND agents. A relay agent is an Archetype.
    """
    id: UUID = field(default_factory=uuid4)
    scope_id: UUID = None
    name: str = ""          # "creator", "member", "moderator", "relay_agent"


@dataclass
class Policy:
    """
    Enforced rules governing what an Archetype may do.
    Deny by default. Explicit allow required.
    Any explicit deny overrides any allow.
    """
    id: UUID = field(default_factory=uuid4)
    scope_id: UUID = None
    archetype_id: UUID = None
    capability_id: UUID = None
    allow: bool = False
    condition: Optional[str] = None     # Future: expression-based conditions


# ─── Interaction ──────────────────────────────────────────────────────────────
# How effects cross boundaries and persist.


@dataclass
class Connector:
    """
    Governed read pathway across scopes or domains.
    How voices come IN from a platform.

    An agent watching Discord for messages is acting as a Connector.
    """
    id: UUID = field(default_factory=uuid4)
    scope_id: UUID = None
    platform: str = ""
    config: dict = field(default_factory=dict)
    active: bool = True


@dataclass
class Binding:
    """
    Governed write link that commits effects.
    How messages go OUT to a platform.

    An agent posting a relayed message to Google Chat is acting as a Binding.
    """
    id: UUID = field(default_factory=uuid4)
    scope_id: UUID = None
    platform: str = ""
    config: dict = field(default_factory=dict)
    active: bool = True


@dataclass
class Provenance:
    """
    Immutable record of origin, change history, and ownership.
    Non-optional. Every significant action emits Provenance.

    This is what makes ownership real — not a claim, a record.
    """
    id: UUID = field(default_factory=uuid4)
    scope_id: UUID = None
    action: str = ""                        # "message.received", "member.joined", etc.
    source_platform: Optional[str] = None
    source_identity: Optional[str] = None   # Raw handle on the source platform
    subject_id: Optional[str] = None        # ID of the thing this record is about
    detail: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_utcnow)


# ─── Runtime Shapes ───────────────────────────────────────────────────────────
# Not primitives. These implement the primitives at runtime.


@dataclass
class Member:
    """
    A caller: a human or agent operating within a Scope with an Archetype.

    The identity stitching problem lives here:
    one person may be @cobraChicken on Discord AND james@company.com on Google Chat.
    canonical_identity is the anchor. platform_handles are the projections.
    """
    id: UUID = field(default_factory=uuid4)
    scope_id: UUID = None
    archetype_name: str = "member"
    display_name: str = ""
    canonical_identity: str = ""            # Primary auth identifier (email, OAuth sub)
    platform_handles: dict = field(default_factory=dict)  # {"discord": "user#1234"}
    is_agent: bool = False
    joined_at: datetime = field(default_factory=_utcnow)


@dataclass
class RelayMessage:
    """
    Canonical form of a message before it is projected onto a platform View.

    The community's version of the message. Platform-agnostic.
    Discord formats it one way. Google Chat formats it another.
    This is what it actually is, independent of either.
    """
    id: UUID = field(default_factory=uuid4)
    scope_id: UUID = None
    provenance_id: UUID = None              # Always required — where did this come from?
    content: str = ""
    source_platform: str = ""
    source_channel: str = ""
    source_message_id: str = ""             # Platform-native ID for deduplication
    author_handle: str = ""                 # Raw handle on source platform
    resolved_member_id: Optional[UUID] = None   # Matched community member, if known
    is_summary: bool = False                # True = digest of thread activity, not a relay
    relay_count: int = 0                    # How many platforms this has been sent to
    timestamp: datetime = field(default_factory=_utcnow)
