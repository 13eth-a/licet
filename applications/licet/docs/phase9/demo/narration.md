# Optional narration — three minutes

**0:00–0:15** Municipal permitting involves repetitive work across fragmented legacy portals. Licet takes a goal and works through the existing browser interface, while keeping observations, decisions and permissions separate.

**0:15–0:27** The request is to prepare permit 000000014 for its next inspection, without paying or signing anything. This evidence comes from Accela's test sandbox, in plan-only mode.

**0:27–0:50** Licet locates the permit and independently verifies its identity. It reads the overview and interprets the structured evidence. The sequence shown here is a readable replay of the recorded semantic trace.

**0:50–1:13** It reads inspection history and examines the full offered catalog. The portal marks Brycer Inspection History as required, providing evidence for the next-inspection decision.

**1:13–1:36** It opens the calendar and verifies that it still belongs to the same permit. The observed September-to-November window has no active dates. Cost and signature requirements are also undisclosed.

**1:36–1:54** The result is partial success, with no mutation. Licet reports the observed limit instead of inventing a booking. This is a successful investigation and safe stop, not completed scheduling.

**1:54–2:12** Separately, controlled tests exercise the full scheduling path: execute, independently re-read the resulting inspection, and verify its state. That evidence uses simulated portal I/O. It is not a real Accela booking.

**2:12–2:22** Policy is deterministic. The no-payment constraint is enforced, legal attestation is prohibited, and live or unknown-environment mutations are blocked.

**2:22–2:32** Recovery is bounded. It re-observes and validates state before continuing, and never blindly repeats an uncertain mutation.

**2:32–2:45** The architecture separates goal planning, policy, semantic capabilities, browser execution and verification. The recorded semantic path is deterministic; this demonstration does not claim a model inference experiment.

**2:45–3:00** LicetBench's official offline core run meets all 250 expected outcomes, with zero unsafe fixture outcomes. These results establish regression coverage, not broad live reliability. The benchmark and live evidence remain separately labeled.
