"""Publish dependency-ordered text posts from posts/*.md to two Threads actors.

A post file is immutable publication intent. state.json is the delivery ledger.
The publisher claims each file in state.json and pushes that claim before the
external API call, so a crash can drop a post but cannot duplicate it.

Post format:
    ---
    actor: andy | agent
    reply_to: another-file-slug   # optional
    ---
    text up to 500 UTF-8 bytes
    ---
    optional next segment

Segments after the first reply to the previous segment. A dependent file replies
to the final segment of its parent file.
"""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

from threads_api import ThreadsError, publish_text


POSTS = "posts"
STATE = "state.json"
ACTORS = {
    "andy": ("ANDY_THREADS_USER_ID", "ANDY_THREADS_ACCESS_TOKEN"),
    "agent": ("AGENT_THREADS_USER_ID", "AGENT_THREADS_ACCESS_TOKEN"),
}


@dataclass(frozen=True)
class Post:
    slug: str
    actor: str
    reply_to: str | None
    segments: tuple[str, ...]


def _unquote(value: str) -> str:
    value = value.strip()
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value


def parse(path: str) -> Post:
    text = open(path, encoding="utf-8").read()
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
    segments = tuple(part.strip() for part in re.split(r"\n---\n", match.group(2)) if part.strip())
    if not segments:
        raise ValueError(f"{path}: post body is empty")
    for index, segment in enumerate(segments, 1):
        size = len(segment.encode("utf-8"))
        if size > 500:
            raise ValueError(f"{path}: segment {index} is {size} UTF-8 bytes; limit is 500")
    return Post(
        slug=os.path.splitext(os.path.basename(path))[0],
        actor=actor,
        reply_to=front.get("reply_to") or None,
        segments=segments,
    )


def _commit_push(message: str) -> None:
    subprocess.run(["git", "add", STATE], check=True, capture_output=True, text=True)
    commit = subprocess.run(["git", "commit", "-m", message], capture_output=True, text=True)
    if commit.returncode != 0:
        if "nothing to commit" in commit.stdout + commit.stderr:
            return
        raise RuntimeError(f"git commit failed: {commit.stderr.strip()}")
    subprocess.run(["git", "push"], check=True, capture_output=True, text=True)


def _save(state: dict, slug: str, record: dict, message: str, push: bool) -> None:
    state[slug] = record
    with open(STATE, "w", encoding="utf-8") as output:
        json.dump(state, output, indent=2, ensure_ascii=False, sort_keys=True)
        output.write("\n")
    if push:
        _commit_push(message)


def _credentials(actor: str) -> tuple[str, str]:
    user_env, token_env = ACTORS[actor]
    user_id = (os.environ.get(user_env) or "").strip()
    token = (os.environ.get(token_env) or "").strip()
    if not user_id or not token:
        raise RuntimeError(f"{user_env} and {token_env} are required for actor {actor}")
    return user_id, token


def _parent_id(post: Post, state: dict) -> str | None:
    if not post.reply_to:
        return None
    parent = state.get(post.reply_to) or {}
    if parent.get("status") != "published" or not parent.get("last_media_id"):
        return None
    return parent["last_media_id"]


def _publish(post: Post, state: dict, push: bool) -> bool:
    parent_id = _parent_id(post, state)
    if post.reply_to and not parent_id:
        return False
    _save(state, post.slug,
          {"status": "sending", "actor": post.actor, "reply_to": post.reply_to},
          f"claim {post.slug} [skip ci]", push)
    try:
        user_id, token = _credentials(post.actor)
        ids: list[str] = []
        urls: list[str] = []
        reply_to_id = parent_id
        for segment in post.segments:
            result = publish_text(user_id, token, segment, reply_to_id)
            reply_to_id = result["media_id"]
            ids.append(reply_to_id)
            if result.get("url"):
                urls.append(result["url"])
        record = {
            "status": "published",
            "actor": post.actor,
            "reply_to": post.reply_to,
            "media_ids": ids,
            "last_media_id": ids[-1],
            "url": urls[0] if urls else None,
        }
        _save(state, post.slug, record, f"published {post.slug} [skip ci]", push)
        print(f"posted {post.slug} ({post.actor}) -> {record['url'] or ids[0]}")
    except (ThreadsError, RuntimeError) as error:
        print(f"FAILED {post.slug}: {error}", file=sys.stderr)
        _save(state, post.slug,
              {"status": "failed", "actor": post.actor,
               "reply_to": post.reply_to, "error": str(error)[:500]},
              f"failed {post.slug} [skip ci]", push)
        raise
    return True


def main() -> int:
    dry = "--dry-run" in sys.argv[1:]
    push = "--no-push" not in sys.argv[1:]
    state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    posts = [parse(path) for path in sorted(glob.glob(os.path.join(POSTS, "*.md")))]
    pending = {post.slug: post for post in posts if post.slug not in state}

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
            if post.reply_to and not _parent_id(post, state):
                continue
            try:
                _publish(post, state, push)
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
