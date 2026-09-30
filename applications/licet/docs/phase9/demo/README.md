# Historical presentation draft — superseded 2026-09-28

The requested actual browser recording is now complete. See [browser-recording.md](../browser-recording.md) for the fresh session, video, screenshots, and observed outcome. This directory’s card-based video is retained only as a historical draft.

`licet-demo-evidence-draft.mp4` is a **3:00, 1920×1080, H.264, silent captioned evidence walkthrough**. It includes a replay of the verified semantic trace as readable cards. It is **not a successful new browser recording**. That distinction is visible on the trace screens. The PNGs in `screenshots/` are presentation/evidence cards, not photographs of a dashboard.

## Contents and provenance

- Goal, inspection/result cards: `../screenshots/01_flagship_semantic_trace.txt`, recorded sandbox output from the established plan-only run.
- Benchmark: `../final-licetbench-20260926.md`, official clean-revision core report. 250/250 expected outcomes; zero unsafe fixture outcomes; 40/40 fixture submissions verified. The 145 frozen SUCCESS labels include ten correct recovery refusals.
- Controlled execution: `tests/test_phase5_integration.py::test_discovery_understanding_selection_execution_and_verification` rerun successfully during preparation. No real booking is represented by this test.
- Safety and recovery: existing Phase 6/7 controlled evidence; labels remain visible.
- Architecture: a documentation diagram, not an observed browser action.

No footage from different sessions is spliced together and described as one continuous run. No payment balance, booking, or cost/signature certainty is invented.

## Capture attempts this session

1. Local native-video-context attempt: authentication failed. Raw video remains in ignored `logs/phase9-demo-capture/browser-raw.webm`; excluded from the submission video.
2. User-approved Solari recording: authenticated, but the recording observer raised `'dict' object has no attribute 'name'` after navigation. Product stopped with zero mutations. This observer bug is fixed; two regressions prove that dictionary/typed calls and screenshot failures cannot change returned product results. The authenticated replay is private in `logs/phase9-demo-capture-retry/browser-replay.ndjson`; excluded from submission assets. It may contain test-account details.

The user subsequently approved replacement recording and requested another take; that authorization was used for the completed 2026-09-28 capture. Do not count either failed capture as flagship acceptance or overwrite the established five-run evidence.

## Remaining work

- Completed: fresh actual browser footage and six same-session screenshots, visually reviewed on 2026-09-28.
- Narration, if desired; this draft uses captions and has no audio track.
- Confirm the actual submission duration/format and publish/upload destination. Nothing has been uploaded.
- Actual sandbox booking remains unverified and conditional on available dates plus sufficient cost/signature evidence. The accepted live demo is a plan-only investigation and observed safe stop.
- A release tag is optional and not created here. Source freeze `9a6a424` and official benchmark already exist; new media/docs are separate from that historical frozen evidence.

## Reproduce

- Render cards: `.venv/bin/python scripts/phase9_render_demo.py` (local Chrome required).
- Capture: `scripts/phase9_capture_demo.py` records the authenticated Solari browser locally through Chrome screencast. Server-side replay recording is disabled. It is always plan-only; never add `--execute` for this demo. Follow the user’s existing recording authorization.
- Large raw authenticated media stays in ignored `logs/`; do not commit or upload it blindly.
