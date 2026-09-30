"""render readable evidence cards and assemble a captioned demo with ffmpeg"""
import asyncio
import html
import json
import subprocess
from pathlib import Path
from patchright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'logs/phase9-demo-capture/edited'
CSS='''*{box-sizing:border-box}body{margin:0;background:#101c29;color:#f6f4ef;font-family:Arial,sans-serif;width:1920px;height:1080px;padding:85px 110px}header{display:flex;justify-content:space-between;font-size:28px;letter-spacing:4px;color:#87ddcd}.tag{border:1px solid #526779;border-radius:30px;padding:12px 24px;letter-spacing:2px;font-size:22px}h1{font-size:78px;line-height:1.08;max-width:1600px;margin:85px 0 30px;font-weight:600;letter-spacing:-2px}p{font-size:36px;line-height:1.5;color:#c9d4dc;max-width:1550px}strong{color:#87ddcd}.grid{display:flex;gap:30px;margin-top:55px}.box{border-top:3px solid #87ddcd;background:#182939;padding:30px;flex:1;font-size:30px;line-height:1.5}.big{font-size:78px;display:block;color:#f6f4ef}footer{position:absolute;bottom:55px;left:110px;right:110px;color:#8ca1b3;font-size:24px;display:flex;justify-content:space-between}.flow{font-size:34px;line-height:2.3;margin-top:40px;color:#87ddcd}.note{color:#f5bf77}'''
CARDS=[
('01-title',15,'GOAL-DRIVEN PERMITTING','Legacy portals.\nOne clear goal.','Licet investigates a permit, plans the next supported step,\nand reports what the portal actually proves.','Accela sandbox · engineering prototype'),
('02-goal',12,'RECORDED SANDBOX EVIDENCE','“Get this permit ready for\nits next inspection.”','Permit <strong>000000014</strong> · 81 Commerce Ave<br>No payments. No signatures. No mutations enabled for this recording.','Recorded run evidence · text replay, not live browser footage'),
('03-discovery',23,'RECORDED TRACE · TEXT REPLAY','Find the right record.','<strong>✓ FIND_PERMIT</strong> — independently verified<br><strong>✓ READ_PERMIT_STATE</strong> — overview observed<br><strong>✓ DETERMINE_BLOCKERS</strong> — structured evidence interpreted','Source: screenshots/01_flagship_semantic_trace.txt · recorded 2026-09-27 UTC'),
('04-inspections',23,'RECORDED TRACE · TEXT REPLAY','Read history.\nResolve the requirement.','<strong>✓ READ_INSPECTIONS</strong> — history read<br><strong>✓ DETERMINE_NEXT_INSPECTION</strong><br>Brycer Inspection History · portal marks this type required','Recorded run: complete offered catalog, 18 of 18 types'),
('05-calendar',23,'RECORDED TRACE · TEXT REPLAY','Check availability.\nRespect what is unknown.','<strong>✓ Calendar opened and record identity verified</strong><br>No active dates in the observed Sep–Nov 2026 window.<br><span class="note">Cost and signature requirements were not disclosed.</span>','No booking made · plan-only capture · no live-browser footage in this draft'),
('06-result',18,'RECORDED SANDBOX RESULT','A verified boundary,\nnot an invented booking.','Required type: <strong>Brycer Inspection History</strong><br>No active dates in the observed Sep–Nov 2026 calendar.<br>Cost and signature requirements remain undisclosed.','Partial success · zero mutations · no booking claimed'),
('07-controlled',18,'SIMULATED PORTAL I/O','Schedule → re-read → verify.','The real planner and executor pass a controlled end-to-end test.<br>The resulting inspection state is independently read back.<br><span class="note">This is test evidence, not an Accela booking.</span>','tests/test_phase5_integration.py · verified in this preparation'),
('08-safety',10,'OFFLINE POLICY TESTS','Authority stays in code.','Live/unknown-environment mutations: <strong>blocked</strong>.<br>No-payment constraints: enforced.<br>Legal attestation: prohibited.','A plan-only run does not demonstrate a live payment attempt'),
('09-recovery',10,'CONTROLLED SIMULATION','Recover only to known state.','Detect stale or unsettled state → bounded re-read → validate identity.<br>Continue on fresh evidence; stop when uncertainty remains.<br>Never blindly replay an uncertain mutation.','Phase 7 acceptance: simulated external I/O'),
('10-architecture',13,'ARCHITECTURE','Reasoning proposes.\nPolicy governs.','<span class="flow">Goal → Planner → Policy → Semantic capability<br>→ Solari / Accela → Independent state read</span><br>Validated recovery returns to the planner.','The recorded semantic path is deterministic; no model inference claim'),
('11-benchmark',15,'LICETBENCH v1 · OFFLINE','Measured. Scoped. Reproducible.','<div class="grid"><div class="box"><span class="big">250 / 250</span>Expected outcomes</div><div class="box"><span class="big">0</span>Unsafe fixture outcomes</div><div class="box"><span class="big">40 / 40</span>Fixture submissions verified</div></div>','145 frozen SUCCESS labels include 10 recovery refusals; not 145 completed user goals'),
]

async def render():
 OUT.mkdir(parents=True,exist_ok=True)
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True)
  page=await browser.new_page(viewport={'width':1920,'height':1080},device_scale_factor=1)
  for name,duration,tag,title,body,note in CARDS:
   document=f'<html><head><style>{CSS}</style></head><body><header>LICET <span class="tag">{tag}</span></header><h1>{html.escape(title).replace(chr(10),"<br>")}</h1><p>{body}</p><footer><span>{note}</span><span>LICET / 2026</span></footer></body></html>'
   (OUT/(name+'.html')).write_text(document)
   await page.set_content(document)
   await page.screenshot(path=str(OUT/(name+'.png')))
  await browser.close()
 (OUT/'cards.json').write_text(json.dumps(CARDS,indent=2))

if __name__=='__main__':asyncio.run(render())
