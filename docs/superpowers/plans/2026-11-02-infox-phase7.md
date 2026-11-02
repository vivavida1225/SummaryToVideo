# InfoX phase 7 implementation plan

**Goal:** Verified editor navigation and durable automatic continuation without replaying admissions or saves.
**Spec:** `docs/infox/automation-implementation-spec.md` §15 and phase 6 handoff; the user's detailed phase 7 requirements.
**Architecture:** Keep production evidence in the immutable run/CAS record. Persist automatic versus manual continuation separately from snapshot settings. Store browser attempts in a separate SQLite table with one automatic claim per run and CAS transitions; a current click owns a waiting window, while restored pages only read records. Cross-origin navigation is a request, never evidence of logged-in editor readiness.

## Constraints and decisions

- Work in the authorized checkout and reuse its existing runtime linkage; exclude pre-existing README, launchers and outputs. One final scoped commit and push to master, as requested.
- The request specifies the design and authorizes implementation through delivery; no extra design approval gate.
- Automatic navigation requires ready_for_editor, saved_verified/unchanged_verified and no reconciliation. Fixed InfoX origin and validated run video ID only.
- External writes keep durable intent and expected_version CAS. Never repeat a save POST, infer acceptance by title/time, change frozen candidates or alter snapshot/preservation contracts.
- Existing records default to manual continuation. Explicit audit/plan APIs switch to manual before claiming work; only new start intent authorizes automatic continuation.
- Browser attempt records distinguish tab creation, navigate intent, navigation request, failures and user-reported login/readiness. Interrupted attempts remain uncertain and require explicit reopen.
- Preserve initial save write error/status independently of subsequent read/verification error, including after restart.

## Review focus

- Two browser tabs and rerenders must not duplicate an automatic navigation.
- Crash between audit, plan and save retains automatic intent; manual operations never save remotely.
- Restore and old DB defaults never reopen old videos or turn old plans into writes.
- Lost local browser acknowledgements cannot prove exactly-once navigation or editor login.
- Conflict/auth write errors survive failed readback and recovery without another POST.

## Tasks

- [x] 1. RED→GREEN automatic/manual continuation persistence, restart boundaries and diagnostic preservation tests; implement manager/store/save fields.
- [x] 2. RED→GREEN guarded browser attempt claim/CAS APIs and frontend window controller; add status/reopen/report UI and tests.
- [x] 3. Controlled local API/Edge scenarios, full backend/frontend/type/build, existing-run read-only/live browser inspection; separate actual versus controlled evidence.
- [x] 4. Independent review, fix material findings with regressions, append phase 7 handoff and prepare scoped authorized delivery; commit/push and matching hashes are reported in the final delivery response.

## Execution ledger

- Baseline HEAD: 6e9e0556f246c29db4164292beebc691592d5101. Pre-existing dirty README.md, scripts/launch.ps1, toggle.cmd and four outputs directories preserved.
- Interfaces: manager/store continuation is independent of snapshot; editor table does not increment run CAS version, preventing navigation bookkeeping from invalidating production evidence.
- Task 1: original 11 new recovery tests failed before implementation; 18 recovery tests now pass, including frozen selected interruption, two automatic owners, auth recovery, legacy conflict cause, both late-response orderings and unchanged placement provenance on re-verification. Save mapping, one POST, fingerprints and preservation checks remain unchanged.
- Task 2: editor API tests first failed before editor module existed; 5 now pass. Initial 9 frontend tests failed module resolution before implementation, then passed. Current frontend navigation suite has 13 passing tests, including a rejected stale claim after job reset.
- Independent final review (requesting-code-review): Critical 0 / Important 4. Rapid reopen during navigation, late restore/report responses, late save acknowledgement after another owner's read, and legacy write cause overwritten by read failure all reproduced RED and fixed GREEN. Additional coverage retains late HTTP outcomes even after another owner verified server content. No deferred minor findings.
- Existing-run API verification: same snapshot/video/task/receipts and unchanged_verified confirmed; zero remote video writes. User-browser Computer Use was stopped because the current Windows browser URL could not be determined safely. No actual logged-in editor or playback verification is claimed.
- Final whole suites: 616 backend passed (3 dependency deprecations), 84 frontend passed, vue-tsc and production build passed. An initial launcher run without documented PSModulePath failed 6 tests; corrected environment passes. One frontend timeout under concurrent load was followed by a passing standalone full suite.
- Extra final regressions: a late conflicting acknowledgement followed by another successful read previously duplicated provenance (RED); retain the original exact placement proof (GREEN). A failed stale browser claim after reset previously set another job's error (RED); epoch guard fixes it (GREEN).
- Final controlled Edge validation: 8 scenarios passed, including waiting/verified navigation, duplicate claim, blocked/closed/reloaded tabs, assignment failure, lost navigation-intent acknowledgement, and a real local backend restart/session refresh. The existing run/save browser smoke also passed. All InfoX writes and editor responses in browser tests are controlled; no external network requests or actual login/playback claims.
