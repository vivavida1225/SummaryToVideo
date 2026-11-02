# InfoX phase 5 implementation plan

**Goal:** Freeze a technically usable favorite segment and validate a local, timing-preserving replacement plan without saving to InfoX.
**Spec:** automation-implementation-spec.md §9/10/15 and phase4 handoff.
**Architecture:** Strict durable plan/selection models; paginated read-only favorite collector and isolated media availability probe; pure MediaLayer/VideoLayer transformer and independent preservation checks; backend manager/API/Vue preview.

## Decisions and constraints

- Preserve unrelated README/launchers/outputs; update the existing handoff and commit only phase 5 files to origin/master after verification.
- General video favorites are supported. Uservideo remains explicitly unsupported until a nonempty schema/compatibility check exists. Search=반도체, favorites_only=true, date filter omitted deliberately; no title/company/visual rejection.
- Bounded complete pagination and deterministic first usable segment reused across targets. Persist selections by CAS before conversion; retries revalidate that exact selection and never silently choose another.
- Read current videostate and revalidate draft/audit version/references. Persist stable references and URL-free fingerprints only; rehydrate current signed media/preview URLs in memory from the exact chosen item. No saved_verified provenance until phase 6.
- Use current public editor source and HAR evidence: ImageLayer duration allocation, MediaLayer image_files with media_type=video (images default image), explicit durationInFrames and clip-local trim. Mixed bundles retain count/order and normal image metadata, with only implicit slot duration made explicit where necessary.
- Source editor module 12764 in 20700-31e582d53acc3cba.js defines hY/Qg allocation. Current 30fps contract; unsupported layouts/timing fail closed. Short clip loops are planned with explicit slot width; real playback is phase 6 verification.
- Only target media/type and necessary slot-duration fields may change. Verify full data, root links, normal assets, narration/voice/captions, geometry/effects, and timing independently. No video create/start/update or browser navigation.

## Tasks

- [x] Test/implement candidate schema, pagination, stable choice and credential-isolated bounded media reads.
- [x] Test/implement local single-video/mixed-bundle conversion and independent preservation checks.
- [x] Test/implement CAS selection/plan storage, async API/recovery/UI preview and auto-plan only when configured.
- [x] Run all tests/build/browser and read-only live verification, independent review and update handoff.

Delivery: stage phase 5 files only, commit and verify origin/master. The final delivery report records the commit and remote hash.
