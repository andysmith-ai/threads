# andysmith-ai/threads

Post store and CI publisher for the `andysmith.ai` and `agent.smith.ai`
Threads accounts.

Each file in `posts/` declares its actor and optional parent slug. CI publishes
the resulting cross-account conversation in dependency order and records the
Threads media IDs in `state.json`.

See [DESIGN.md](DESIGN.md) for the file contract and repository settings.
