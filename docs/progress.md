# Implementation progress — docs/implementation.md

Plan approved in conversation. Worktree inapplicable: directory is not a Git repo and Git CLI is unavailable.

| Boundary | Producer / consumer | Review |
| --- | --- | --- |
| Backend / frontend | HTTP contract in implementation.md | Fixed before implementation |
| Backend / launcher | server CLI + health/session/shutdown + .runtime/server.json | Fixed before implementation |
| Serializer / compressor | serialized scene input → five-line narration | Per-market index/rate checks and greeting-aware sentence limits |

- Backend: complete; 95 tests across serializer, narration validation, retries, SDK boundary, storage/clipboard, jobs and API. Narration review findings fixed with regression tests; old archives remain readable.
- Frontend: complete; 12 tests, typecheck/build and actual Edge browser checks pass. Five-line narration display and newline-excluded character counts verified.
- Launcher: complete; lifecycle, port collision, duplicate reuse, warm-start freshness and Korean/spaced-path checks passed.
- Integration: actual Gemini, native clipboard and Edge browser checks passed. Backend, frontend and final application reviews accepted with no open actionable findings.

Ruling: The machine Python executable moved mid-run from the per-user install to C:/Python314 while its standard library stayed behind. A project-local official Python NuGet 3.14.7 distribution was installed at .tools/python/tools, verified against NuGet catalog SHA512, and .venv upgraded to reference it. Avoid modifying the machine installation.

Ruling: certifi failed on the environment's trusted interception chain; ssl.create_default_context also rejected its missing Authority Key Identifier. Native Windows truststore validates the chain successfully; use truststore SSLContext with CERT_REQUIRED and hostname verification, never disable verification.

Latest narration integration: outputs/20260911_135153_351794_a3a37a55 completed with gemini-3.5-flash-lite on the first call, five narration lines, final clipboard verified equal to compressed.txt. The 378-character sample gives an advisory length warning and measures 61.53 seconds with Korean Heami speech at default rate. Sparse mixed-flow input was also evaluated. Detailed evidence and historical integration records are in docs/verification.md.
