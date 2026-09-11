# Backend review

Reviewed `docs/implementation.md`, the existing serializer prompt, all `backend/*.py`, and backend tests. Read-only implementation review; no live Gemini request, real clipboard access, dependency changes, or implementation edits were performed.

Initial validation: `.venv/Scripts/python.exe -m pytest tests -q` passed **42 tests**. Additional in-process reproductions confirmed findings 1–3 and 5 below. Those cases were absent from the original suite.

## Scoped re-review — accepted

All five findings below are **resolved**. A fresh review run of `.venv/Scripts/python.exe -m pytest tests -q` passed **47 tests** (three dependency deprecation warnings).

- The first state event now uses `persist=False`, so valid source text is serialized into memory before any output write. An unavailable output directory stops API spending while keeping the serialized text downloadable; the new regression exercises this case.
- The section-ancestor walk stops at the selected news root, and the wrapped checked-in sample regression passes.
- The expanded block-tag set separates adjacent headings while the existing exact inline-text and golden-output tests still pass.
- Output base and run directories must now resolve to their intended locations. The regression creates an actual Windows junction pointing elsewhere within the temporary application root and verifies no redirected artifact is written.
- A `RequestValidationError` handler returns a string-valued `detail` without echoing the input body; the regression passes.

The Gemini TLS update was also reviewed without network access. `truststore==0.10.4` is pinned, and separate `truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)` objects are passed through both supported Google SDK `client_args` and `async_client_args`. Inspection of the installed SDK confirms those contexts are preserved for httpx. A mocked `genai.Client` invocation verified both contexts retain `CERT_REQUIRED` and hostname checking, the timeout is 60,000 ms, SDK attempts remain 1, and automatic function calling is disabled. This uses native certificate validation; it does not disable TLS verification.

The root agent reports a completed real Gemini run using two calls including one format repair, plus exact final clipboard verification. These integration results were not independently repeated in this scoped review.

## Original findings (all resolved)

### 1. P2 — Metadata persistence prevents the in-memory fallback before serialization

**Location:** `backend/jobs.py:90`, called from `backend/jobs.py:104`.

Every state transition synchronously persists metadata and lets write errors escape. If `outputs` is unwritable or the disk is full, the initial `serializing` event fails before HTML is serialized. The job becomes failed with both `serialized` and `compressed` set to `None`; neither download nor snapshot retry is possible. This is the common form of save failure, but the current save-failure test only injects a failure for `serialized.txt` while allowing metadata writes.

**Reproduction:** Inject `OSError('disk read-only')` from `manager.store.metadata`, then run the checked-in valid HTML sample. Observed `failed`, `serialized=None`, `compressed=None`.

**Suggested fix:** Make metadata persistence best effort, preserve job events/state in memory, and record a deduplicated warning. Still respect the selected policy of stopping API spending if the serialized artifact cannot be saved. Add a test for an entirely unwritable output store that verifies the serialized result remains downloadable.

### 2. P2 — An outside section wrapper incorrectly invalidates valid news

**Location:** `backend/serializer.py:79`.

The nested-section check calls `section.find_parent('section')` without stopping at the selected news root. Consequently, an unrelated outer page `<section>` causes all valid news sections inside `.embedded-content` to be rejected. This conflicts with the requirement to ignore content outside the actual news root and is relevant to CF_HTML, which can include the full page context.

**Reproduction:** `serialize_html('<section class="page-wrapper">' + sample_html + '</section>')` raises the nested-section error, while the unwrapped checked-in sample succeeds.

**Suggested fix:** Reject section ancestors only between the selected section and the selected news root. Add a wrapped-sample regression test while retaining rejection of true nested news sections.

### 3. P2 — Block elements other than p/div/li silently concatenate words

**Location:** `backend/serializer.py:23`.

Only `p`, `div`, and `li` introduce block boundaries. Other common blocks, including headings and blockquotes, concatenate adjacent text with no separating space. The approved whitespace decision explicitly requires block elements to be separated, while inline elements remain concatenated.

**Reproduction:** `_text(BeautifulSoup('<header><h1>Market</h1><h2>Summary</h2></header>', 'html.parser').header)` returns `MarketSummary` rather than `Market Summary`.

**Suggested fix:** Use an explicit, sufficiently complete block-tag set, preserving current inline behavior. Test adjacent heading/block elements and unchanged adjacent inline spans.

### 4. P2 — Output directory validation permits redirection into another run

**Location:** `backend/storage.py:54–60`.

`directory()` resolves `outputs/<job_id>` and checks only that it remains under the application root. A pre-existing junction/symlink at `outputs/<id>` pointing to a different run or another directory within the app is accepted. Subsequent checks also accept it because they use the already redirected folder as the boundary. This violates the contract that results remain isolated inside their unique `outputs/run_id` directory. This finding is based on code inspection, not creation of a real Windows junction.

**Suggested fix:** Verify the resolved output base and run directory against their intended locations, and reject run-level redirection rather than only escapes from the app root. Add a junction/symlink containment test, including a target elsewhere within the app root.

### 5. P3 — Request-validation errors do not follow the shared error contract

**Location:** `backend/app.py:18–29`, app exception setup around `backend/app.py:85`.

Pydantic request errors use FastAPI's default `detail` list, while the shared HTTP contract promises `{detail: string}`. Missing/both input fields, invalid copy stages, and oversized field values therefore have a different response shape from the app's explicit errors.

**Reproduction:** An authenticated `POST /api/jobs` with `{}` returns HTTP 422 with a list-valued `detail`.

**Suggested fix:** Add a `RequestValidationError` handler that produces a concise string without echoing the submitted HTML; assert the contract for invalid job/copy bodies.

## Areas with no identified blocker

The existing implementation provides the expected five-scene shape validation, exact original index preservation, description limits, three-call retry ceiling, per-call timeout, bounded server retry hints, auth-key rotation, one format repair, explicit source snapshot reuse, token/Origin checks, single active job, safe generic unexpected errors, and guarded artifact filenames. Clipboard ctypes signatures include pointer-sized handles, and writes transfer ownership only after `SetClipboardData` succeeds. These were reviewed without invoking the real clipboard. Current tests do not exercise the actual Gemini SDK boundary or real Windows clipboard ownership; those remain integration checks for the root agent.

**Current verdict:** Accepted for the reviewed backend scope. All five original findings are resolved, 47 tests pass, and no new actionable issue was identified in the fixes or native TLS configuration.
