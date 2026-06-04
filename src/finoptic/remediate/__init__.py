"""FinOptic remediation stage: turn findings into safe CLI / script remediations."""

from __future__ import annotations

from finoptic.remediate.cli_generator import (
    attach_remediations,
    build_remediation,
    render_script,
)

__all__ = ["build_remediation", "attach_remediations", "render_script"]
