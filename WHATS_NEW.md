# What's New

This file tracks the notable changes shipped in each build so we have a simple running release history for Track&Trace.

## How to use this file
- Add a new entry at the top for each build we push to GitHub or deploy to Render.
- Keep entries focused on user-visible behavior, production fixes, and operational changes.
- Include the git commit so we can match a Render deploy back to the exact code.

## Build History

### 2026-03-14 - Unreleased
Title: Add dedicated Wan Hai Track&Trace adapter

What changed:
- Replaced the generic Wan Hai carrier fallback with a dedicated `WanHaiAdapter`.
- Added browser-driven Wan Hai tracking through the VIP cargo query page and booking detail popup.
- The adapter now extracts:
  - latest status
  - latest event time
  - location
  - vessel and voyage
  - loading/discharge route
  - estimated arrival date when booking detail is available
- Added `beautifulsoup4` as a project dependency for HTML parsing.

Why it mattered:
- Wan Hai was previously recognized by name but not actually integrated reliably.
- The old generic JSON/web mode was being blocked by anti-bot responses and returning HTML instead of usable tracking data.
- The new adapter was validated locally against a real shipment and returned live status successfully.

Files:
- `.env.example`
- `README.md`
- `pyproject.toml`
- `src/shipment_sync/carriers/registry.py`
- `src/shipment_sync/carriers/wan_hai.py`

### 2026-03-14 - `095d3bc`
Title: Write last-checked as ClickUp date field

What changed:
- Fixed `Last T&T Update` / last checked so it writes to ClickUp as a real date-time custom field.
- The app now sends Unix milliseconds with `value_options.time=true`, which matches ClickUp date field expectations.

Why it mattered:
- Successful Track&Trace checks can now reliably stamp the shipment-level "last checked" field.
- This supports auditing which shipments were actually checked automatically.

Files:
- `src/shipment_sync/clickup_client.py`

### 2026-03-14 - `445b12b`
Title: Protect last-checked field from snapshot misconfiguration

What changed:
- Added a guard so the app will not try to use the same ClickUp field for both `last checked` and the Track&Trace snapshot hash.
- If both env vars point to the same field, the snapshot field is ignored safely.

Why it mattered:
- Prevents writing text snapshot data into a ClickUp date/time field.
- Makes the "last checked" setup safer during rollout.

Files:
- `src/shipment_sync/config.py`

### 2026-03-12 - `43a5142`
Title: Only comment when Track&Trace changes

What changed:
- Added snapshot-based change detection for shipment updates.
- The app can now avoid posting repeated Track&Trace comments when the carrier result has not changed.
- Added support for an optional "No change" comment mode.
- Extended API and sync output to report unchanged shipments.

Why it mattered:
- Reduces noise in ClickUp.
- Makes automated updates easier to review at shipment level.

Files:
- `.env.example`
- `README.md`
- `src/shipment_sync/api.py`
- `src/shipment_sync/clickup_client.py`
- `src/shipment_sync/config.py`
- `src/shipment_sync/main.py`
- `src/shipment_sync/models.py`
- `src/shipment_sync/sync.py`

### 2026-03-12 - `3883633`
Title: Use published Playwright 1.58.0 image

What changed:
- Updated the Docker base image to the published Playwright `1.58.0` runtime.
- Pinned the Python Playwright package to the matching `1.58.0` version.

Why it mattered:
- Fixed the browser/runtime mismatch that was breaking MSC tracking in Render.
- Brought the deployed image in line with the browser binaries expected by Playwright.

Files:
- `Dockerfile`
- `pyproject.toml`

### 2026-03-12 - `7bdeb46`
Title: Use matching Playwright 1.58.2 runtime

What changed:
- Attempted to align the Docker image and Python package on Playwright `1.58.2`.

Outcome:
- This build did not become the final production baseline because the `v1.58.2-noble` image tag was not available.
- It is kept here as part of the release trail because it informed the final `1.58.0` fix.

Files:
- `Dockerfile`
- `pyproject.toml`

### 2026-03-12 - `7404cda`
Title: Align Playwright package with Docker image

What changed:
- Updated the Playwright Python dependency and container configuration to reduce browser/runtime mismatch issues.

Why it mattered:
- This was the first step toward stabilizing Render browser automation for MSC.

Files:
- `Dockerfile`
- `pyproject.toml`

### 2026-03-12 - `1422c81`
Title: Merge remote placeholder commit

What changed:
- Merged the local production work with the remote repository history so Render could build from GitHub cleanly.

Why it mattered:
- Unblocked the first real GitHub-backed Render deployment.

### 2026-03-12 - `446ebdb`
Title: Initial shipment sync app and production deployment setup

What changed:
- Added the Track&Trace application codebase and CLI/API entrypoints.
- Added Render deployment assets and production docs.
- Added Docker support for hosted deployment.
- Added local launch helpers and the initial marketing site structure.

Why it mattered:
- This was the first production-ready baseline for the app.

Key files:
- `Dockerfile`
- `render.yaml`
- `README.md`
- `docs/PRODUCTION_TRACK_TRACE.md`
- `docs/RENDER_LAUNCH_CHECKLIST.md`
- `pyproject.toml`
