# andysmith-ai/threads

Post store and CI publisher for the `andysmith.ai` and `agent.smith.ai`
Threads accounts.

Each file in `posts/` declares its actor and optional parent slug. CI publishes
the resulting cross-account conversation in dependency order and records the
Threads media IDs in `state.json`.

See [DESIGN.md](DESIGN.md) for the file contract and repository settings.

## Authorize the two accounts

1. Enable **Settings → Pages → Build and deployment → GitHub Actions**.
2. In the Meta Threads app, register this exact Valid OAuth Redirect URI:
   `https://andysmith-ai.github.io/threads/`.
3. Add both accounts as Threads Testers and accept both invitations.
4. Run once per account:

   ```sh
   python3 oauth/authorize.py andy --app-id <THREADS_APP_ID>
   python3 oauth/authorize.py agent --app-id <THREADS_APP_ID>
   ```

The helper prompts for the App Secret without echoing it, opens the authorization
URL, receives the one-time code through the Pages-to-localhost bridge, exchanges
it for a 60-day token, prints the Threads user ID, and copies the full token to
the clipboard. Use `--no-browser` when the account is signed into a different
browser profile; use `--show-token` only when clipboard delivery is unavailable.
