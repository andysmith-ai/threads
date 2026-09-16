"""Small stdlib client for text posts and replies through the Threads API."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request


GRAPH = "https://graph.threads.net/v1.0"


class ThreadsError(RuntimeError):
    def __init__(self, message: str, code: int | None = None,
                 subcode: int | None = None):
        super().__init__(message)
        self.code = code
        self.subcode = subcode


def _request(method: str, path: str, token: str, fields: dict[str, str]) -> dict:
    data = {**fields, "access_token": token}
    encoded = urllib.parse.urlencode(data).encode()
    url = GRAPH + path
    request = urllib.request.Request(
        url if method == "POST" else url + "?" + encoded.decode(),
        data=encoded if method == "POST" else None,
        method=method,
        headers={"User-Agent": "andysmith-ai-threads-publisher/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")[:1000]
        try:
            details = (json.loads(body).get("error") or {})
        except (json.JSONDecodeError, AttributeError):
            details = {}
        raise ThreadsError(
            f"Threads API {method} {path} failed ({error.code}): {body}",
            details.get("code"),
            details.get("error_subcode"),
        ) from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise ThreadsError(f"Threads API {method} {path} failed: {error}") from None


def _wait_until_ready(container_id: str, token: str) -> None:
    for _ in range(30):
        result = _request("GET", f"/{container_id}", token,
                          {"fields": "id,status,error_message"})
        status = result.get("status")
        if status in {None, "FINISHED", "PUBLISHED"}:
            return
        if status in {"ERROR", "EXPIRED"}:
            raise ThreadsError(
                f"Threads container {container_id} became {status}: "
                f"{result.get('error_message', '')}"
            )
        time.sleep(2)
    raise ThreadsError(f"Threads container {container_id} was not ready after 60 seconds")


def permalink(media_id: str, token: str) -> str | None:
    try:
        return _request("GET", f"/{media_id}", token, {"fields": "permalink"}).get("permalink")
    except ThreadsError:
        return None


def current_user_id(token: str) -> str:
    profile = _request("GET", "/me", token, {"fields": "id"})
    user_id = str(profile.get("id") or "").strip()
    if not user_id:
        raise ThreadsError(f"Threads profile returned no id: {profile}")
    return user_id


def publish_text(user_id: str, token: str, text: str,
                 reply_to_id: str | None = None,
                 reply_control: str | None = None) -> dict:
    """Create and publish one text post. Returns media_id and optional permalink."""
    form = {"media_type": "TEXT", "text": text}
    if reply_to_id:
        form["reply_to_id"] = reply_to_id
    if reply_control:
        form["reply_control"] = reply_control
    for attempt in range(7):
        try:
            created = _request("POST", f"/{user_id}/threads", token, form)
            break
        except ThreadsError as error:
            parent_not_visible = error.code == 24 and error.subcode == 4279009
            if not reply_to_id or not parent_not_visible or attempt == 6:
                raise
            time.sleep(10)
    container_id = created.get("id")
    if not container_id:
        raise ThreadsError(f"Threads create returned no id: {created}")
    _wait_until_ready(container_id, token)
    published = _request("POST", f"/{user_id}/threads_publish", token,
                         {"creation_id": container_id})
    media_id = published.get("id")
    if not media_id:
        raise ThreadsError(f"Threads publish returned no id: {published}")
    return {"media_id": media_id, "url": permalink(media_id, token)}
