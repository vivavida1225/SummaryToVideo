# InfoX phase 6 implementation plan

**Goal:** Save only verified fixed replacement plans, reconcile uncertain writes by reading, and verify the persisted result before recording provenance.
**Spec:** `docs/infox/automation-implementation-spec.md` §10/11/15; phase 5 handoff and the user's phase 6 constraints.
**Architecture:** A typed save operation separate from project/generation receipts. One atomic whole-plan POST; immutable hashes and references, never remote JSON or signed URLs, are durable. A backend save coordinator rehydrates the fixed candidate, reconstructs the proposal from the latest unchanged source, and verifies full server content independently of the server version timestamp.

## Constraints and decisions

- Work in the requested checkout; preserve and exclude existing README, launchers, toggle and outputs. One final phase 6 commit, then authorized push to origin/master.
- Mapping evidence: editor API `saveVideoAndContextData`; editor `fetchVideoState` loads `response.context` into context state; save hook reads `videoState.context`. Actual existing videostate has object `context`, no `contextData`. Map `video` to `video`, `context` to `contextData`, `updated_at` to `updatedAt`; reject missing context rather than inventing one.
- One POST contains every target, so no intermediate follow-up save is necessary. Record its returned updated_at and compare to the subsequent read. Never apply read retry policy to save POST.
- Require exact source version and stable full-state fingerprint before writing. Reconstruct with the frozen choices and refreshed signed URLs; require original result_fingerprint and independent preservation checks.
- Unknown/intent_recorded/rejected/accepted recovery only reads. Compare server content with expected result after replacing only top-level updated_at with the source version and ignoring known signed URL fields. Changed content or unapplied unknown stays needs_attention; never automatically repeat POST or return to creation.
- Record saved_verified per placement only after a changed server version, expected full content, exact candidate references and draft checks pass. No-change plans take a separate unchanged_verified path and create no save receipt or provenance.
- Browser auto-navigation is phase 7. Server verification does not claim editor playback verification. Existing live run has no targets; no synthetic hits, new projects or generation writes in live validation.

## Review focus

- Concurrent owners: CAS claims before external write; results tied to same intent, no duplicate POST.
- Crash after intent / response / read: recovery routes to save reconciliation only.
- Signed URL refresh and server timestamp changes: ignore only known transport URLs and top-level version; reject unrelated mutations.
- Mixed image/video bundles, multiple layers, short and long clips: retain healthy items, slot boundaries, mute, TTS, captions and layout.
- Missing/invalid context, response status/version, candidate or frame budget: fail closed with a specific UI reason.

## Tasks

- [x] Add failing tests and implement mapping/client one-shot save, strict save model, typed CAS operations and verified history.
- [x] Add failing tests and implement save coordinator, recovery/manager/API routes, unchanged path and conflict/lost-response behavior.
- [x] Add UI status/actions and tests; exercise real local API with browser, persistence and no navigation.
- [x] Run whole suites/type/build, existing-run read-only live verification and editor inspection where possible; fresh review and handoff with precise limitations.
- [x] Prepare phase 6-only staging and authorized delivery; final commit/push and local/remote hash comparison are recorded in the delivery response.

## Execution ledger

- Source inspection complete; actual live read confirms context mapping shape. No remote video writes performed.
- User explicitly authorizes implementation and commit/push; no additional design approval is needed. Work in place to preserve the specified existing runtime linkage.
- Baseline: 551 backend tests passed. Final: 593 backend / 71 frontend tests passed, type check and production build passed. Dedicated save suites: 42 passed.
- Fresh review: Critical 0 / Important 2. Wrong signed resource path readback and disabled zero-target automatic completion reproduced RED, fixed GREEN. Also reproduced/fixed stale pre-save UI claims after verified saving. Full backend/frontend suites passed after the fixes.
- Separate durable URL-resource fingerprint retains all known signed URL field locations, hosts and paths; queries may refresh. Readback independently validates fixed candidate segment/preview URLs. No signed URL is persisted.
- Minor deferred: known POST 401/403 or 409 cause can be replaced by a generic rejection/readback error label. No write retry or false provenance follows; handoff records this diagnostic limitation.
- Live existing-run validation: unchanged_verified only, original immutable snapshot/receipts/video/task unchanged, zero video writes or synthetic hits. Actual replacement save/conflict response/readback/playback remain unverified because the authorized run has no targets. Editor autoplay/navigation remains phase 7.
- Recovery of saving/verifying is explicit; automatic interruption during earlier read-only audit/plan may stop at a ready plan for the existing save action. Durable automatic continuation intent is left to phase 7, preserving manual read-only APIs.
- Final controlled Edge browser smoke passed: save/read/verification stages, reload persistence, 390/1440px layout, zero external browser requests/navigation. Real editor replacement playback was not performed or claimed.
