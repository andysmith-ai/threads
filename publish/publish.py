"""Publish dependency-ordered text posts from posts/*.md to two Threads actors.

A post file is immutable publication intent. state/<slug>.json is its delivery
record. The publisher claims each file in its sidecar and pushes that claim
before the external API call, so a crash can drop a post but cannot duplicate it.

Post format:
    ---
    actor: andy | agent
    reply_to: another-file-slug   # optional local parent
    reply_to_id: 123456789        # optional external parent
    reply_to_url: https://...     # optional external provenance
    ---
    text up to 500 characters
    ---
    optional next segment

Use either reply_to or reply_to_id, never both. An external parent does not need
a file in this repository. Segments after the first reply to the previous segment.
"""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

from threads_api import ThreadsError, current_user_id, publish_text


POSTS = "posts"
STATE_DIR = "state"
PUSH_ATTEMPTS = 3
ACTORS = {
    "andy": ("ANDY_THREADS_USER_ID", "ANDY_THREADS_ACCESS_TOKEN"),
    "agent": ("AGENT_THREADS_USER_ID", "AGENT_THREADS_ACCESS_TOKEN"),
}


class StatePushError(RuntimeError):
    """A delivery sidecar commit could not be safely pushed."""


@dataclass(frozen=True)
class Post:
    slug: str
    actor: str
    reply_to: str | None
    reply_to_id: str | None
    reply_to_url: str | None
    segments: tuple[str, ...]


def _unquote(value: str) -> str:
    value = value.strip()
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value


def parse(path: str) -> Post:
    with open(path, encoding="utf-8") as source:
        text = source.read()
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not match:
        raise ValueError(f"{path}: missing front matter")
    front = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            key, value = line.split(":", 1)
            front[key.strip()] = _unquote(value)
    actor = front.get("actor", "")
    if actor not in ACTORS:
        raise ValueError(f"{path}: actor must be one of {', '.join(ACTORS)}")
    if front.get("reply_to") and front.get("reply_to_id"):
        raise ValueError(f"{path}: use reply_to or reply_to_id, not both")
    if front.get("reply_to_url") and not front.get("reply_to_id"):
        raise ValueError(f"{path}: reply_to_url requires reply_to_id")
    segments = tuple(part.strip() for part in re.split(r"\n---\n", match.group(2)) if part.strip())
    if not segments:
        raise ValueError(f"{path}: post body is empty")
    for index, segment in enumerate(segments, 1):
        size = len(segment)
        if size > 500:
            raise ValueError(f"{path}: segment {index} is {size} characters; limit is 500")
    return Post(
        slug=os.path.splitext(os.path.basename(path))[0],
        actor=actor,
        reply_to=front.get("reply_to") or None,
        reply_to_id=front.get("reply_to_id") or None,
        reply_to_url=front.get("reply_to_url") or None,
        segments=segments,
    )


def _state_path(slug: str) -> str:
    if not slug or os.path.basename(slug) != slug or slug in {".", ".."}:
        raise ValueError(f"invalid post slug: {slug!r}")
    return os.path.join(STATE_DIR, f"{slug}.json")


def _load_record(slug: str) -> dict | None:
    path = _state_path(slug)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as source:
        return json.load(source)


def _load_state() -> dict[str, dict]:
    state = {}
    for path in sorted(glob.glob(os.path.join(STATE_DIR, "*.json"))):
        slug = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as source:
            state[slug] = json.load(source)
    return state


