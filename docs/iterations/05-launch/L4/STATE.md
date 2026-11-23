# L4 auth/limits review fixes — STATE (2026-11-23)

Branch `it05-auth-fix`, issue #29, report `docs/iterations/05-launch/review-auth.md`. Tested source: the commit this
file is in (see `git log -1`). Backend only; contract / `.env.example` / `docs/deploy.md` / Caddyfile are the
coordinator's (agreed over hc).

## Findings → fix → test

| # | Fix | Tests |
|---|---|---|
| P1-1 | Invite account key = `sha256("ocean-above invite account v1\0" + code)[:32]` (`logins.account_key`), not the code's index. Duplicate codes collapse. | `test_auth.py::test_review_1_invite_account_key_is_the_code_not_its_position`, `test_auth_review.py::test_review_1_removing_a_code_does_not_move_accounts` |
| P1-2 | `GoogleVerifier`: failed fetch keeps the last good keys (usable ≤ `max_stale` 24 h after the last success); next attempt after `min_refetch` doubling per consecutive failure, capped at `max_backoff` 15 min; single flight (`_fetching` + Condition); the fetch runs with the lock released; a request without a usable key waits only for the running fetch (≤ 4 s); 503 only when no usable key set exists; fetch timeout 5 → 3 s. | `test_review_2_failed_fetches_are_rate_limited_and_back_off`, `…_a_failed_refetch_keeps_the_cached_keys`, `…_stale_keys_are_used_while_google_is_down_then_expire`, `…_the_fetch_runs_outside_the_lock_and_only_once`, `…_concurrent_cold_requests_share_one_fetch`, `…_rotation_still_works` (+ existing L1 kid/ttl tests unchanged) |
| P1-3 | One account per code (same key as #1): the name is only the display name, refreshed on each login → one code = one daily limit. | `test_review_3_one_code_is_one_account_and_one_daily_limit` |
| P2-4 | `CF-Connecting-IP` used only with `TRUST_CF_CONNECTING_IP=1` (default off), still only from a trusted peer; otherwise right-most untrusted XFF. | `test_review_4_cf_header_is_ignored_unless_trusted`, `…_spoofed_cf_headers_do_not_reset_the_throttle`; `test_auth.py` client-ip + throttle tests updated |
| P2-5 | Throttle key `throttle_key(ip)`: IPv6 → its /64, IPv4-mapped → IPv4. | `test_review_5_ipv6_addresses_share_their_64`, `…_rotating_ipv6_addresses_hits_the_throttle` |
| P2-6 | Follows from #1/#3: the name is not part of the identity; a different code never reaches another account. Give each reviewer their own code. | `test_review_6_the_name_is_not_a_credential` |
| P2-7 | Store schema **v3**: `users.session_epoch`. Cookie `user.epoch.expires.sig`; logout (with a currently valid cookie) bumps the epoch → all that user's cookies die. `current_user` also requires the login method to be on: dev → dev login enabled; invite → code still in `INVITE_CODES`; google → `GOOGLE_CLIENT_ID` set. | `test_review_7_logout_revokes_copied_cookies`, `…_logout_with_a_stale_cookie_does_not_log_out_the_new_session`, `…_removing_a_code_ends_its_sessions`, `…_turning_logins_off_ends_their_sessions`, `…_old_format_cookies_are_rejected`; v1→v3 migration in `test_limits.py` |
| P2-8 | `session.verify`: non-ASCII → None; compare bytes. | `test_review_8_non_ascii_cookies_are_unauthorized` (4 cases, unit + HTTP 401) |
| P2-9 | `_number` requires `math.isfinite`; `exp - iat ≤ 24 h`. | `test_review_9_non_finite_time_claims_are_rejected` (4), `…_lifetime_over_a_day_is_rejected` |
| P2-10 | `tokens_since`: NULL tokens count as `len(request)/3 + 4096`. Running turns overshooting the budget: accepted (bounded now that one code = one limit); see U1. | `test_limits.py::test_review_10_calls_without_a_token_count_are_estimated`, `…_unreported_calls_can_exhaust_the_site_budget` |
| P2-11 | `guards.ApiGuard` (ASGI, `/api/*`): body > 64 KB → 413 `payload_too_large` (by Content-Length, or counted while reading chunked); body buffered and replayed. Field bounds (after-validator, keeps lone surrogates a 401): code/name/username/password/nonce 200, credential 8192, text 8000. | `test_review_11_oversized_bodies_are_refused_before_parsing`, `…_message_text_bound`, `…_guard_passes_disconnects_and_other_paths` |
| P2-12 | Startup warning when Google/invite login is on and `COOKIE_SECURE` off (`Settings.warnings`). Non-GET `/api/*`: 403 `forbidden_origin` when `Sec-Fetch-Site` is `cross-site`/`same-site`, or `Origin` (incl. `null`) ≠ `Host` / `X-Forwarded-Host` (Next dev rewrite). No header → allowed (non-browser). | `test_review_12_insecure_cookie_with_public_logins_is_warned`, `…_cross_site_writes_are_refused` (12 cases), `…_cross_site_post_over_http` |

## Behaviour changes (agreed with the coordinator, for the PR and contract)
1. New error codes, same shape: 413 `payload_too_large`, 403 `forbidden_origin`.
2. Cookie format `user.epoch.expires.sig`: existing cookies stop working (sign in again). Logout ends every session of the user.
3. Invite account = one per code; `index:name` accounts from L1 are not migrated (the demo has no invite users yet).
4. New env `TRUST_CF_CONNECTING_IP=1` — **required on the homelab** (behind Cloudflare); without it every client
   behind cloudflared shares the Caddy/XFF throttle key.

## For the coordinator
- `.env.example` / `docs/deploy.md`: `TRUST_CF_CONNECTING_IP`; "one code per reviewer"; "renaming `DEV_AUTH_USER`
  does not end the old dev user's sessions — rotate `SESSION_SECRET`"; Caddy `request_body { max_size 64KB }`.
- Proposed invariants for `docs/design/state_invariants.md`: **S1** a session is valid only while its epoch equals
  `users.session_epoch` and its login method is enabled; **U2** an invite code maps to exactly one account,
  independent of the code list order.
- Residual (not changed): with default `TRUSTED_PROXIES` a LAN client in dev (192.168.x) can still choose its XFF
  throttle key; production peers are the compose network only. No global failed-login cap (would itself be a DoS lever).

## Verify
```
cd backend && HYPOTHESIS_PROFILE=ci uv run pytest -q tests/test_auth_review.py tests/test_auth.py tests/test_limits.py tests/test_http_api.py
bash tool/test_diff.sh && python3 tool/review_lint.py
```
Result at this commit: full suite no new failures (0 known); lint all ok.
