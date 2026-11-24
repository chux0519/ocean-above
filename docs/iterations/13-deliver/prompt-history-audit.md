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

## Final re-scan (after the last export, 64 files)
- Dev machine `.env`, `.redact.local`, invite codes, test code, old password: 0 hits.
- VPS `.env`: one non-secret setting (`DAILY_TOKEN_BUDGET` = 2,000,000) ×21; no key, secret or invite code.
- Token shapes: the same base64 false positive only.

## Not included (owner decision, 2026-11-24)
- The homelab "potafree.net" Codex session (`rollout-2026-11-23T15-26-16-…`, 3.1 MB) did the homelab deploy and the
  VPS migration for this project. That one session also holds the owner's other private projects (another website,
  network and SEO services, TLS certificates, other `.env` files), so it is **left out**. What it did for Ocean Above
  is described in `prompt history/README.md` and in `docs/deploy.md`. Its instructions came from the coordinator
  session (included); its results are visible there as the hc messages it returned.
