#!/usr/bin/env python3
"""Authorize one Threads actor and exchange its code for a long-lived token.

The Meta redirect lands on the repository's HTTPS GitHub Pages callback bridge,
which forwards the one-time authorization code to this process on 127.0.0.1.
The Threads App Secret and access tokens never enter the browser page or git.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


DEFAULT_REDIRECT_URI = "https://andysmith-ai.github.io/threads/"
LOCAL_PORT = 8787
AUTHORIZE_URL = "https://threads.com/oauth/authorize"
SHORT_TOKEN_URL = "https://graph.threads.com/oauth/access_token"
LONG_TOKEN_URL = "https://graph.threads.net/access_token"
SCOPES = "threads_basic,threads_content_publish,threads_manage_replies,threads_read_replies"


def authorization_url(app_id: str, redirect_uri: str, state: str) -> str:
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "response_type": "code",
        "state": state,
    })


def request_json(url: str, *, form: dict[str, str] | None = None,
                 query: dict[str, str] | None = None) -> dict:
    if query:
        url += "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(form).encode() if form else None,
        method="POST" if form else "GET",
        headers={"User-Agent": "andysmith-ai-threads-oauth/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"Threads OAuth failed ({error.code}): {body}") from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"Threads OAuth request failed: {error}") from None


def exchange_code(app_id: str, app_secret: str, redirect_uri: str,
                  code: str) -> dict:
    short = request_json(SHORT_TOKEN_URL, form={
        "client_id": app_id,
        "client_secret": app_secret,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code": code,
    })
    short_token = short.get("access_token")
    user_id = str(short.get("user_id") or "")
    if not short_token or not user_id:
        raise RuntimeError("Short-lived token response omitted access_token or user_id")
    long = request_json(LONG_TOKEN_URL, query={
        "grant_type": "th_exchange_token",
        "client_secret": app_secret,
        "access_token": short_token,
    })
    long_token = long.get("access_token")
    if not long_token:
        raise RuntimeError("Long-lived token response omitted access_token")
    return {
        "access_token": long_token,
        "user_id": user_id,
        "expires_in": int(long.get("expires_in") or 0),
    }


def receive_code(expected_state: str, port: int, timeout: int) -> str:
    outcome: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_error(404)
                return
            query = urllib.parse.parse_qs(parsed.query)
            if query.get("state", [""])[0] != expected_state:
                outcome["error"] = "OAuth state mismatch; authorization was rejected"
            elif query.get("error"):
                outcome["error"] = query.get("error_description", query["error"])[0]
            elif query.get("code"):
                outcome["code"] = query["code"][0]
            else:
                outcome["error"] = "Callback contained neither code nor error"
            ok = "code" in outcome
            body = ("Threads authorization received. You can close this tab."
                    if ok else f"Threads authorization failed: {outcome['error']}")
            encoded = body.encode()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), CallbackHandler)
    except OSError as error:
        raise RuntimeError(f"Cannot listen on 127.0.0.1:{port}: {error}") from None
    server.timeout = 1
    deadline = time.monotonic() + timeout
    print(f"Waiting for callback on http://127.0.0.1:{port}/callback …")
    try:
        while not outcome and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not outcome:
        raise RuntimeError(f"No OAuth callback received within {timeout} seconds")
    if outcome.get("error"):
        raise RuntimeError(outcome["error"])
    return outcome["code"]


def copy_to_clipboard(text: str) -> bool:
    for command in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            result = subprocess.run(command, input=text, text=True, capture_output=True)
            return result.returncode == 0
    return False


def masked(token: str) -> str:
    return token if len(token) < 16 else f"{token[:8]}…{token[-6:]}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("actor", choices=("andy", "agent"))
    parser.add_argument("--app-id", default=os.environ.get("THREADS_APP_ID"))
    parser.add_argument("--redirect-uri", default=os.environ.get(
        "THREADS_REDIRECT_URI", DEFAULT_REDIRECT_URI))
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--show-token", action="store_true",
                        help="print the full token instead of only copying it")
    args = parser.parse_args()

    app_id = (args.app_id or input("Threads App ID: ")).strip()
    if not app_id:
        raise RuntimeError("Threads App ID is required")
    app_secret = os.environ.get("THREADS_APP_SECRET") or getpass.getpass(
        "Threads App Secret (hidden): ")
    if not app_secret:
        raise RuntimeError("Threads App Secret is required")

    state = secrets.token_urlsafe(32)
    url = authorization_url(app_id, args.redirect_uri, state)
    print(f"\nActor: {args.actor}")
    print(f"Meta Valid OAuth Redirect URI must be exactly:\n{args.redirect_uri}\n")
    print(f"Open this authorization URL:\n{url}\n")
    if not args.no_browser:
        webbrowser.open(url, new=2)
    code = receive_code(state, LOCAL_PORT, args.timeout)
    credentials = exchange_code(app_id, app_secret, args.redirect_uri, code)

    actor = args.actor.upper()
    token_name = f"{actor}_THREADS_ACCESS_TOKEN"
    user_name = f"{actor}_THREADS_USER_ID"
    token = credentials["access_token"]
    copied = copy_to_clipboard(token)
    expires_at = int(time.time()) + credentials["expires_in"] if credentials["expires_in"] else 0

    print("\nAuthorization complete.")
    print(f"GitHub variable {user_name}={credentials['user_id']}")
    print(f"GitHub secret   {token_name}={token if args.show_token else masked(token)}")
    if copied:
        print("Full access token copied to clipboard.")
    elif not args.show_token:
        print("No clipboard command found; rerun with --show-token to print the full token.")
    if expires_at:
        print("Refresh before:", time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(expires_at)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, KeyboardInterrupt) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
