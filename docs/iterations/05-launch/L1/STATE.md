# L1 auth — STATE (2026-11-23)

Branch `it05-auth`, issue #24. Tested source: the commit this file is in (see `git log -1`).

## Done
- `POST /api/auth/google`: `app/api/google.py` `GoogleVerifier` — PyJWT RS256 only, JWKS cache (TTL 1 h), unknown kid →
  one refetch per 60 s, aud / iss (both forms) / exp+iat (±60 s, injectable clock) / sub / `email_verified is True`;
  401 `invalid_credentials`, 503 `auth_unavailable`, 404 when `GOOGLE_CLIENT_ID` unset. Upsert `(google, sub)` with email
  and name (email when no name). Real Google JWKS fetched once manually: 2 keys parsed.
- `POST /api/auth/invite`: `app/api/logins.py` — SHA-256 digests, compare with every code; name NFKC, 1–40, no control
  chars (400); user `(invite, "<index>:<casefolded name>")`; 404 when `INVITE_CODES` empty.
- `/api/auth/dev` now 404 when disabled (contract v1.2; was 401). Enabled only when both `DEV_AUTH_*` set.
- Login throttle: 10 / min / client IP over all three endpoints, 429 + `Retry-After`; client IP from `CF-Connecting-IP` /
  right-most untrusted `X-Forwarded-For` only when the peer is in `TRUSTED_PROXIES` (default loopback + private ranges).
- `/api/config` + `invite_login`; `User.email`; `/api/me.usage` **stub** (`messages_today: 0`, `daily_limit` from
  `DAILY_MESSAGES_PER_USER` default 50, `resets_at` next UTC midnight) — L2 replaces `usage_view` in `routes.py`.
- Title fallback (API layer, `summary_view`): stored title, else first non-blank user message (whitespace collapsed,
  60 chars as the agent does), else "New conversation". Store keeps the raw title so the agent's "set once" logic is unchanged.
  New store query `first_user_text` (ownership-checked).
- `Settings` repr hides secrets. Dependency: `pyjwt[crypto]>=2.10` (adds cryptography, cffi, pycparser).
- Design text: `design-8.1-proposal.md` (how Google login works, trust boundary).

## Verify
```
cd backend && HYPOTHESIS_PROFILE=ci uv run pytest -q tests/test_auth.py tests/test_http_api.py
bash tool/test_diff.sh && python3 tool/review_lint.py
```
`tests/test_auth.py` (31 tests): forged signature, tampered payload, wrong aud/iss, expiry + leeway, iat in future,
email_verified false/None/"true"/1, missing claims, malformed/oversized, HS256-with-public-key and `none`, unknown kid
refetch + refetch limit, TTL, JWKS failure / empty / bad keys → 503, replay, invite code/name bounds and identity,
isolation between invite users, throttle (window, per IP behind proxy, spoofed headers from untrusted peer), no
token/code in logs, env parsing, title fallback.

## For the coordinator (files outside my scope)
- `.env.example` / compose: add `GOOGLE_CLIENT_ID`, `INVITE_CODES`, `TRUSTED_PROXIES` (optional), `DAILY_MESSAGES_PER_USER`.
- The api container needs outbound HTTPS to `www.googleapis.com` (JWKS); httpx honours `HTTPS_PROXY` if one is needed.
- `INVITE_CODES`: append only (index is part of the account key).
- Real Google login not exercised end-to-end here (needs the browser + GIS from L3); planned in D.

## Open
- none blocking. L2 owns `usage_view` / limits; conflict surface in `routes.py` is that one function and `me`.
