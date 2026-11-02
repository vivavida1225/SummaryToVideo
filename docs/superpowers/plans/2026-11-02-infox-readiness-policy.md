# InfoX generated-text readiness policy

**Goal:** Stop comparing InfoX-generated narration/captions with the submitted script, as requested by the user after diagnosing the latest real run.
**Architecture:** Keep the snapshot and outbound payload unchanged. `verify_draft` checks identity, scene count/structure, nonempty generated content, audio files/duration/settings, and nonempty timed captions. It does not compare returned text with either the snapshot or other returned text. Keep source/result fingerprints and preservation checks across asset saving.
**Scope:** Existing checkout/runtime; preserve unrelated README, launch scripts, toggle and outputs. Do not resume the real automatic run or perform remote video writes as part of validation.

- [x] Write failing readiness and save-preservation regressions.
- [x] Remove original-text comparisons, update UI wording and current specification/handoff policy.
- [x] Run backend/frontend suites, type/build and controlled browser checks; read-only readiness check of the current failed real run.
- [x] Review and prepare scoped delivery under the existing commit/push authorization; the final response reports the actual commit and matching remote hash.

## Verification ledger

- Baseline e77fd59710fb951a5d130e2f78bff7cc331035c6. All unrelated README, launch/toggle and five outputs directories preserved.
- RED: two generated-text readiness cases and three full save-preservation scenarios failed with the old original-text equality policy. GREEN: accept generated text while retaining complete source/result preservation during saving.
- RED/GREEN: retain the previously valid timed empty caption placeholder when another segment supplies real text. Empty or malformed production data still fails.
- Target readiness suite: 33 passed. Final full backend: 638 passed with 3 existing dependency deprecations. Frontend: 84 passed; type check and production build passed.
- Controlled local API/Edge smoke passed with narration/captions different from the immutable snapshot, through audit/plan/one save/readback/history restoration and responsive layout; zero external browser requests.
- Existing latest failed real run: all four readiness predicates now true; same input/snapshot and five scenes. Raw DB record unchanged, zero video writes and no automatic resume. Persisted error is cleared only through a subsequent explicit run-state check with the updated backend.
- Independent requesting-code-review: no material findings. No deferred minors. Historical exact-text requirements are explicitly superseded in the specification and handoff; actual replacement save/playback remain outside this read-only validation.
