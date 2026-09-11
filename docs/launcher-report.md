# Windows launcher report

`scripts/launch.ps1` is the single start/stop implementation behind `start.cmd` and `stop.cmd`.

- Start and stop operations are serialized with a project-specific named mutex.
- Existing servers are reused or stopped only after `/api/health` matches both `app: summary-to-video` and the recorded `instance_id`. Shutdown obtains the current session token and posts to `/api/shutdown`; the launcher never force-kills a process.
- The first free loopback port in 8765 through 8775 is used. `server.json` is written as soon as the hidden child starts so a slow startup cannot create duplicate children. A matching ready response is still required before reuse or browser opening. A live, launcher-owned child that misses the readiness deadline keeps its state record and produces a clear error.
- Project-local Node 24.21.0 and Python 3.14.7 are preferred. Missing runtimes are downloaded from the official Node and NuGet endpoints and verified against official SHA256/SHA512 metadata before extraction.
- The virtual environment is repaired when its recorded Python home moved or it no longer runs. Requirements, package-lock, and frontend source fingerprints in `.runtime` prevent repeat installs and builds when inputs are unchanged.
- Launcher and backend output are recorded under `.runtime`; interactive failures show the launcher log path.

## Validation

- Windows PowerShell parser: zero errors; `scripts/launch.ps1` has a UTF-8 BOM for PowerShell 5.1 Korean text handling.
- `tests/windows_smoke.py`: startup, duplicate reuse, authenticated graceful shutdown, and occupied-8765 fallback passed in 27.6 seconds. Its capture uses temporary files because a long-lived Windows descendant can retain anonymous pipe handles after PowerShell exits.
- An unchanged normal start preserved the Python dependency stamp, frontend dependency stamp, frontend build stamp, and `frontend/dist/index.html` timestamp and contents. This confirms the warm path did not reinstall or rebuild.
- A copied project at `C:\WORKS\런처 한글 경로 검사` passed `start.cmd`, moved-venv repair, health identity validation, duplicate reuse, and `stop.cmd`. The fixture was removed after the check.
- Main-project `server.json` is absent and ports 8765 through 8775 are left free after testing.

The runtime bootstrap download branches were not exercised because both verified project runtimes were already installed. Their code uses only the specified official URLs and validates archive hashes before replacing a tool directory.
