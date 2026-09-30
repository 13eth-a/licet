# Screenshot evidence set (2026-09-26)

Each item is captured from **real product output**, not a mock. `.txt` files are
the raw captures; `.png` files are the same text rendered through headless Chrome
in a terminal style. The separate connected React workspace in `ui/` is not shown
in this evidence set; the portal trace below is captured from the CLI session.

| # | Checklist item | Artifact | Class |
|---|---|---|---|
| 1 | Agent executing + permit state + safe-stop result | `01_flagship_semantic_trace.txt` | REAL ACCELA |
| 2 | LicetBench summary | `02_licetbench_summary.txt` | AUTOMATED TESTS |
| 3 | Safety block | `03_safety_block.txt` | AUTOMATED TESTS |
| 4 | Recovery trace | `04_recovery_trace.txt` | CONTROLLED TEST |
| 5 | Architecture diagram | `05_architecture.txt` | DOCUMENTATION |

PNGs: `01_…png` … `05_…png` (rendered from the `.txt` captures).

## What is _not_ here, and why

- **"Successful inspection result"** — there is no real one. The dated capacity
  survey found no citizen-reachable slot for an account-owned record, so no real
  booking exists to photograph. The
  schedule → re-read → `VERIFIED_SUCCESS` sequence is controlled-test evidence
  only and must be shown labeled **SIMULATED PORTAL I/O**. See
  `03_safety_block.txt` for the zero-mutation side and the
  [claims audit](../claims-audit.md).
- **Live payment screen** — never rendered on an owned record; the `$74.50`
  figure is fixture data. Do not present it as a live balance.

## Regenerate

```bash
cd <repo root>

# 1. Real flagship trace (live, read-only; ~3 min; needs .env credentials)
.venv/bin/python scripts/ni_phase5_acceptance.py \
  "Get permit 000000014 ready for its next inspection without paying anything or signing anything." \
  --attempts 1 --timeout 90 | tee docs/phase9/screenshots/01_flagship_semantic_trace.txt

# 2. Benchmark summary (offline, deterministic)
.venv/bin/python -m licetbench run --suite core --repeat 5 --shuffle --seed 19 \
  --no-regression-log | tee docs/phase9/screenshots/02_licetbench_summary.txt

# 3. Safety block (offline policy replay)
.venv/bin/python scripts/phase6_adversarial_replay.py | tee docs/phase9/screenshots/03_safety_block.txt

# 4. Recovery trace (controlled simulation)
.venv/bin/python scripts/phase7_acceptance.py --output logs/phase9/phase7-acceptance.json \
  | tee docs/phase9/screenshots/04_recovery_trace.txt
```

Render PNGs from any capture (Google Chrome, headless):

```bash
render() { # <name>
  local txt="$1"
  local html="$(mktemp -d)/p.html"
  python - "$txt" > "$html" <<'PY'
import html, sys
text = open(sys.argv[1], encoding="utf-8").read()
print("<!doctype html><html><head><meta charset='utf-8'><style>"
      "body{background:#0b0e14;color:#e6e6e6;margin:0;padding:24px}"
      "pre{font:13px/1.45 'SF Mono',Menlo,Consolas,monospace;white-space:pre-wrap}"
      "</style></head><body><pre>" + html.escape(text) + "</pre></body></html>")
PY
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --headless --disable-gpu --hide-scrollbars \
    --window-size=1200,1600 --screenshot="${txt%.txt}.png" "file://$html" >/dev/null 2>&1
}
render docs/phase9/screenshots/01_flagship_semantic_trace.txt
render docs/phase9/screenshots/02_licetbench_summary.txt
render docs/phase9/screenshots/03_safety_block.txt
render docs/phase9/screenshots/04_recovery_trace.txt
render docs/phase9/screenshots/05_architecture.txt
```
