# InfoX phase 3 implementation plan

**Goal:** Execute one immutable transmission snapshot and verify its draft using the matching task event and server data.
**Architecture:** Existing FastAPI/Vue and SQLite CAS receipts. A backend manager owns short write admission and long bounded tracking; the UI polls by run ID. No asset writes or automatic navigation.
**Spec:** `docs/infox/automation-implementation-spec.md` §7, §12, §15; `phase1-handoff.md`; `phase2-runs.md`.

## Constraints and decisions

- Work in the explicitly requested checkout; preserve unrelated README, launchers and outputs. Stage only phase 3 files in one final commit.
- Authenticate before claiming writes. Persist begin_request before each one-shot external write, record_outcome with expected_version afterward. Never replay unknown/intent_recorded writes.
- Subscribe before generation start and buffer events until its task ID is persisted. Resume only reads after restart. Unverifiable receipt or missed completion requires attention, never a new project.
- Completion requires matching channel/task completed without errors plus exact ordered scene text, source input, voice and segment captions. Progress/edit permission/time alone are insufficient.
- Draft readiness ends at `draft_ready`, the phase 4 entry point. Auto-replacement remains pending.
- Store allowlisted event summaries and validation booleans only; no raw events, credentials, remote errors or signed URLs.

## Tasks

- [x] Add one-shot client writes, state verifier and matching event parser; test HTTP retry exclusion and false completion signals first.
- [x] Add CAS tracking state and backend manager, restart/reconciliation, stream buffering/reconnect, delayed state reads; test admission races and uncertain writes first.
- [x] Connect protected start/resume APIs and UI polling/manual editor link; retain snapshot/review/revision policy; test response loss and reload.
- [x] Run all backend/frontend checks and local browser smoke; execute exactly one durable live run, inspect sanitized event/data evidence, update handoff and independent review.

Delivery: explicitly stage only phase 3 files, commit on master and verify the pushed origin/master hash. The final delivery report records the commit and remote verification.
