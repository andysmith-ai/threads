# andysmith-ai/threads

Post store and CI publisher for the `andysmith.ai` and `agent.smith.ai`
Threads accounts.

Each file in `posts/` declares its actor and optional parent slug. CI publishes
the resulting cross-account conversation in dependency order and records the
Threads media IDs in `state.json`.

See [DESIGN.md](DESIGN.md) for the file contract and repository settings.

## Authorize the two accounts

The tablet-friendly OAuth app is deployed at
[`https://andysmith-ai.github.io/threads/`](https://andysmith-ai.github.io/threads/).

1. In the Meta Threads app, register that exact URL as a Valid OAuth Redirect URI.
2. Add both accounts as Threads Testers and accept both invitations.
3. Open the OAuth app on the tablet. Enter the Threads App ID and App Secret,
   authorize `andy`, switch the Threads login, then authorize `agent`.
4. Copy each resulting 60-day token into the corresponding GitHub Actions
   secret. The publisher resolves each Threads user ID from its token.

The app has no backend or third-party JavaScript. App credentials and temporary
OAuth transaction data stay in that browser's localStorage, and token exchanges
originate from the tablet—not Zeno. Long-lived tokens remain only in page memory
until copied to GitHub Secrets.

`oauth/authorize.py` remains a safer server-side alternative. It keeps the App
Secret out of browser storage and uses the same Pages URL only as an HTTPS
callback bridge:

```sh
python3 oauth/authorize.py andy --app-id <THREADS_APP_ID>
python3 oauth/authorize.py agent --app-id <THREADS_APP_ID>
```
