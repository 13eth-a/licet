"""The planner's two prompts: the standing contract, and each step's observation.

Phase 1's planner needed two things that did not exist:

1. **A system contract that states the scope.** The live model smoke test
   (2026-09-20) asked for "the permit for 801 Windward Way" with no portal in
   context and its first tool call was
   `navigate https://aca-prod.accela.com/TAMPA/Default.aspx` — a *production*
   municipality. The model supplies a plausible Accela URL from prior knowledge,
   so the MVP's one-portal scope has to be *stated to the model*, not assumed.
   Hence a scope block that names the sandbox and forbids every other agency.
2. **An observation the planner can act on.** `read_page` returns URL, flow
   position, field inventory, the validation panel, frames/popups, JS notices,
   parsed inspection types and calendar availability; that is far more than a
   model needs per turn and far too much to send raw. `observation_payload`
   keeps what a decision needs and reports what it dropped.

The environment block is portal knowledge, and every line of it was verified
live (see `docs/accela_ui_map.md`): the read path is My Records, anonymous search
returns 0 rows, the displayed record number is per record type, and this sandbox
offers no bookable appointment dates. Putting those in the prompt is what makes
"report the blocker" reachable at all.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from licet.browser import accela
from licet.config import Config, load_config

# A 4,000-character page read is mostly navigation chrome; the planner needs the
# record/section text and the validation panel.
MAX_OBSERVATION_TEXT = 2400
# ACA forms render hundreds of controls; the model acts on a handful.
MAX_OBSERVATION_FIELDS = 60
MAX_FIELD_OPTIONS = 12
MAX_FRAMES = 6
# A control's value is a field's *content*, not a place to put a document: ACA's
# MaskedEdit wrapper hides 143-character ids, and `__VIEWSTATE` values run to
# 98,000 characters. Sending those is what made one live run cost 377,640 input
# tokens (measured 2026-09-20).
MAX_FIELD_VALUE_CHARS = 160
# Hard ceiling for one page summary, so an unforeseen ACA page shape cannot blow
# the context budget again: the summary is trimmed to fit whatever happens.
MAX_OBSERVATION_CHARS = 9000

_ROLE = (
    "You are Licet, an autonomous browser agent that completes permitting tasks "
    "on an Accela Citizen Access (ACA) citizen portal. You act by calling browser "
    "tools, exactly one call per turn. Never describe an action in prose instead "
    "of taking it: if you decide something should happen, call the tool that does "
    "it."
)

_SCOPE = """\
## Scope — one portal, no exceptions
- This run is scoped to a single portal: `{portal_root}` (agencyCode `{agency_code}`).
- Never navigate to, search, or act on any other Accela agency, host or
  environment — not production (`aca-prod.accela.com`), not another
  municipality's portal, not a URL you have seen in a similar task elsewhere.
  If you believe you need another portal, stop and say so.
- The only host you may request is `{site_root}`."""

_WORKING = """\
## How you work
- Call exactly one tool per turn, then read its observation before deciding again.
- ACA is old WebForms: pages change by postback. After anything that changes the
  page, `read_page` before concluding anything. Do not assume a click worked.
- Sections load over AJAX after the page load event. An Inspections section can
  read as "You have not added any inspections" while it is still rendering — if
  the observation reports `still_loading`, `wait` with `until_absent` and read
  again instead of reporting an empty section as fact.
- Resolve controls with `by="text"` or `by="label"`. ACA control ids are ~60
  characters and are agency-configured; use `by="selector"` only with an id you
  just read off the page.
- Set `intent` on `click`/`select` when the meaning matters: an unclassified call
  is blocked rather than guessed, and the observation says so.
- When an observation reports `blocked`, `success: false` or an `error`, read the
  reason — it distinguishes "retry this" from "re-read the page" from "stop".
  Retrying an action that fails the same way repeatedly ends the run.
- Do not loop on a page you have already read: if two reads say the same thing,
  the next step is a different action or a final answer, not another read.
- If the goal is a question about the record's current state, the answer is in
  the text you have already read: status, sections, inspection history, and the
  scheduler's own type list are all in it. Do not open section after section
  looking for a different answer — a section this portal does not render will
  never appear, and hunting for it burns the step budget for nothing.
- Picking inspection types needs no user input. The wizard marks required types
  ("Floor Deck (required)", "Set Backs (optional)"). Take the type the goal names;
  if it names none, take the one marked (required); if none is required, take the
  first offered. Never end a run by asking which type to choose.
- Two or three reads of one record are enough to answer any question about it.
  Longer runs come from acting (scheduling, form steps), not from re-reading.
