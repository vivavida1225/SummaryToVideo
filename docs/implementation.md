# Local market compressor — implementation contract

Approved scope: Vue3/TypeScript/Vite + Python/FastAPI on Windows. Clipboard HTML is default; no default file, no automatic API call on page load. One explicit run serializes HTML, saves and copies it, then compresses with gemini-3.5-flash-lite, validates, saves and copies final text. Numbered keys from existing .env rotate on recoverable failures. Three calls total, 60 seconds/call, 300 seconds overall, 2/4-second retry waits (server hints take precedence). Invalid format gets at most one repair within that budget. Outputs are plain UTF-8 text with === separators, isolated per run.

## Shared HTTP contract

- Same-origin server. GET /api/session => {token, model, configured_keys: number[]}. Token in X-App-Token for every other /api route except /api/health. Session fetch must be same-origin (no CORS); backend enforces Host and Origin.
- GET /api/health => {app: "summary-to-video", ready: true, instance_id: string}.
- GET /api/files => {files: [{path: string, size: number}]} (paths relative to src).
- POST /api/clipboard/read => {text: string, format: "text"|"html"}.
- POST /api/jobs body {html: string} OR {file_path: string}, never both; returns Job (202).
- GET /api/jobs/{id} => Job.
- POST /api/jobs/{id}/retry => new Job (202), uses prior serialized snapshot.
- POST /api/jobs/{id}/copy body {stage: "serialized"|"compressed"} => {ok: true}.
- GET /api/jobs/{id}/artifacts/{name}: serialized.txt / compressed.txt only. Fetch with token then download Blob; fallback to in-memory content if disk save failed.
- POST /api/shutdown (token required): graceful local server shutdown, launcher uses this.
- Errors: {detail: string}, conflicts optionally {detail: string, job_id: string}.
- Job = {id, parent_id: string|null, state: "queued"|"serializing"|"copying_serialized"|"requesting"|"retry_wait"|"validating"|"copying_compressed"|"completed"|"failed", source: string, created_at: ISO string, elapsed_seconds: number, attempt: number, max_attempts: 3, key_number: number|null, retry_at: ISO|null, serialized: string|null, compressed: string|null, scene_count: number|null, body_char_count: number|null, warnings: string[], error: string|null, output_dir: string, events: [{at: ISO string, message: string}] }.

## Tasks

1. Root: Python serializer, validator, Gemini adapter/retry runner, job orchestration, FastAPI/security and tests.
2. Frontend worker: frontend/ only (plus .tools Node download if needed), full Korean interface and component tests; use contract above. Store job id in sessionStorage; on reload recover first, otherwise clipboard once. Poll 1s until terminal. Prevent duplicate clicks. Display results, warnings, copy/download, retry. Support a failed save with in-memory download via API. No rendering input as HTML.
3. Launcher worker: scripts/, start.cmd, stop.cmd; .venv and frontend production build managed by launcher, detect build/dependency freshness. Use .runtime/server.json (pid, port, instance_id), hidden server, reuse health-identified app, scan 8765..8775, secure shutdown via session token. Launch python -m backend.server --port PORT --instance-id ID. Root sets app root from package location. Runtime Node 24 LTS in .tools/node; python already present. Handle paths with spaces/Korean, logs and failure dialogs. No global git operations (git unavailable, no .git directory).
4. Review and integration: unit/API/frontend tests, Windows launch/lifecycle, browser checks and one actual Gemini integration attempt. Document any external key/model availability blocker accurately.

## Decisions

- Existing user .env and src inputs stay intact. Existing compression prompt is read each request; normalize separator examples in memory, append deterministic output contract; preserve source prompt.
- Files only inside resolved src; outputs inside unique outputs/run_id. Reject symlink/junction escapes and oversized inputs (5 MiB).
- Clipboard win32 implementation supports Unicode text and CF_HTML, with bounded contention retries. Text with literal news HTML takes precedence over rich text.
- Script-only whitespace trims at text boundaries; internal line breaks become spaces (block elements separated), inline node text concatenated without invented spaces. Ambiguous structure fails instead of producing partial content.
- Exactly 5 compressed scenes, first scene has title, one description, blank line, original KOSPI/KOSDAQ triples. Scenes 2..5 title + one description line. One blank line only before first index. 45/70 description limits exclude first-scene bullet prefix; count title + description incl spaces/punctuation excluding markers/newlines/index block. Preserve index triples verbatim. Title 25 and total 400..550 are warnings; 650 hard maximum.
- Only one active job. Explicit manual retry creates new run from original serialized snapshot; no prompt/clipboard reread of source data. Startup must not overwrite clipboard.
- Work in existing requested folder: no Git repository/CLI exists, so worktree and commit workflows do not apply.
