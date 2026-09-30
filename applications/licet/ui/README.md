# Connected Licet workspace

This local workspace connects the Licet interface to the existing Python agent and the Accela sandbox account in the root `.env`. Its live views display real semantic events, browser observations, and final report evidence. The separate Booking replay view is explicitly offline and uses the regression harness.

## Start

From the repository root, run these in separate terminals:

```sh
.venv/bin/python scripts/licet_ui_bridge.py
npm run dev --prefix ui
```

Open `logs/ui-bridge/connect.html` in your browser. The private pairing page opens `http://127.0.0.1:8766/`, consumes its per-process key, and removes the key from the address bar. Alternatively, enter the key from `logs/ui-bridge/session-key.txt` in Settings. Restarting the bridge creates a new key. Accela credentials are never sent to the hosted UI.

Start an operation using a real permit number. Runs shows observed semantic events; Browser shows actual authenticated Accela frames; Permits shows inspection and calendar evidence from the completed report. Export trace downloads the current run data.

The bridge only accepts the exact hosted Licet origin or the local UI origin, the loopback Host header, and a matching pairing token. It cannot be used to read arbitrary files or enable execution. Every run is plan-only; no payment, signature, or booking is submitted. An outcome must come from the agent report, never a timer.

## Recording

```sh
LICET_UI_FILM_OUTPUT=logs/licet-connected-walkthrough .venv/bin/python scripts/record_connected_ui.py
.venv/bin/python scripts/phase9_encode_browser.py logs/licet-connected-walkthrough --screen-directory ui-screen
```

The recorder starts after local pairing, clicks the actual Run agent button, follows the same session's Accela frames, and returns to the observed outcome and calendar evidence. Footage and authentication-related artifacts stay under ignored `logs/`. Review the footage before any external upload.

The encoder saves a silent, full-length MP4 and a shorter MP4 with idle waits reduced. Both use the actual captured frames. The corresponding edits JSON records timing changes.

Set `LICET_RECORD_REPLAY=1` when recording to append the offline Booking replay view. The button calls the existing captured-calendar regression harness through the local bridge. It displays original versus injected calendar markup and the real executor's result over simulated browser I/O. Its September 2026 date and confirmation are fixture data, not a live appointment. The shortened export preserves the replay segment's reading time.

## Validate

```sh
npm run build --prefix ui
.venv/bin/python -m pytest -q tests/test_ui_bridge.py tests/test_phase9_capture_demo.py tests/test_scheduling_portal_replay.py
```
