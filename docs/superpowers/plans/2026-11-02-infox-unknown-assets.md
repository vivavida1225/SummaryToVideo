# InfoX unknown asset continuation policy

**Goal:** Treat unknown asset verdicts like non-detections for progression and replacement targeting, as requested by the user.
**Architecture:** Preserve unknown verdicts/reasons/counts. Completed audits may contain unknown items and metadata-unreadable warnings. A shared `blocking_issues` property retains structural/version/identity/inspection-budget failures. Plans replace only detected items; unknown media is preserved through the existing independent source/result checks. Manual audit/plan remains read-only.
**Constraints:** Keep immutable snapshot, CAS receipts, frozen favorite selection, one-shot save, frame/timing schema, fingerprints and server verification. Do not resume the real automatic run or make remote video writes during validation. Preserve unrelated README, launchers, toggle and outputs.

- [x] RED/GREEN pure audit, replacement, automatic/manual and save-preservation regressions.
- [x] Update shared audit gates, UI explanation and current spec/handoff.
- [x] Full backend/frontend/type/build, controlled Edge flow with unknown media, read-only real audit.
- [x] Independent review and prepare scoped authorized delivery; the final response records the actual commit/push and matching remote hash.

## Verification ledger

- Baseline b88c2d0a559e5b59474b63748d14043840dff83c. Unrelated README, launch/toggle and six outputs directories preserved.
- RED: 9 new backend cases failed because unknown audits blocked or the shared blocking-issue view did not exist. GREEN: all pass; related targeted suites 87 passed.
- RED/GREEN: UI explains that unknown assets are retained and do not block progression, while still showing their unknown verdict/reason.
- Final: 647 backend passed (3 existing dependency deprecations), 85 frontend passed, type check and production build passed.
- Controlled local API/Edge smoke passed with unknown metadata retained through detected-only planning, one save/readback, history restoration and 390/1440px layout. Unknown source file preserved; external browser network requests zero.
- Read-only real audit: detected=4, not_detected=4, unknown=2 (both metadata_missing), new audit completed, zero blocking issues. No DB changes, auto resume, candidate claim or remote video writes.
- Independent review: no material findings or deferred minors. Clarification: descriptive text decoding depth >8 remains nonblocking metadata_unreadable; global node budget and ROOT/metadata-node traversal limits remain blocking inspection_limit. This distinction is explicit in the updated specification and handoff.
