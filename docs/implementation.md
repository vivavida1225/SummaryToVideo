# Local market compressor — implementation contract

Approved scope: Vue3/TypeScript/Vite + Python/FastAPI on Windows. Clipboard HTML is default; no default file, no automatic API call on page load. One explicit run serializes HTML, saves and copies it, then compresses with gemini-3.5-flash-lite, validates, saves and copies final text. Numbered keys from existing .env rotate on recoverable failures. Three calls total, 60 seconds/call, 300 seconds overall, 2/4-second retry waits (server hints take precedence). Invalid format gets at most one repair within that budget. Serialized input uses === separators; compressed output is a five-line plain-text narration, isolated per run.

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

- Existing user .env and src inputs stay intact. The compression prompt is read each request and a five-line narration output contract is appended. No separator example normalization is performed.
- Files only inside resolved src; outputs inside unique outputs/run_id. Reject symlink/junction escapes and oversized inputs (5 MiB).
- Clipboard win32 implementation supports Unicode text and CF_HTML, with bounded contention retries. Text with literal news HTML takes precedence over rich text.
- Script-only whitespace trims at text boundaries; internal line breaks become spaces (block elements separated), inline node text concatenated without invented spaces. Ambiguous structure fails instead of producing partial content.
- Exactly 5 nonempty narration lines, with no fences, headings, numbering or separators. Each line contains 1..2 period-terminated body sentences; decimal points are not sentence boundaries. Fixed opening/closing greetings on lines 1/5 are excluded from sentence counts (2..3 total sentences on those lines). Only outer whitespace and CRLF are normalized; the validator never merges, splits or rewrites narration.
- Line roles: market result/intraday path; strongest external variable; leading sectors/flows; investor flows/risks; market definition/next-session checks. The prompt selects three concrete takeaways and forbids unsupported facts or stronger causal claims. These semantic requirements are reviewed separately from deterministic validation.
- First-line KOSPI/KOSDAQ clauses must contain the matching final index, percentage and direction from the input. Thousands separators may differ; percentages accept % or 퍼센트; a 0.00% change may be described as 보합. Percentage direction is checked against the input point change, including unsigned percentages. Explicit closing quotes take priority over intraday quotes. Ambiguous prose triggers the existing repair request. Index triples remain mandatory in serialized input.
- body_char_count counts all Unicode code points including greetings/numbers/spaces/punctuation, excluding line endings. 490..550 inclusive is required, with a target of 500. Both short and long drafts raise validation errors; there is no automatic sparse-input exception. scene_count still means input scene count. HTTP types and artifact names are unchanged. Old stored results are loaded/copied/downloaded as-is without revalidation or migration.
- Direction vocabulary includes 급락/폭락/내림세 and 급등/폭등/반등/오름세; matching is still checked against the source direction. A recognized opposite direction remains an error.
- Repairs preserve source, prior model draft and corrective user instructions as separate conversation turns, with the full system prompt retained. Feedback includes all independently detectable validation violations, measured character/line counts, amount to add/remove, five-line scene roles, greeting/sentence rules and per-scene size guidance. One repair is allowed within the existing three-call total budget.
- A final response validation failure retains state `failed` and its error, but exposes the exact last draft through `compressed`, saves it as `compressed.txt`, and counts its characters excluding newlines. The existing result article supports manual copy/download while the failure alert stays in the progress column; there is no automatic copy. Loading older validation failures falls back to the latest `invalid_response_N.txt` without rewriting historical files. Other provider/input failures do not become narration results.

- Model responses use exactly four `<SCENE_BREAK>` delimiters for five scenes. The backend trims separator-adjacent whitespace, converts explicit boundaries to newlines, then runs narration validation. Missing/excess delimiters, empty scenes and internal scene newlines are errors; boundaries are never inferred. Legacy plain five-line responses remain accepted. Every nonempty response is archived as `raw_response_N.txt`; invalid originals also remain in `invalid_response_N.txt`. `compressed` always uses decoded text, including failed drafts shown for manual review. Length excludes delimiters and newlines. The frontend and HTTP artifact names are unchanged.
- Only one active job. Explicit manual retry creates new run from original serialized snapshot; no prompt/clipboard reread of source data. Startup must not overwrite clipboard.
- Work in existing requested folder: no Git repository/CLI exists, so worktree and commit workflows do not apply.
