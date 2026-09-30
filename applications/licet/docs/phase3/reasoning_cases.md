# Phase 3 interpretation decisions

the architecture review’s interpretation oracle, 2026-09-21. These are 30 worked decisions for
implementation/fixture generation to encode in golden tests, not claims that a Phase 3 engine has passed
30 tests. Unless a row states otherwise, record identity is verified, stated
observations are current, and unmentioned prerequisites are unknown. F = FACT,
I = INFERENCE, U = UNCERTAIN. “No supported blocker” never means “no blocker.”

## Status and applicability

| ID | Structured evidence | Required interpretation | Must not claim |
|---|---|---|---|
| S01 | Overview explicitly Issued; question=current status | F: portal status is Issued. Answer now; no additional sections needed. | Ready for inspection; all reviews passed |
| S02 | Submitted; configured expiration date predates as-of time; never issued | F: Submitted and displayed date. U: date applicability unknown if expiry is asked. | Expired permit blocker solely from date |
| S03 | Overview Issued; dated history explicitly expired yesterday; no renewal evidence | F: both source statements. Conflict affects current validity. Retrieve relevant renewal/history evidence. | Silently select Issued or Expired by scrape order |
| S04 | Raw inspection lifecycle Completed, result absent | F: inspection completed. U: outcome unknown; retrieve result if needed. | Passed or Failed |
| S05 | Permit status “Not Issued”; inspections “Not Approved” and “Not Scheduled” in separate attempts | Preserve all raw values. Permit status unmapped unless agency mapping exists; inspection outcome failure and lifecycle pending are distinct. | Issued, passed, or scheduled from substring matches |

## Inspection chronology

| ID | Structured evidence | Required interpretation | Must not claim |
|---|---|---|---|
| H01 | Rough Electrical attempt A failed Sept 18; B passed Sept 20; same requirement/unit; complete history | F: earlier failure, later pass. Earlier outcome no longer an unresolved failure. Separate conditions remain independent. | Current failed-inspection blocker from A |
| H02 | Failed A; same-scope B scheduled tomorrow | F: failure and scheduled follow-up. I: address cited correction before arranged visit if still outstanding. | Schedule another duplicate inspection |
| H03 | Failed A; later follow-up cancelled; complete relevant history | F: cancelled follow-up and unresolved failed outcome. I: confirm correction and consider another request. | Cancellation is a pass or a new failed inspection |
| H04 | Same type: Unit A failed, Unit B passed later | Keep separate scopes. F: Unit A failure remains. | Unit B's pass resolves Unit A |
| H05 | Same type failed and passed; missing event dates or truncated history | F: both outcomes. U: current/latest outcome unknown. Need attempt detail/history. | Last visible row is most recent |

## Failure and comments

| ID | Structured evidence | Required interpretation | Must not claim |
|---|---|---|---|
| F01 | Latest relevant attempt failed; linked comment “Enclose exposed junction box.” | F: failed result and exact instruction. I: address correction, then likely seek reinspection. U: correction completion and eligibility. | Junction box fixed; immediate/mandatory booking |
| F02 | Failed result; no comment | F: failure. U: reason unknown. Request linked attempt comments. | Invent cause or repair instructions |
| F03 | Linked comment “Okay to proceed after correction.”; outcome unknown | F: conditional wording. U: correction unspecified and completion unknown. | Unconditional permission or named correction |
| F04 | Passed result and current same-attempt comment “Correction Required” | Flag result/comment conflict; fetch clarification/detail. | Rewrite result to Failed, or declare ready |
| F05 | Comment describes electrical defect but has no attempt linkage; two failed trades | Quote as unassigned record note if relevant. U: inspection association unknown. | Attach comment to newest/nearest inspection |

## Blockers and requirements

| ID | Structured evidence | Required interpretation | Must not claim |
|---|---|---|---|
| B01 | Unpaid $74.50 balance; due date/gate absent | F: unpaid balance. Potential payment impediment, stage unknown. | Confirmed approval/reinspection gate |
| B02 | Due unpaid $74.50 plus current “Payment required before issuance” condition | Confirmed issuance gate with both evidence IDs; recommend resolving balance for issuance. Phase 3 executes nothing. | Payment must precede all inspections |
| B03 | No documents visible; coverage partial; no document requirements | U: document state incomplete; no supported missing-document blocker. | Revised plans missing/required |
| B04 | Explicit revised-plan requirement unsatisfied; current application under review | F: unmet plan requirement and review pending; link issuance gate only if portal states one. Multiple items preserved. | Review late or plan absence prevents all work |
| B05 | Active administrative hold explicitly blocks inspections; failed prerequisite; unpaid nongated fee | Rank explicit inspection hold first, failed prerequisite second; fee remains potential. Describe all three independently. | Fee is the highest required gate just because amount is known |