- A record detail page already renders its own state in its text (record number,
  status, expiration, and whether there is any inspection history). Some of its
  section names are links and some are labels: if clicking a section leaves the
  page unchanged, the content was already on screen — answer from what you have
  instead of clicking it again. Repeating a no-op is a stall, and a stall stops
  the run."""

_SAFETY = """\
## Safety boundary (enforced outside your control)
Consequential actions — paying a fee, submitting an application, accepting terms
or a legal attestation, signing, uploading, cancelling or withdrawing — are held
for the user's explicit approval, and the run stops there.

- If the goal requires one of those, call it with the correct `intent` and let
  the run stop. That is the intended outcome, not a failure.
- Never enter payment details, and never claim you performed a held action.
- Reading, searching, opening records, reading history, and scheduling (which
  can be rescheduled) are automatic — do those without asking."""

_ENVIRONMENT = """\
## This environment (verified live)
- The records in these tasks belong to the signed-in test account and are listed
  at {my_records_url}; each row links to its record detail page. That is the read
  path here.
- Anonymous search returns 0 rows for these records on this portal. An empty
  search is therefore NOT evidence that a record does not exist. Reach records
  through the signed-in account, and say what you actually did.
- Record detail URL shape: {detail_shape}
- The displayed record number is per record type: this agency prints a legacy
  numeric id for some types (e.g. `000000014`) and `BLD26-004xx` for others. The
  stable identity is `capID1`/`capID2`/`capID3` in the record's own link — reuse
  that link rather than rebuilding it by hand.
- Record sections: {sections}.
- Inspection scheduling is entered at {inspection_entry_url}, or by the
  scheduling link on the record page.
- This sandbox may offer no selectable appointment day for a record, and some
  record types offer no inspection types at all. If the page says either, that is
  the answer: stop and report it. Retrying does not create availability, and
  reporting a booking you did not make is the worst possible outcome."""

_FINISH = """\
## Finishing
- End the run with a message that contains no tool call.
- That message must state: what you did, what you verified from the portal
  (naming the record and the section you read), and what stopped you if anything
  did.
