# Prompt history

Raw, unedited sessions of the coding agents used to build Ocean Above. Only secrets were removed: each one is replaced
by `[REDACTED:<NAME>]`, and the counts per file are in [REDACTIONS.md](REDACTIONS.md). The audit is in
`docs/iterations/13-deliver/prompt-history-audit.md`.

- `claude-code/` holds Claude Code sessions, one folder per working directory:
  - `-Volumes-ZT-repos-llc2026-ocean-above`: the coordinator (main session). It talked with the owner, wrote plans,
    reviewed every PR and released.
  - `…-itNN-*`: implementer workers, one per task and branch (git worktree). Each was started by the coordinator in
    a terminal multiplexer (hc), and its task text appears as the first user message.
  - `…-review*`: read-only reviewers (independent code reviews).
  - Subagent transcripts are under the session folders.
- **Not included:** one Codex agent on the build host ("potafree.net"). It deployed the demo on the homelab and later
  moved it to the VPS (2026-11-23). Its session also covers the owner's other private projects, so it is withheld.
  The coordinator's messages to it and its replies are in the coordinator session.

Sessions are JSON Lines as written by the tools. Failed attempts, dead ends and corrections are kept on purpose.