## Next actions and readiness

| ID | Structured evidence | Required interpretation | Must not claim |
|---|---|---|---|
| N01 | Scheduler offers Rough, Final, Optional Test; no explicit requirement list | F: offered types. U: required sequence unknown. No required scheduling candidate. | Offered minus history equals outstanding requirements |
| N02 | Explicit same-scope prerequisite Rough→Final; Rough passed; Final required/uncompleted; complete current relevant holds/requirements with no hold | Final is the next recorded required inspection for that scope. Readiness only within supplied explicit requirements, as-of time. | Appointment available, physical site prepared, action executed |
| N03 | Rough failed; paid fee; no dependency graph | I: correct cited issue and consider reinspection. U: authoritative sequence/eligibility unknown. | Fee payment alone makes permit ready |
| N04 | Two independent explicit unmet requirements: plan correction and issuance payment | Return both candidates, indicate no evidenced ordering, same priority if otherwise tied. | A unique operational next step |
| N05 | Record explicitly Expired; a future inspection remains scheduled | F: both. Potential stale scheduling/validity conflict. Recommend confirming eligibility/renewal requirement before relying on appointment. | Appointment proves valid permit; auto-cancel or auto-renew |

## Missing data, conflict, and isolation

| ID | Structured evidence | Required interpretation | Must not claim |
|---|---|---|---|
| U01 | Inspections explicitly empty, complete and not loading | F: no inspection entries shown. No missing requirement without requirement evidence. | No work required, or extraction failure |
| U02 | Fees tab unavailable; documents parse_failed; asks why stalled | Report known problems elsewhere, coverage uncertainty for these tabs; bounded targeted reads if material. | No fees/documents, or unavailable tab is a permit blocker |
| U03 | Current balance shows $74.50; payment receipt with unknown allocation/time | Preserve both; U: current reconciliation unknown; request ledger/allocation only if needed. | Receipt proves current balance zero |
| U04 | Correct record A snapshot includes a condition sourced from record B | Reject foreign-record fact and every dependent claim; request clean same-record state. | Transfer B's hold to A |
| U05 | All visited sections have no problems, but required inspection list unknown; readiness question | Partial answer: no problems observed in covered sections; readiness unknown. | Ready, no blockers, or complete understanding |

## Flagship worked answer

Input facts (all same verified record, current and source-linked):

- `overview.status`: Issued.
- `insp.A.result`: Corrections Required, latest relevant Rough Electrical attempt,
  Sept 18; complete relevant attempt history and known scope.
- `insp.A.comment`: “Enclose exposed junction box.” Linked to A.
- `fees.balance`: USD 74.50 unpaid, no payment gate/sequence evidence.
- No evidence that physical correction is completed or reinspection is scheduled.

Approved answer:

> The portal lists the permit as Issued. Its latest relevant Rough Electrical
> inspection did not pass on Sept 18. The inspector wrote, “Enclose exposed
> junction box.” The portal also shows a $74.50 unpaid balance.
>
> The likely next step is to address that correction, then request another Rough
> Electrical inspection if eligible. The available evidence does not establish
> that the correction is complete or that the balance must be paid before
> reinspection.

Underlying interpretation:

- Observed problem: Rough Electrical failed, supported by `insp.A.result`.
- Potential impediment: unpaid balance, supported by `fees.balance`; affected
  stage unknown. No confirmed payment gate.
- Conditional inferred action: correction → possible reinspection, premises
  `insp.A.result` and `insp.A.comment`; prerequisite completion unknown.
- No global “ready” judgment. No scheduling or payment action.

If the user asks only “what did the inspector say?”, quote the linked comment
and identify the attempt. Do not fetch fees or give a full blocker analysis.

## Evaluation cautions

Negative assertions are as important as expected claims. A response passes only
if it includes supported required content, excludes the forbidden promotion,
preserves uncertainty, cites the right record/entity, and requests no mutation.
A hedged phrase (“probably must pay”) still asserts an unsupported requirement.
A citation to a real fee fact does not entail a payment gate. Rephrasing a claim
cannot repair missing premises.
