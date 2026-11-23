"""The workbench package: the FMOL executive demo (INIT-001).

An add-on in the same pattern as `scripts/plants_demo/`: it reads the existing NetBox
fabric and writes only its own artifacts, so the core workshop and its tests are
untouched.

Nothing here imports a device library at module import time. The EOS and FRR drivers
import what they need inside the functions that use it, so `import workbench` works on
a laptop with no lab running and no dependencies beyond the standard library.
"""

from __future__ import annotations

from .contracts import (
    AUTONOMY_LEVELS,
    DIFF_FORMATS,
    EVENT_KINDS,
    PLATFORMS,
    Actual,
    AuditEntry,
    ContractError,
    Drift,
    Intent,
    Plan,
    Preview,
    Skip,
    may_act_unattended,
    new_id,
    utc_now,
)

__all__ = [
    "AUTONOMY_LEVELS",
    "DIFF_FORMATS",
    "EVENT_KINDS",
    "PLATFORMS",
    "Actual",
    "AuditEntry",
    "ContractError",
    "Drift",
    "Intent",
    "Plan",
    "Preview",
    "Skip",
    "may_act_unattended",
    "new_id",
    "utc_now",
]

__version__ = "0.1.0"
