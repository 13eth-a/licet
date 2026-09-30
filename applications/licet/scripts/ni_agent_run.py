"""run the licet planner loop against the live null island sandbox"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from licet.agent.model import ModelError, build_model  # noqa: E402
from licet.agent.planner import Planner  # noqa: E402
from licet.browser import accela  # noqa: E402
from licet.browser.dispatcher import ToolDispatcher  # noqa: E402
from licet.browser.solari_client import SolariSession  # noqa: E402
from licet.config import load_config  # noqa: E402
from licet.eval.harness import RunRecord, build_cases, score_runs  # noqa: E402
from licet.logging.logger import RunLogger  # noqa: E402
from licet.safety.policy import Environment, environment_from_url  # noqa: E402

LOG_DIR = Path("logs/ni_agent")
# the only host a run may touch
SANDBOX_HOSTS = ("aca-test.accela.com",)


def out(message: str = "") -> None:
    print(message, flush=True)


def sandbox_problem(url: str | None) -> str | None:
    """why this target must not be driven, or none when it is the sandbox"""
    if not url:
        return "ACCELA_SANDBOX_URL is not set"
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        valid = parsed.scheme == "https" and host in SANDBOX_HOSTS and parsed.port in (None, 443) and not parsed.username and not parsed.password
    except ValueError:
        valid = False
    if not valid:
        return f"{url!r} is not on {', '.join(SANDBOX_HOSTS)} — refusing to drive it"
    return None


def _environment_badge(url: str | None) -> str:
    """prominent badge for the demo: sandbox vs live — read only vs unknown"""
    env = environment_from_url(url)
    if env is Environment.SANDBOX:
        return "SANDBOX  — mutations allowed only here (reversible schedule)"
    if env is Environment.LIVE_READ_ONLY:
        return "LIVE — READ ONLY  — no mutations will be executed"
    return "UNKNOWN  — no mutations will be executed (fail-closed)"


def _friendly_result(stop: str | None, has_permit: bool) -> tuple[str, str]:
    """human result title + one-line explanation for the final card"""
    mapping: dict[str | None, tuple[str, str]] = {
        "goal_completed": (
            "COMPLETION DECLARED",
            "The model declared the goal complete. Check the observed evidence and scorer before treating the outcome as verified.",
        ),
        "approval_required": (
            "SAFE STOP — Approval Required",
            "An action was held for approval. This does not establish the outcome of earlier actions.",
        ),
        "missing_information": (
            "INCOMPLETE — Missing Information",
            "Licet stopped because required information was not available.",
        ),
        "ambiguous_record": (
            "SAFE STOP — Ambiguous Record",
            "Licet could not confidently identify the correct permit and made no selection.",
        ),
        "no_valid_action": (
            "INCOMPLETE — No Valid Action",
            "Licet found no permitted next action.",
        ),
        "portal_unavailable": (
            "PORTAL UNAVAILABLE",
            "Licet could not continue because the portal was unavailable or the session expired. Any uncertain earlier submission needs reconciliation.",
        ),
        "max_steps_exceeded": (
            "INCOMPLETE — Step Limit",
            "Licet reached its step budget before finishing. No additional mutation was attempted.",
        ),
        "repeated_action_failed": (
            "SAFE STOP — No Progress",
            "Licet detected repeated failures with no progress and stopped.",
        ),
    }
    if stop in mapping:
        return mapping[stop]
    if has_permit:
        return ("INCOMPLETE", "Licet stopped before the requested outcome was established.")
    return ("SAFE STOP", "Licet stopped safely without selecting a record.")


def _mutation_evidence(run) -> str:
    """summarize trace evidence without turning a page read into outcome proof"""
    from licet.safety.risk_levels import changes_state

    entries = [e for e in (run.actions or [])
               if changes_state(e.get("semantic_action") or "") and not e.get("blocked")]
    if not entries:
        return "No state-changing action is recorded as dispatched in this trace."
    last = entries[-1]
    action = last.get("semantic_action")
    if last.get("success") and (last.get("verification") or {}).get("success"):
        return f"{action}: a post-action page read succeeded. This alone does not prove the requested final state."
    return f"{action}: resulting state is not established by this trace. Reconcile before retrying."


def _print_permit_card(run) -> None:
    permit = run.permit
    if permit is None:
        out("  ┌─ Active permit ─────────────────────")
        out("  │ No verified permit — Licet did not select a record.")
        out("  └─────────────────────────────────────")
        return
    status = permit.status or "—"
    ptype = permit.permit_type or "—"
    out("  ┌─ Active permit ─────────────────────")
    out(f"  │ {permit.permit_id} — {ptype} — {status}")
    if getattr(permit, "address", None):
        out(f"  │ {permit.address}")
    if permit.ref is not None:
        out(f"  │ {permit.ref.as_key()}")
    if getattr(permit, "sections", None):
        out(f"  │ Sections: {', '.join(permit.sections)}")
    out("  └─────────────────────────────────────")


def _print_trace(run, verbose: bool = False) -> None:
    actions = run.actions or []
    if not actions:
        out("  Trace: (no browser actions)")
        return
    out("  Trace: (semantic intent → outcome)")
    for idx, entry in enumerate(actions, 1):
        semantic = entry.get("semantic_action") or entry.get("name") or "—"
        target = entry.get("target") or entry.get("url") or entry.get("observation", {}).get("url", "") or ""
        display = str(target).strip()
        if len(display) > 56:
            display = display[:53] + "..."
        blocked = entry.get("blocked")
        success = entry.get("success")
        if blocked:
            auth = entry.get("authorization") or {}
            decision = auth.get("decision")
            icon = "◷ HOLD" if decision == "require_approval" else "✗ BLOCKED"
        elif success:
            icon = "✓"
        else:
            icon = "✗"
        label = str(semantic).upper() if semantic else "—"
        extra = ""
        if verbose:
            prov = (entry.get("resolution") or {}).get("provenance")
            if prov:
                extra = f"  [{prov}]"
        out(f"    {idx:02}  {label:<24} {display:<56} {icon}{extra}")
        if blocked or not success:
            reason = (entry.get("error") or {}).get("message") or (entry.get("authorization") or {}).get("reason", "")
            if reason:
                short = str(reason).strip().replace("\n", " ")
                if len(short) > 120:
                    short = short[:117] + "..."
                out(f"        ↳ {short}")


def _print_policy_card(run) -> None:
    pending = getattr(run.state, "pending_approval", None) if run.state else None
    held_entry = next(
        (a for a in (run.actions or []) if a.get("blocked") and (a.get("authorization") or {}).get("decision") == "require_approval"),
        None,
    )
    if pending is None and held_entry is None:
        return
    if pending is not None:
        action = pending.action
        reason = pending.reason
        details = pending.details or {}
    else:
        auth = held_entry.get("authorization") or {}
        action = auth.get("action") or held_entry.get("semantic_action") or "—"
        reason = auth.get("reason") or ""
        details = {}
    out("  ┌─ Policy decision ──────────────────")
    out(f"  │ Action: {action}")
    try:
        from licet.safety.policy import ACTION_RISKS, normalize_action  # lazy: terminal only

        risk = ACTION_RISKS.get(normalize_action(action))
        risk_label = risk.name.replace("_", " ").title() if risk else "Unknown"
    except Exception:
        risk_label = "Unknown"
    env_badge = _environment_badge(run.state.current_url if run.state else None)
    out(f"  │ Risk: {risk_label}")
    out(f"  │ Environment: {env_badge.split(' —')[0].strip()}")
    out(f"  │ Decision: Confirmation Required")
    if reason:
        short = str(reason).strip().replace("\n", " ")
        if len(short) > 96:
            short = short[:93] + "..."
        out(f"  │ Reason: {short}")
    if details.get("url"):
        out(f"  │ Page: {details['url']}")
    out("  │ This held action was not dispatched; see the trace for earlier actions.")
    out("  └─────────────────────────────────────")


def parse_args(argv: list[str]) -> dict[str, Any]:
    options: dict[str, Any] = {
        "prompt_ids": [],
        "goals": [],
        "all": False,
        "score": False,
        "list": False,
        "skip_login": False,
        "allow_non_sandbox": False,
        "max_steps": None,
        "verbose": False,
        "help": False,
    }
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in ("--help", "-h"):
            options["help"] = True
            index += 1
        elif token == "--prompt-id":
            options["prompt_ids"].append(argv[index + 1])
            index += 2
        elif token == "--goal":
            options["goals"].append(argv[index + 1])
            index += 2
        elif token == "--max-steps":
            options["max_steps"] = int(argv[index + 1])
            index += 2
        elif token in ("--all", "--score", "--list", "--skip-login", "--allow-non-sandbox", "--verbose"):
            options[token.lstrip("-").replace("-", "_")] = True
            index += 1
        else:
            raise SystemExit(f"unknown argument {token!r} (try --list or --help)")
    return options


def selected_goals(options: dict[str, Any]) -> list[tuple[str, str]]:
    """(case id, goal) pairs to run, in the order they will be run"""
    cases = {case.prompt_id: case for case in build_cases()}
    chosen: list[tuple[str, str]] = []
    if options["all"]:
        chosen += [(case.prompt_id, case.prompt) for case in build_cases()]
    for prompt_id in options["prompt_ids"]:
        case = cases.get(prompt_id)
        if case is None:
            raise SystemExit(f"unknown prompt id {prompt_id!r} — try --list")
        if all(prompt_id != existing for existing, _ in chosen):
            chosen.append((prompt_id, case.prompt))
    for goal in options["goals"]:
        chosen.append(("adhoc", goal))
    return chosen


async def main(argv: list[str]) -> int:
    options = parse_args(argv)
    if options.get("help"):
        out(__doc__ or "")
        out("Options: --list --prompt-id ID --goal TEXT --all --score --skip-login --allow-non-sandbox --max-steps N --verbose --help")
        return 0
    if options["list"]:
        for case in build_cases():
            out(f"{case.prompt_id} [{case.category:>9} / {case.expects:<13}] {case.prompt}")
        return 0

    goals = selected_goals(options)
    if not goals:
        out(__doc__)
        return 2

    config = load_config()
    problem = sandbox_problem(config.accela_sandbox_url or accela.PORTAL_ROOT)
    out("=== Licet planner — live run ===")
    out(f"Target: {config.accela_sandbox_url or accela.PORTAL_ROOT}")
    out(f"Environment: {_environment_badge(config.accela_sandbox_url or accela.PORTAL_ROOT)}")
    out(f"Model: {config.agent_model} (fallback {config.fallback_model})")
    out(f"Cases: {len(goals)}  max_steps={options['max_steps'] or config.max_steps}")
    if problem:
        if not options["allow_non_sandbox"]:
            out(f"REFUSING: {problem}")
            return 2
        out(f"WARNING: {problem} (--allow-non-sandbox given)")
    out()

    try:
        model = build_model(config)
    except ModelError as exc:
        out(f"model unavailable: {exc}")
        return 2

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    runs: dict[str, dict[str, Any]] = {}

    session = SolariSession()
    await session.start()
    try:
        for case_id, goal in goals:
            out(f"── {case_id}: {goal}")
            client = await session.client()
            logger = RunLogger(f"{stamp}_{case_id}", log_dir=LOG_DIR)
            dispatcher = ToolDispatcher(client, logger=logger)

            if not options["skip_login"]:
                login = await client.authenticate()
                if not login.ok:
                    detail = login.error.message if login.error else "unknown"
                    out(f"  [FAIL] Login could not complete: {detail}")
                    out("  Result: PORTAL UNAVAILABLE — no permit was accessed.")
                    out(f"  Log: {logger.path}")
                    out()
                    runs[case_id] = {"error": f"login failed: {detail}"}
                    continue
                if login.data.get("authenticated") is False:
                    out(f"  [WARN] Login did not leave the login page ({login.url})")

            planner = Planner(
                model,
                dispatcher,
                config=config,
                max_steps=options["max_steps"],
            )
            try:
                run = await planner.run(goal, prompt_id=case_id)
            except ModelError as exc:
                out(f"  [FAIL] Model call failed mid-run: {exc}")
                out("  Result: MODEL UNAVAILABLE — run interrupted; inspect the log and reconcile any earlier submission before retrying.")
                runs[case_id] = {"error": f"model failure: {exc}"}
                continue
            except Exception as exc:  # noqa: BLE001 - one case failing is a result
                out(f"  [FAIL] {type(exc).__name__}: {exc}")
                traceback.print_exc()
                runs[case_id] = {"error": f"{type(exc).__name__}: {exc}"}
                continue

            runs[case_id] = run.as_dict()
            out(
                f"  [done] stop={run.stop_condition_value} steps={run.steps} "
                f"actions={len(run.actions)} tokens={run.input_tokens}+{run.output_tokens} "
                f"{run.latency_ms / 1000:.1f}s"
            )
            _print_permit_card(run)
            _print_trace(run, verbose=bool(options["verbose"]))
            _print_policy_card(run)
            title, explanation = _friendly_result(run.stop_condition_value, run.permit is not None)
            out(f"  ┌─ Result: {title} ─────────────────")
            out(f"  │ {explanation}")
            out(f"  │ {_mutation_evidence(run)}")
            out("  └─────────────────────────────────────")
            out("  Answer:")
            for line in (run.final_answer or "(none)").splitlines():
                out(f"    {line}")
            out(f"  Log: {logger.path}  (full trace + screenshots)")
            out()
    finally:
        await session.close()

    runs_path = LOG_DIR / f"{stamp}_runs.json"
    runs_path.write_text(json.dumps(runs, indent=2, ensure_ascii=False))

    if options["score"]:
        out("=== scoring ===")
        cases = {case.prompt_id: case for case in build_cases()}
        records = {
            case_id: RunRecord(
                prompt_id=case_id,
                final_answer=entry.get("final_answer", ""),
                actions=entry.get("actions", []),
                stop_condition=entry.get("stop_condition"),
                steps=int(entry.get("steps", 0)),
                model=entry.get("model", ""),
            )
            for case_id, entry in runs.items()
            if case_id in cases
        }
        scored_cases = [cases[case_id] for case_id in records if case_id in cases]
        if scored_cases:
            report = score_runs(scored_cases, records)
            for result in report["results"]:
                out(f"[{'PASS' if result['passed'] else 'FAIL'}] {result['prompt_id']} {result['prompt'][:64]}")
                for key, item in (result.get("criteria") or {}).items():
                    if not item["passed"]:
                        out(f"        FAIL {key}: {item['detail']}")
            out(f"scored {report['passed']}/{report['total']}")
        else:
            out("no scored cases (ad-hoc goals are not part of the fixture suite)")

    out(f"runs written to {runs_path}")
    out(f"re-score offline: .venv/bin/python scripts/ni_eval_fixtures.py --score {runs_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
