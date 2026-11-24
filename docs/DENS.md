# Dens — encrypted peer-to-peer workspaces (v3.6.0)

A **den** connects two or more DreadFox installs into a shared, end-to-end encrypted workspace. There is **no server**: each FOXWORX runs its own private Tor onion service, members connect directly to each other over Tor, and every member verifies every other member by key. The group can grow: any member can invite (unless the founder limits invites to admins), and members who were offline catch up from whoever is online.

Open the **Dens** tab in FOXWORX.

## What a den gives you

| Section | What it does |
|---|---|
| **Chat** | Group chat between the human operators. |
| **Workspace** | Shared notes, findings (with severity) and **command proposals** for humans to review. |
| **Files** | Encrypted file sharing (up to 10 MB each), checked with SHA-256 on download. |
| **Agents** | Light, capped help between your DreadFox agents (see below). |
| **Members** | Who's in, who's online, their key fingerprint, and **safety words** to verify each other. |

## Getting started

1. Install `tor` (the FoxOS kit does this; otherwise `sudo apt install tor`). FOXWORX starts its own Tor process as your user. It doesn't use or change the system Tor service.
2. **New den** → name it → **Invite** → send the one-time code to a friend privately (Signal, in person…). A code works once and expires after 24 hours.
3. Your friend clicks **Join with code** and pastes it. Your FoxWorx must be running while they join (over Tor it takes a few seconds to a minute).
4. Open **Members** and read your **safety words** to each other on a call. If both screens show the same six words, nobody is impersonating either of you.

Your node's name in dens is set with **Rename** under *This DreadFox*. Your **ID** is a fingerprint of your node key.

## How it's secured

- **Identity:** each install has an Ed25519 signing key and an X25519 key, stored in `<state>/den/identity.json` (0600). The ID shown in the UI is a fingerprint of the signing key.
- **Membership is a signature chain:** the founder signs the den's genesis; each member was added by a signed entry from an existing member. Any peer can check the whole chain; there's no authority to trust.
- **Every entry is signed and hash-chained** per author, so relaying members can't forge, alter, reorder or drop entries from the middle.
- **Content is end-to-end encrypted** (ChaCha20-Poly1305) under the den key. The den key reaches each member sealed to their X25519 key. Tor additionally encrypts and authenticates every connection; an onion address *is* that node's public key.
- **Removing a member** (admins) rotates the den key, so they can't read anything posted afterwards. Their later posts are rejected even if backdated, because the removal records the last post of theirs that counts.
- **The onion service only reaches the den listener** (127.0.0.1:8766), never the workbench web UI. That listener speaks only the den protocol and requires a signed handshake.
- All cryptography comes from the `cryptography` package already used by FOXWORX (no custom primitives).

## The rules for partner agents

Partners pay for their own AI, so agent collaboration is deliberately small:

- A request is either **Recall** (look it up in the den's shared notes and findings: **no AI, no tokens**) or **one short question** (≤ 500 characters).
- Requests land in the recipient's **inbox**. Nothing is answered automatically unless the recipient turns on auto-answers for that den:
  - *Off* (default): everything waits for the human.
  - *Recall from shared notes only*: answered automatically from the den workspace, zero tokens.
  - *Recall + short questions with my AI*: short questions are answered by **your local model** (LM Studio, free).
- **Cloud models are off for partners** unless you tick *Allow my cloud AI*, and then each answer is capped (50–600 tokens, default 300).
- A **daily limit per partner** (default 10 model answers) stops anyone from running up your usage. Senders are limited to 25 requests a day per den.
- Answers never use tools, and the model is told the question is untrusted text.
- When drafting by hand you can pull from shared notes (free), your **Zenko memory** (local, reviewed by you before sending), or your AI.

**Nothing from a den ever runs on your machine.** A command proposal is text; *Review in my Copilot* puts it in your Copilot box, where your own scope, guardrails and approval still apply.

## Settings (`settings.env`)

| Key | Default | Meaning |
|---|---|---|
| `FOXWORX_DEN_ENABLED` | `1` | Turn dens off with `0`. |
| `FOXWORX_DEN_TOR` | `1` | Use the private Tor onion service. |
| `FOXWORX_DEN_LISTEN_PORT` | `8766` | Den listener on 127.0.0.1 (the onion forwards here). |
| `FOXWORX_DEN_SOCKS_PORT` | `19050` | SOCKS port of FOXWORX's own Tor (doesn't clash with system Tor on 9050). |
| `FOXWORX_DEN_LABEL` | `DreadFox` | Initial node name shown to other members. |
| `FOXWORX_DEN_ADDRESS` | — | Testing/LAN only: `direct:host:port` instead of Tor. |

## Limits and trade-offs

- Tor adds a little latency (chat arrives in well under a second once connected) and files move at roughly 0.2–2 MB/s.
- Some networks block Tor. Those members can't connect until they're on another network (or use Tor bridges in a future version).
- An invite only works while the inviter's FOXWORX is online.
- The founder can't leave; admins can remove others. Leaving deletes the den's data from your machine.
- A den is not an authorization grant. Every member tests only their own authorized scope; shared findings and proposals are reference material.
