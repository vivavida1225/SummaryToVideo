# InfoX phase 4 implementation plan

**Goal:** Inspect actual assigned assets without remote writes and expose grounded company findings.
**Architecture:** Pure bounded ROOT graph inspector + typed SQLite CAS audit records + backend manager read-only continuation + Vue results. Existing draft receipt/event validation remains the gate.
**Spec:** `docs/infox/automation-implementation-spec.md` §8/§15 and phase 3 handoff.

## Decisions and constraints

- Work in the user-designated checkout; preserve README, launchers and outputs. Commit only phase 4 work and update the existing handoff, as in the preceding phase.
- Traverse connected ROOT children only. Auto-selected ImageLayer/VideoLayer references are in scope; explicit manual/template media are excluded. Unknown structure/markers are unresolved, never a successful empty audit.
- Image bundles use each child, never parent metadata. Match same-scene metadata by exact bucket/filename and video segment identity. Do not inspect scripts/queries/unselected candidates without a matching reference.
- Configured aliases: 삼성전자, SK하이닉스, 삼성, SK, 하이닉스, 삼전닉스. NFKC/case/whitespace normalization is for matching only. SK must retain ASCII word boundaries (including around whitespace-separated SK).
- Persist stable safe references, matched aliases/field/source paths, counts and ISO server version; no raw free text, URLs, credentials or complete remote payloads.
- Exempt only a typed internally verified saved favorite replacement with exact run/video/scene/layer/item/file/segment identity. Remote is_favorite never grants an exemption. No public provenance mutation API.
- New generation continues to an audit after draft readiness; existing verified drafts have an explicit read-only audit action. End at assets_audited or needs_attention; never ready_for_editor. Restarted audits only re-read.

## Tasks

- [x] Test and implement pure extraction, reference matching, alias boundaries, favorite history and conservative unknowns.
- [x] Test and implement strict audit records/CAS, async manager continuation/recovery and guarded audit API.
- [x] Connect result counts/problem list/reinspection and verify UI response loss/reload/layout.
- [x] Run complete tests/build/browser, audit the existing phase 3 video using reads only, update handoff and independent review.

Delivery: explicitly stage phase 4 files only; commit and verify the origin/master hash. Final delivery records the commit and remote verification.
