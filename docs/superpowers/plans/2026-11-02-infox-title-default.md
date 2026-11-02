# Editable InfoX default title

**Goal:** Default the Vue title field to today's `MMDD_시황영상` and let the user edit/save the default format in Vue.
**Architecture:** A small pure formatter expands uppercase MMDD with Asia/Seoul month/day. Vue stores the default format in browser localStorage, separate from each video's editable title. Saving the format explicitly applies it to the current title; restores of snapshots/pending intents keep their original titles. The final expanded title continues through the existing snapshot API.
**Constraints:** No remote InfoX requests or runtime record rewrites. Preserve unrelated README/launchers/toggle/outputs. Existing API defaults and immutable snapshots remain compatible. Handle invalid/blocked browser storage honestly.

- [x] RED/GREEN formatter and UI tests for date, saved format, individual title, snapshot/pending restore and storage failure.
- [x] Add editable default format, preview/help and explicit save/apply action using current Vue styling.
- [x] Frontend suite/type/build, backend regression suite and real local API/Edge form/restore verification.
- [x] Independent review, update handoff and prepare scoped authorized delivery; the final response records the commit/push and matching remote hash.

## Verification ledger

- Baseline f3536776e8e1ed955b162c543e77cbf06312375f. Unrelated README/launch/toggle and six outputs directories preserved.
- RED: 5 UI cases failed with the old title/default controls; formatter suite initially failed because the new module did not exist. GREEN: 70 targeted tests passed, including all 8 new cases.
- Final: backend 647 passed (3 existing dependency deprecations), frontend 93 passed, vue-tsc and production build passed. Corrected two test-only unsupported Testing Library role options found by the first type check.
- Controlled local API/Edge: current KST default, saved format persistence, individual title passed into snapshot, changing defaults without rewriting the snapshot, original-title restoration, and 320/390/1440px expanded settings layout all passed. Existing audit/plan/save/readback/history flow also passed; external requests/navigation zero.
- Reviewed the synthetic 390px screenshot. No overflow or inaccessible controls observed. Independent requesting-code-review found no material issues or deferred minors.
- No actual InfoX API calls, real project creation/generation/save or existing run mutations performed for this feature.
