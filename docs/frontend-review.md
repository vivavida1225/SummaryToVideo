# Independent frontend review

Reviewed `docs/implementation.md`, `docs/frontend-report.md`, all files under `frontend/src`, the Vite configuration, package scripts, and document language. No frontend implementation edits, live API requests, or clipboard operations were performed. Root owns actual-browser integration checks.

Initial verification: with `.tools/node` in `PATH`, `npm --prefix frontend test` passed **8 tests**. A fresh build was unnecessary for this source review and was not run. The negative cases below were absent from the original component suite.

## Scoped re-review — accepted

All three original findings are **resolved**. A fresh independent run of `npm --prefix frontend test` passed **11 tests**.

- Startup now starts file listing and source/job restoration independently through `Promise.allSettled`, with separate error handling. The file-list failure regression confirms the default clipboard remains usable; inspection confirms saved-job recovery is in the same independent branch and still suppresses clipboard reading on successful recovery.
- The status heading has `role="status"`, `aria-live="polite"`, and `aria-atomic="true"`. Completion text is inside this live region; elapsed-time updates and the growing timeline are outside it. The component suite verifies completed status content.
- A polling 403 refreshes the session once and retries the job lookup. A remaining 403 or a 404 clears the stale job reference/storage, so no further poll is scheduled and source controls unlock. Regressions cover a missing job and successful recovery after token rotation. The added unmount guard prevents an in-flight operation from scheduling a new poll after disposal.

Root reports a fresh successful production build and real Edge checks of source entry, invalid input, archived-job recovery, downloads, clipboard content, and mobile layout. Those browser checks were not independently repeated in this scoped source review.

## Original findings (all resolved)

### 1. P2 — File listing failure prevents unrelated startup recovery and clipboard loading

**Location:** `frontend/src/App.vue:96–100`.

Startup awaits `api.files()` before inspecting the saved job ID. Any file-listing error jumps to the outer catch and skips both job recovery and the default clipboard read. For a saved running/completed job, the result and progress disappear even though `/api/jobs/{id}` may still work. Without a saved job, a file-system issue leaves the default clipboard source empty. A failure in the optional file-source mode should not prevent the selected clipboard mode or job recovery.

**Reproduction scenario:** Return a successful `/api/session`, HTTP 500 from `/api/files`, and a valid job from the stored `/api/jobs/{id}`. No job request is made. The same setup without a saved ID never calls `/api/clipboard/read`.

**Suggested fix:** Recover the stored job first, and isolate file-list loading in its own error boundary (or load it independently). Preserve the rule that a successfully recovered job suppresses startup clipboard reading. Add tests for file-list failure with and without a saved job.

### 2. P2 — Successful job completion is not announced to screen-reader users

**Location:** `frontend/src/components/StatusTimeline.vue:34`; `frontend/src/App.vue:252`.

The progress heading and event list change asynchronously but have no status/live semantics. The existing polite live region only receives clipboard/file refresh and manual copy/download messages; polling never updates it. Consequently, a user who starts a run and waits receives no automatic announcement that the final result is ready. Failures have alerts, so successful completion is the missing branch.

**Suggested fix:** Announce state transitions or at least completion through a small `role="status"` / polite, atomic live region. Do not make the elapsed-time counter or the entire growing timeline live, since those update every second. Add a test that moves a job from requesting to completed and checks the completion message in a live region.

### 3. P3 — Permanent polling errors continue indefinitely

**Location:** `frontend/src/App.vue:59–68`.

All polling failures run `schedulePoll()` again while retaining the prior nonterminal job. HTTP 403 after a backend restart therefore continues once per second even though the token cannot become valid without reinitialization. A permanent 404 similarly keeps the stale job marked as running and the source/run controls disabled; unlike startup recovery, polling has no missing-job handling. A full page reload can recover, but the polling loop itself cannot.

**Suggested fix:** Distinguish transient connection/server errors from 403/404. Stop automatic polling for a session error and present an explicit reconnect/reload action; clear a confirmed missing job ID or offer a reset while preserving any visible result. Add a bounded-request-count test for permanent errors and retain retry behavior for temporary failures.

## Contract and quality assessment

The normal user flow complies with the contract: clipboard is the default mode; files begin unselected; source HTML stays in text controls; startup does not create a compression job; successful saved-job recovery suppresses the clipboard read; the API token is attached to authenticated routes; duplicate submission is guarded; polling uses one-second scheduling and stops at terminal states; retry sends no source data; results and warnings remain available for failed jobs with retained artifacts; downloads fetch authenticated Blob responses; and output copy goes through the Windows backend.

Controls use native buttons, a labeled radio group, labeled source inputs, and labeled readonly results. Keyboard focus styling, reduced-motion handling, document `lang="ko"`, error alerts, and responsive CSS are present. Real rendering, mobile overflow, focus behavior, and browser download completion remain with root's browser checks.

**Current verdict:** Accepted for the reviewed frontend scope. All three original findings are resolved, 11 tests pass, and no new actionable issue was identified in the fixes.