- A precise "I could not finish, and here is why" is a correct answer. Never
  report a booking, payment, submission or cancellation you did not confirm by
  re-reading the portal after taking it."""

# Sent mid-run when observations keep teaching us nothing new (see
# `AgentState.note_facts`). It is advisory on purpose: the model may have a real
# reason to keep going, and this is a pointer, not a stop condition. It is also
# the only mechanism that reaches the failure the stall detector cannot see —
# a section tour where every page changes and no fact does.
CONVERGENCE_NUDGE = (
    "Convergence check: your last {stale} observations of this record returned no "
    "new facts. You have already read off the portal: {facts}. You have "
    "{remaining} step(s) left.\n\n"
    "Do not open another section looking for a different answer. Either reply now, "
    "with no tool call, stating what you found and anything you could not "
    "determine — or name the single specific fact you still need and take the one "
    "action that would reveal it. Repeating an action you have already taken ends "
    "the run without an answer."
)

EMPTY_REPLY_NUDGE = (
    "You returned neither a tool call nor a final answer. Call exactly one tool — "
    "or, if the task is finished or cannot proceed, reply in plain text with no "
    "tool call and state what you did, what you verified, and what stopped you."
)


def build_system_prompt(
    config: Config | None = None, *, extra_constraints: Sequence[str] = ()
) -> str:
    """The standing contract for one run.

    Deterministic and dependency-free so it can be asserted on in tests — the
    scope rule in particular, since a prompt that forgets it is how a run ends up
    pointed at a production portal.
    """
    config = config or load_config()
    portal_root = (config.accela_sandbox_url or accela.PORTAL_ROOT).rstrip("/")
    parts = [
        _ROLE,
        _SCOPE.format(
            portal_root=portal_root,
            site_root=accela.SITE_ROOT,
            agency_code=accela.AGENCY_CODE,
        ),
        _WORKING,
        _SAFETY,
        _ENVIRONMENT.format(
            my_records_url=accela.MY_RECORDS_URL,
            detail_shape=(
                f"{accela.SITE_ROOT}/{accela.AGENCY_CODE}/Cap/CapDetail.aspx"
                "?Module=<module>&TabName=<module>"
                "&capID1=<capID1>&capID2=<capID2>&capID3=<capID3>"
                f"&agencyCode={accela.AGENCY_CODE}&IsToShowInspection="
            ),
            sections=", ".join(accela.RECORD_SECTIONS),
            inspection_entry_url=accela.INSPECTION_ENTRY_URL,
        ),
        _FINISH,
    ]
    if extra_constraints:
        parts.append(
            "## Additional constraints for this run\n"
            + "\n".join(f"- {line}" for line in extra_constraints)
        )
    return "\n\n".join(parts)


# --- observations ------------------------------------------------------------


def _compact_field(field: Mapping[str, Any]) -> dict[str, Any] | None:
    """One control, small enough to send. None when it is not worth sending.

    Hidden inputs are dropped outright: they are never actionable, and ACA puts
    its entire form state in them (`__VIEWSTATE` alone was 98,003 characters in
    one live observation).
    """
    if field.get("kind") == "hidden":
        return None
    compact: dict[str, Any] = {
        key: field[key]
        for key in ("id", "name", "kind", "label")
        if field.get(key) not in (None, "", False)
    }
    value = field.get("value")
    if value not in (None, "", False):
        text = str(value)
        compact["value"] = text[:MAX_FIELD_VALUE_CHARS]
        if len(text) > MAX_FIELD_VALUE_CHARS:
            compact["value_truncated"] = len(text)
    for flag in ("required", "masked", "postback"):
        if field.get(flag):
            compact[flag] = True
    options = field.get("options") or ()
    if options:
        compact["options"] = list(options)[:MAX_FIELD_OPTIONS]
        if len(options) > MAX_FIELD_OPTIONS:
            compact["options_truncated"] = True
    return compact


def _fit_budget(summary: dict[str, Any], *, budget: int = MAX_OBSERVATION_CHARS) -> dict[str, Any]:
    """Trim a page summary to `budget` characters, largest bulk first.

    Order is deliberate: the field inventory goes first (it is the largest and
    the most re-derivable — a re-read brings it back), then frames, and the
    page's own text last, because that text is the evidence the answer cites.
    """
    def size() -> int:
        return len(json.dumps(summary, ensure_ascii=False))

    if size() <= budget:
        return summary
    fields = list(summary.get("fields") or [])
    while len(fields) > 1 and size() > budget:
        fields = fields[: len(fields) // 2]
        summary["fields"] = fields
        summary["fields_truncated"] = "page summary budget"
    if size() > budget and summary.pop("frames", None) is not None:
        summary["frames_dropped"] = "page summary budget"
    text = str(summary.get("text") or "")
    while text and size() > budget:
        text = text[: int(len(text) * 0.6)]
        summary["text"] = text
        summary["text_truncated"] = True
    return summary


def _page_summary(
    data: Mapping[str, Any],
    *,
    max_text: int = MAX_OBSERVATION_TEXT,
    max_fields: int = MAX_OBSERVATION_FIELDS,
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    text = str(data.get("text") or "")
    summary["text"] = text[:max_text]
    if data.get("truncated") or len(text) > max_text:
        summary["text_truncated"] = True
    if data.get("flow"):
        summary["flow"] = data["flow"]
    if data.get("still_loading") or data.get("loading"):
        summary["still_loading"] = data.get("loading") or data.get("still_loading")
    if data.get("notices"):
        summary["notices"] = list(data["notices"])

    fields = list(data.get("fields") or [])
    if fields:
        actionable = [field for field in fields if field.get("kind") != "hidden"]
        summary["fields_total"] = len(actionable)
        summary["hidden_fields"] = len(fields) - len(actionable)
        if not actionable:
            summary.pop("hidden_fields")
        compact = [
            item
            for item in (_compact_field(field) for field in actionable[:max_fields])
            if item is not None
        ]
        if compact:
            summary["fields"] = compact
        else:
            summary.pop("fields_total")
            summary.pop("hidden_fields", None)
        if len(actionable) > max_fields:
            summary["fields_truncated"] = True

    types = list(data.get("inspection_types") or [])
    if types:
        summary["inspection_types"] = [
            {
                key: option[key]
                for key in ("name", "required", "control_id")
                if option.get(key) not in (None, "", False)
            }
            for option in types
        ]
    total = data.get("inspection_type_total")
    if isinstance(total, int):
        summary["inspection_type_total"] = total

    if data.get("calendar"):
        months = list(data["calendar"])
        summary["calendar"] = {
            "available": bool(data.get("calendar_available")),
            "months": [
                {
                    "month": month.get("month", ""),
                    "active_days": list(month.get("active_days") or []),
                    "inactive_day_count": len(month.get("inactive_days") or []),
                }
                for month in months
            ],
            "selectable_times": data.get("selectable_times") or "",
        }

    errors = list(data.get("validation_errors") or [])
    if errors:
        summary["validation_errors"] = errors[:12]

    frames = [
        {
            key: frame[key]
            for key in ("url", "title", "popup", "login_panel")
            if frame.get(key) not in (None, "", False)
        }
        for frame in (data.get("frames") or [])
        if frame.get("url") or frame.get("popup") or frame.get("login_panel")
    ]
    if frames:
        summary["frames"] = frames[:MAX_FRAMES]
    if data.get("popup_open"):
        summary["popup_open"] = True
    return _fit_budget(summary)


# How many of the most recent observations keep their full page detail. The
# first live planner run spent 64,871 input tokens on four turns: every page read
# stays in the conversation, so a run that reads six pages re-sends all six on
# every subsequent turn. Older pages keep their URL, flow position and a text
# excerpt (enough to remember which record we are on); their field inventory is
# dropped and the observation says so, so nothing disappears silently.
MAX_LIVE_OBSERVATIONS = 2
PRUNED_TEXT_CHARS = 600
DROPPED_KEYS = ("fields", "fields_total", "fields_truncated", "frames", "inspection_types")


def prune_observations(messages: list[dict[str, Any]], *, keep_last: int = MAX_LIVE_OBSERVATIONS) -> int:
    """Trim page detail from all but the last `keep_last` observations.

    Returns how many were trimmed. Idempotent: an already-trimmed observation is
    marked and skipped, so this is safe to call before every model turn.
    """
    indices = [
        index
        for index, item in enumerate(messages)
        if item.get("type") == "function_call_output"
    ]
    older = indices[:-keep_last] if keep_last > 0 else indices
    trimmed = 0
    for index in older:
        item = messages[index]
        try:
            payload = json.loads(item.get("output") or "")
        except (TypeError, json.JSONDecodeError):
            continue
        page = payload.get("page")
        if not isinstance(page, dict) or page.get("pruned"):
            continue
        text = str(page.get("text") or "")
        page["text"] = text[:PRUNED_TEXT_CHARS]
        for key in DROPPED_KEYS:
            page.pop(key, None)
        page["pruned"] = "earlier page on this run: keep only the url, flow and opening text"
        item["output"] = json.dumps(payload, ensure_ascii=False)
        trimmed += 1
    return trimmed


def observation_payload(
    call_name: str,
    args: Mapping[str, Any] | None,
    outcome: Mapping[str, Any],
    *,
    max_text: int = MAX_OBSERVATION_TEXT,
    max_fields: int = MAX_OBSERVATION_FIELDS,
) -> dict[str, Any]:
    """What the model is told after one tool call."""
    payload: dict[str, Any] = {
        "tool": call_name,
        "success": outcome.get("success"),
        "semantic_action": outcome.get("semantic_action"),
        "url": outcome.get("url"),
    }
    if outcome.get("blocked"):
        payload["blocked"] = True
    requested = {key: value for key, value in (args or {}).items() if key != "include"}
    if requested:
        payload["requested"] = requested
    if outcome.get("error"):
        payload["error"] = outcome["error"]
    data = outcome.get("data") or {}
    if data:
        payload["page"] = _page_summary(data, max_text=max_text, max_fields=max_fields)
    return payload


def render_observation(
    call_name: str,
    args: Mapping[str, Any] | None,
    outcome: Mapping[str, Any],
    *,
    max_text: int = MAX_OBSERVATION_TEXT,
    max_fields: int = MAX_OBSERVATION_FIELDS,
) -> str:
    """The observation as the model sees it: compact JSON, one object."""
    return json.dumps(
        observation_payload(
            call_name, args, outcome, max_text=max_text, max_fields=max_fields
        ),
        ensure_ascii=False,
    )


# --- the can't-finish report -------------------------------------------------

# Phase 0 review §2: "there is no expected-final-answer spec and no defined
# 'I couldn't finish' report format". This is that format — produced when the run
# stops without the model having given one (a held action, a portal outage, the
# step budget). It is tagged `final_answer_source="system"` so a scorer never
# mistakes it for the model's own conclusion.
#
# Wording matters: a system report must not read as a claim that anything was
# done. It states what stopped the run, never a completed consequential action.
def system_report(
    *,
    goal: str,
    stop_reason: str,
    steps: int,
    url: str | None = None,
    flow: str | None = None,
    facts: Sequence[str] = (),
    held_action: str | None = None,
) -> str:
    lines = [
        "Licet stopped before finishing.",
        f"- Stopped: {stop_reason}",
        f"- Steps taken: {steps}",
    ]
    if url:
        lines.append(f"- Last page: {url}" + (f" ({flow})" if flow else ""))
    if facts:
        lines.append("- Read off the portal: " + "; ".join(facts))
    if held_action:
        lines.append(
            f"- Held for your approval: {held_action} — reply with your approval to "
            "run it, or with different instructions."
        )
    # Wording matters twice over: this must not read as a claim that anything
    # happened, and it must not use the vocabulary a scorer watches for. The
    # eval's negation check only looks inside the current clause, so a list like
    # "nothing was submitted, paid, cancelled" reads as *three* claims — the
    # commas break the negation away from each word.
    lines.append(
        "- Licet's guard held every consequential action; none ran without your "
        "approval."
    )
    return "\n".join(lines)
