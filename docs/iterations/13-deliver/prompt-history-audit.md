# Prompt history audit (2026-11-24)

Scope: `prompt history/` as exported by `tool/export_sessions.py` at `main` after 10-otel (57 files: 56 Claude Code
session files of the main session, workers and reviewers, plus REDACTIONS.md). Only counts are recorded here, never
values.

## Method
1. **Export-time redaction** (`tool/export_sessions.py`): literal values from the repo's `.env*` (except examples) and
   `.redact.local` (VPS origin IPs), plus token shapes (OpenAI-style `sk-…`, GitHub, AWS, Google API key, Slack,
   private keys). Replacements are listed per file in `prompt history/REDACTIONS.md`.
2. **Independent literal scan** (`audit.py`, run after export) against every secret we hold:
   - the dev machine's `.env` (DeepSeek key, Bailian key);
   - `.redact.local`;
   - the 10 reviewer invite codes and the coordinator test code;
   - the old dev-account password;
   - the production VPS `.env`: invite codes, session secret, keys. It was read on the build host into a mode-600
     temp file, deleted after the scan;
   - the build host's `.env`.
3. **Token-shape scan:** `sk-`, GitHub, `AIza`, private-key headers, long bearer tokens, JWTs, Cloudflare-style
   tokens.

## Results
| Check | Hits |
|---|---|
| Literals from the dev machine's `.env`, `.redact.local`, invite codes, test code, old password | 0 |
| Literals from the VPS `.env` | 0 |
| Literals from the build host's `.env` | 53, all non-secret settings: `DAILY_TOKEN_BUDGET` value ×21 (2,000,000) and `NO_PROXY` value ×32 (a localhost list). Not redacted |
| Token shapes | 1 `AIza…`-shaped match, inside base64 image data in the main session (checked in context: not a key) |

The Google OAuth **client ID** is public by design (it is sent to every browser). It was still redacted at export
because it sits in `.env`.

## Not included (owner decision needed)
- The homelab "potafree.net" Codex session (`rollout-2026-11-23T15-26-16-…`, 3.1 MB) did the homelab deploy and the
  VPS migration for this project. The same session also worked on other projects of the owner (the potafree.net site,
  other services, TLS certificates, their `.env` files). Including it unedited would publish that other work. Options:
  include it whole after the same redaction pass, or leave it out and describe what it did. Asked the owner.
