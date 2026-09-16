# Threads channel — design & handoff

## What this repo is
Post store + CI publisher for the **Threads** account. Same shape as
`andysmith-ai/telegram`: push a post into `posts/`, CI publishes the ones not yet
in `state.json`, oldest-first, commits state. **The file is the source of truth —
no content/image logic in CI.**

## KEY DIFFERENCE vs telegram
Telegram is 1:1 with the blog. **Threads is NOT** — it holds **its own,
audience-tuned posts** (short derivatives of the blog, written by a future
"content factory" agent, not a straight repost). So the producer and the post
format differ; only the reconcile+state+CI *machinery* is shared.

## Reuse the telegram template
Copy `andysmith-ai/telegram/publish/publish.py` + `.github/workflows/publish.yml`
+ `state.json` + `publish/seed.py` and swap the **adapter**:
- replace `telegram.py`/`richmessage.py` with a `threads_api.py` that talks to the
  **Threads API** (Meta Graph API).

## Threads API notes (for the adapter)
- Two-step publish: `POST /{user-id}/threads` (create a media container:
  `text`, `media_type=TEXT|IMAGE|VIDEO`, `image_url`/`video_url`) → returns a
  container id → `POST /{user-id}/threads_publish?creation_id=<id>`.
- **Auth**: a long-lived user access token (Threads/Instagram Graph). Store as repo
  secret `THREADS_ACCESS_TOKEN` (+ `THREADS_USER_ID` as a var).
- **Limit**: ~500 chars/post. A longer post = a **chain**: publish segment 1, then
  each next with `reply_to_id` = the previous post id.
- Media is by **public URL** (verbatim from the file — no minting here), like telegram.
- Permalink: the publish response returns the post id; build the public URL from it
  for the `state.json` record / the thread reply.

## Post contract (proposed — refine when building the producer)
```
posts/YYYY-MM-DD-<slug>.md
---
media: [https://...]      # optional, final public URLs, verbatim
---
<segment 1 text ≤ 500 chars>
---
<segment 2 …>             # optional; each `---`-separated block = one post in a chain
```

## TODO (next session)
- Build `publish/threads_api.py` (container-create + publish + chain) and a
  `publish/publish.py` mirroring telegram's reconcile loop.
- Decide the **content producer**: the content factory that turns a blog post into a
  Threads-native short post/chain (its own agent/prompt — different from the 1:1
  telegram repost).
- Repo secret `THREADS_ACCESS_TOKEN`, var `THREADS_USER_ID`.

See `andysmith-ai/telegram/DESIGN.md` for the shared reconcile/state/seed rationale.
