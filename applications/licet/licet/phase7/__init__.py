"""Phase 7 recovery, robustness, and failure-handling primitives.

Recovery is deliberately separate from browser execution and mutation policy:
this package decides *whether* a failed operation may be recovered, while the
existing dispatcher/executor decides *how* to perform safe reads and how to
reconcile consequential actions.
"""

from licet.phase7.portal import (
    PageIdentity,
    PortalFinding,
    PortalState,
    RecoveryRoute,
    audit_transient_identity_sources,
    identity_from_world,
    route_from_result,
    route_recovery,
    settled_browser_state,
)
from licet.phase7.recovery import (
    Checkpoint,
    Failure,
    FailureType,
    LoopDetector,
    PageFingerprint,
    ProgressKind,
    ProgressTracker,
    RecoveryBudgets,
    RecoveryController,
    RecoveryResult,
    RecoveryStats,
    StateConflict,
    TimeoutType,
    classify_failure,
)

__all__ = [
    "Checkpoint", "Failure", "FailureType", "LoopDetector", "PageFingerprint",
    "PageIdentity", "PortalFinding", "PortalState", "ProgressKind",
    "ProgressTracker", "RecoveryBudgets", "RecoveryController",
    "RecoveryResult", "RecoveryStats", "RecoveryRoute", "StateConflict",
    "TimeoutType", "audit_transient_identity_sources", "classify_failure",
    "identity_from_world", "route_from_result", "route_recovery",
    "settled_browser_state",
]
