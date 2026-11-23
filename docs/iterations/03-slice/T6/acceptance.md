# Acceptance — T6 minimal frontend (2026-11-23)

- Tested source: PR #8 head `0572ff2` (squash-merged as `b442722`). Coordinator reran `npm test` (19 passed); lint ok;
  PR only touches `frontend/**` and `docs/iterations/03-slice/T6/**`.
- Screenshots reviewed (desktop 1440×900, mobile 390×844, mock API): cards match the contract, only `*_display`
  numbers shown, Confirm booking gated by state, trace expandable, mobile drawer, no horizontal scroll.
- Round 02: Next dev indicator 9/1 issues explained (browser extension mutating `<html class>`; duplicate ids in the
  mock, fixed); app code has none.
- Carried to 04: streamdown chunk 149 KB gzip (trim or fall back to react-markdown); live expiry countdown;
  Impeccable critique/audit; npm needs an explicit proxy on this machine (machine config, not repo).
