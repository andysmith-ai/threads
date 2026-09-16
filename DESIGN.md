# Threads channel — design

## Boundary

This repository is the publication ledger for one platform and two actors:
`andy` (`andysmith.ai`) and `agent` (`agent.smith.ai`). One repository is
intentional: replies need the published media ID of their parent. Keeping both
actors in one dependency graph avoids polling or copying IDs between repositories.

Zeno produces immutable files. GitHub Actions owns the external Threads API calls.
Tokens never enter the Zeno research sandbox.

## Post contract

`posts/<stable-slug>.md`:

```text
---
actor: andy
reply_to: optional-parent-slug
---
First text segment, at most 500 UTF-8 bytes.
---
Optional second segment.
```

`actor` is `andy` or `agent`. `reply_to` names another file without `.md`.
Additional body segments form a chain: each replies to the preceding segment.
A dependent file replies to its parent's final segment.

Public-research slugs are deterministic from the Zulip message ID:
`research-<id>-question`, `research-<id>-progress`, and
`research-<id>-answer`. This makes a retried producer commit idempotent.

## Delivery

On a push touching `posts/**`, `publish/publish.py`:

1. parses and validates all unpublished files;
2. waits until each declared parent has a published `last_media_id`;
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

Variables:

- `ANDY_THREADS_USER_ID`
- `AGENT_THREADS_USER_ID`

Both tokens need `threads_basic` and `threads_content_publish`. The Andy post
must allow replies from the Agent account.

## Local validation

```sh
python publish/publish.py --dry-run
```

Dry-run validates files and the reply graph without credentials or API calls.
