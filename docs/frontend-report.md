# Frontend implementation report

## Delivered

- Vue 3 + TypeScript + Vite frontend in `frontend/`, with exact dependency versions and `package-lock.json`.
- Warm light editorial dashboard with dark navy type, teal accents, responsive two-column layout, keyboard focus states, reduced-motion support, accessible live/error messages, and a detailed job event timeline.
- Same-origin typed API client. `/api/session` bootstraps the token; every subsequent API request sends `X-App-Token`.
- Editable clipboard text as the default source, explicit clipboard/file refresh controls, and a file selector with no preselected file. Input is only placed in text controls and is never rendered as HTML.
- Single-submit protection, one-second polling to a terminal state, sessionStorage job recovery, 409 `job_id` recovery, warnings and metrics, server-side clipboard copy, authenticated Blob downloads, and snapshot-only retry.
- Startup recovery happens before clipboard reading. A stored job suppresses `/api/clipboard/read`, preserving the clipboard on reload.
- Portable Node.js v24.21.0 at `.tools/node`; its Windows x64 archive matched the official SHA-256 `158f7685b44de51f6c0df1d153526cbcd3e1bc739a8dfc607721cef75de9e541` before extraction. Download artifacts were removed after extraction.

## Component test coverage

`src/App.spec.ts` verifies eight browser-facing behaviors:

1. Startup reads clipboard exactly once only when no job is stored, the raw source remains editable, and the explicit refresh reads it again.
2. Files begin unselected, refresh on request, and the eventual selection submits exactly `{file_path}` with the session token.
3. Clipboard submission is deduplicated, polls after one second, and renders the completed result with input/final scene metrics.
4. HTTP 409 preserves the response `job_id` and recovers the active server job.
5. Manual retry posts only to `/api/jobs/{id}/retry`, stores the new job ID, and does not reread clipboard/source.
6. A failed job without a serialized snapshot does not offer retry.
7. Copy posts the correct result stage; download fetches the authenticated artifact and starts a Blob download.
8. Reload restores a live job and its serialized result without touching the startup clipboard.

## Verification

- `npm test`: 8 tests passed.
- `npm run typecheck`: passed.
- `npm run build`: passed; Vite emitted `dist/index.html` plus hashed CSS and JavaScript assets.
- `npm ls --depth=0`: used to verify the pinned dependency tree is complete.

## Integration notes

- The Vite development proxy points to `127.0.0.1:8765`; production uses relative `/api` paths and expects FastAPI to serve the built frontend on the same origin.
- Artifact downloads intentionally always call the API, so the backend's in-memory fallback remains available when disk persistence fails.
- End-to-end browser/API verification depends on the backend and launcher integration owned by the root worker.