def _commit_push(path: str, message: str) -> None:
    subprocess.run(["git", "add", "--", path], check=True, capture_output=True, text=True)
    commit = subprocess.run(
        ["git", "commit", "-m", message, "--only", "--", path],
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0:
        if "nothing to commit" in commit.stdout + commit.stderr:
            return
        raise StatePushError(f"git commit failed: {commit.stderr.strip()}")

    for attempt in range(PUSH_ATTEMPTS):
        pushed = subprocess.run(["git", "push"], capture_output=True, text=True)
        if pushed.returncode == 0:
            return
        if attempt == PUSH_ATTEMPTS - 1:
            raise StatePushError(
                f"git push failed after {PUSH_ATTEMPTS} attempts: {pushed.stderr.strip()}"
            )

        fetched = subprocess.run(["git", "fetch", "origin"], capture_output=True, text=True)
        if fetched.returncode != 0:
            raise StatePushError(f"git fetch failed: {fetched.stderr.strip()}")
        rebased = subprocess.run(["git", "rebase", "@{upstream}"], capture_output=True, text=True)
        if rebased.returncode != 0:
            detail = rebased.stderr.strip() or rebased.stdout.strip()
            subprocess.run(["git", "rebase", "--abort"], capture_output=True, text=True)
            raise StatePushError(
                f"git rebase failed; state conflict requires manual resolution: {detail}"
            )
        sidecar_changed = subprocess.run(
            ["git", "diff", "--quiet", "@{upstream}...HEAD", "--", path],
            capture_output=True,
            text=True,
        )
        if sidecar_changed.returncode == 0:
            raise StatePushError(
                f"{path} already changed upstream; refusing a concurrent publication"
            )
        if sidecar_changed.returncode != 1:
            raise StatePushError(
                f"git diff failed while checking {path}: {sidecar_changed.stderr.strip()}"
            )


def _save(slug: str, record: dict, message: str, push: bool) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    path = _state_path(slug)
    with open(path, "w", encoding="utf-8") as output:
        json.dump(record, output, indent=2, ensure_ascii=False, sort_keys=True)
        output.write("\n")
    if push:
        _commit_push(path, message)


def _credentials(actor: str) -> tuple[str, str]:
    user_env, token_env = ACTORS[actor]
    token = (os.environ.get(token_env) or "").strip()
    if not token:
        raise RuntimeError(f"{token_env} is required for actor {actor}")
    user_id = (os.environ.get(user_env) or "").strip() or current_user_id(token)
    return user_id, token


def _parent_id(post: Post) -> str | None:
    if post.reply_to_id:
        return post.reply_to_id
    if not post.reply_to:
        return None
    parent = _load_record(post.reply_to) or {}
    if parent.get("status") != "published" or not parent.get("last_media_id"):
        return None
    return parent["last_media_id"]


def _publish(post: Post, push: bool, open_replies: bool) -> bool:
    parent_id = _parent_id(post)
    if post.reply_to and not parent_id:
        return False
    claim = {"status": "sending", "actor": post.actor,
             "reply_to": post.reply_to, "reply_to_id": post.reply_to_id,
             "reply_to_url": post.reply_to_url}
    _save(post.slug, claim, f"chore(state): claim {post.slug} [skip ci]", push)
    try:
        user_id, token = _credentials(post.actor)
        ids: list[str] = []
        urls: list[str] = []
        reply_to_id = parent_id
        for index, segment in enumerate(post.segments):
            reply_control = "everyone" if open_replies and index == len(post.segments) - 1 else None
            result = publish_text(user_id, token, segment, reply_to_id, reply_control)
            reply_to_id = result["media_id"]
            ids.append(reply_to_id)
            if result.get("url"):
                urls.append(result["url"])
        record = {
            "status": "published",
            "actor": post.actor,
            "reply_to": post.reply_to,
            "reply_to_id": post.reply_to_id,
            "reply_to_url": post.reply_to_url,
            "media_ids": ids,
            "last_media_id": ids[-1],
            "url": urls[0] if urls else None,
        }
        _save(post.slug, record, f"chore(state): record {post.slug} publication [skip ci]", push)
        print(f"posted {post.slug} ({post.actor}) -> {record['url'] or ids[0]}")
    except StatePushError:
        raise
    except (ThreadsError, RuntimeError) as error:
        print(f"FAILED {post.slug}: {error}", file=sys.stderr)
        failed = {"status": "failed", "actor": post.actor,
                  "reply_to": post.reply_to, "reply_to_id": post.reply_to_id,
                  "reply_to_url": post.reply_to_url, "error": str(error)[:500]}
        _save(post.slug, failed, f"chore(state): record {post.slug} failure [skip ci]", push)
        raise
    return True


def main() -> int:
    dry = "--dry-run" in sys.argv[1:]
    push = "--no-push" not in sys.argv[1:]
    state = _load_state()
    posts = [parse(path) for path in sorted(glob.glob(os.path.join(POSTS, "*.md")))]
    pending = {post.slug: post for post in posts if post.slug not in state}
    reply_targets = {post.reply_to for post in posts if post.reply_to}

    if dry:
        simulated = dict(state)
        while pending:
            ready = [
                post for post in pending.values()
                if not post.reply_to
                or (simulated.get(post.reply_to) or {}).get("status") == "published"
            ]
            if not ready:
                unresolved = ", ".join(f"{p.slug}->{p.reply_to}" for p in pending.values())
                raise RuntimeError(f"unresolved reply dependencies: {unresolved}")
            for post in ready:
                print(f"[dry-run] {post.slug}: actor={post.actor}, "
                      f"reply_to={post.reply_to or '-'}, segments={len(post.segments)}")
                simulated[post.slug] = {"status": "published", "last_media_id": "dry-run"}
                del pending[post.slug]
        return 0

    failed = 0
    while pending:
        progressed = False
        for post in list(pending.values()):
            if post.reply_to and not _parent_id(post):
                continue
            try:
                _publish(post, push, post.slug in reply_targets)
            except (ThreadsError, RuntimeError):
                failed += 1
            del pending[post.slug]
            progressed = True
        if not progressed:
            unresolved = ", ".join(f"{p.slug}->{p.reply_to}" for p in pending.values())
            print(f"unresolved reply dependencies: {unresolved}", file=sys.stderr)
            return 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
