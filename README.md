# Community

> "The community is the sum of all of those parts — the whole. The ownership must lie with the creator."

A community platform built on [The Missing Layer](https://github.com/CobraChickenAI/themissinglayer).

One registration surface. Many platforms. Voices move between them automatically. No one owns your community except you.

---

## How it works

**You own the community.** Members register once at your surface. Then they go back to Discord, Google Chat, wherever they already live. Agents watch those platforms and relay meaningful conversations across them — without anyone having to do anything.

**Discord is a View. Google Chat is a View. Substack is a View.** If any of them disappear, you lose a surface. You don't lose the community.

---

## Quickstart

### 1. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 2. Create your community

```bash
python main.py   # starts the surface on localhost:8000
```

```bash
curl -X POST http://localhost:8000/communities \
  -H "Content-Type: application/json" \
  -d '{"name": "My Community", "slug": "my-community", "owner_email": "you@example.com"}'
```

Copy the returned `id` into your `.env` as `COMMUNITY_SCOPE_ID`.

### 3. Set up Discord

1. Create a bot at [discord.com/developers](https://discord.com/developers/applications)
2. Under **Bot**, enable **Message Content Intent**
3. Copy the token into `.env` as `DISCORD_BOT_TOKEN`
4. Invite the bot to your server with `bot` and `applications.commands` scopes

### 4. Register a Binding (where relays appear)

Tell the community which Discord channel to post relays into:

```bash
curl -X POST http://localhost:8000/bindings \
  -H "Content-Type: application/json" \
  -d '{
    "scope_slug": "my-community",
    "platform": "discord",
    "default_channel_id": "YOUR_CHANNEL_ID",
    "owner_email": "you@example.com"
  }'
```

### 5. Register a member

```bash
curl -X POST http://localhost:8000/members \
  -H "Content-Type: application/json" \
  -d '{
    "scope_slug": "my-community",
    "email": "member@example.com",
    "display_name": "James",
    "platform_handles": {"discord": "james"}
  }'
```

The response includes a verification code. The member sends `VERIFY <code>` to the bot from Discord. Once verified, the agent knows it's them — their display name shows in relays instead of their raw handle.

### 6. Run

```bash
python main.py
```

---

## Architecture

Built on [TML's nine primitives](https://github.com/CobraChickenAI/themissinglayer):

| TML Primitive | Role in this system |
|---|---|
| **Scope** | The community itself — what the creator owns |
| **View** | Each platform surface (Discord, Google Chat, Substack) |
| **Connector** | The agent watching a platform for inbound messages |
| **Binding** | The agent posting relays to a platform |
| **Archetype** | Creator, Member, Relay Agent |
| **Provenance** | Every relay, every join, every verification — recorded |
| **Capability** | The outcomes: relay a message, register a member, verify an identity |

```
surface/app.py           — Registration surface (FastAPI)
core/primitives.py       — TML primitive dataclasses
core/store.py            — SQLite-backed persistence (swap for Firestore when ready)
agents/relay.py          — Filter → resolve → provenance → dispatch
agents/discord_agent.py  — Discord Connector + Binding
```

---

## Adding a platform

To add Google Chat (or anything else):

1. Create `agents/google_chat_agent.py` following the same pattern as `discord_agent.py`
2. Register it in `main.py`'s `dispatch_registry`
3. Call `POST /bindings` to tell the community where to post on that platform

The relay logic in `agents/relay.py` doesn't change. New agents just plug in.

---

## Relay filter

Not every message crosses a platform boundary. By default, a message relays if:
- It's at least 40 characters (a complete thought, not a reaction)
- It's not emoji-only
- It's not already a relay (no loops)

Edit `MIN_RELAY_LENGTH` in `agents/relay.py` to tune this.

---

## License

CC BY-SA 4.0 — same as The Missing Layer.
