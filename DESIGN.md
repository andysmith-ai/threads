# Threads channel — design

## Boundary

This repository is the publication ledger for one platform and two actors:
`andy` (`andysmith.ai`) and `agent` (`agent.smith.wiki`). One repository is
intentional: replies need the published media ID of their parent. Keeping both
actors in one dependency graph avoids polling or copying IDs between repositories.

Zeno produces immutable files. GitHub Actions owns the external Threads API calls.
Tokens never enter the Zeno research sandbox.

Inbound replies from other people are deliberately not mirrored into git. They
remain available through Threads; this repository records only publication
intent and results for our two accounts.

## Post contract

`posts/<stable-slug>.md`:

```text
---
actor: andy
reply_to: optional-local-parent-slug
---
First text segment, at most 500 UTF-8 bytes.
---
Optional second segment.
```

`actor` is `andy` or `agent`. `reply_to` names another file without `.md`.
Additional body segments form a chain: each replies to the preceding segment.
A dependent file replies to its local parent's final segment.

A reply to a post that is not stored here uses its Threads media ID directly:

```text
---
actor: andy
reply_to_id: "12345678901234567"
reply_to_url: "https://www.threads.net/@someone/post/ABC"
---
My reply.
```

`reply_to_url` is optional provenance for humans; the API uses `reply_to_id`.
Exactly one of `reply_to` and `reply_to_id` may be present. The foreign parent
does not need to be copied into this repository.

Public-research slugs are deterministic from the Zulip message ID:
`research-<id>-question` and `research-<id>-answer`. This makes a retried
producer commit idempotent.

## Delivery

On a push touching `posts/**`, `publish/publish.py`:

1. parses and validates all unpublished files;
2. resolves local parents through `state.json`; external `reply_to_id` values are
   already ready and require no stored parent;
3. writes and pushes a `sending` claim to `state.json`;
4. creates and publishes every segment through the selected actor;
5. writes the returned media IDs and permalink to `state.json`.

Claims happen before API calls. A crash can therefore drop a post but cannot
duplicate it. Delete a `failed` or `sending` state entry only after checking the
Threads account manually.

The adapter follows the official two-step API: create
`/{threads-user-id}/threads`, wait for the container, then call
`/{threads-user-id}/threads_publish`. Replies pass `reply_to_id`.

## Repository settings

Secrets:

- `ANDY_THREADS_ACCESS_TOKEN`
- `AGENT_THREADS_ACCESS_TOKEN`

The publisher resolves each Threads user ID through `/me` using its access token.
The following repository variables are optional explicit overrides:

- `ANDY_THREADS_USER_ID`
- `AGENT_THREADS_USER_ID`

Both accounts must be app roles/testers while the Meta app is in development.
Tokens need `threads_basic`, `threads_content_publish`,
`threads_manage_replies`, `threads_read_replies`, and
`threads_manage_mentions`. Read access makes Andy's reply visible to the Agent;
mention access allows the Agent to reply because Andy explicitly tagged it.
Long-lived tokens expire after 60 days and must be refreshed while still valid.
The Andy post must allow replies from the Agent account.

## Local validation

```sh
python publish/publish.py --dry-run
```

Dry-run validates files and the reply graph without credentials or API calls.
