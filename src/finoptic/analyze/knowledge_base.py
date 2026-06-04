"""Light RAG knowledge base of FinOps best-practice snippets.

These short, accurate strings ground the LLM (and the deterministic fallback) so that
generated runbooks cite real remediation practice rather than hallucinating procedure.
Snippets are keyed by the waste ``category`` emitted by the detect stage.
"""

from __future__ import annotations

from collections.abc import Iterable

from finoptic.domain import CATEGORIES

#: Best-practice snippets keyed by waste category. Kept concise and provider-neutral
#: so they read well inside a prompt and inside a deterministic runbook line.
KB: dict[str, list[str]] = {
    "orphaned_storage": [
        "Unattached block storage (EBS/managed disks) bills at full provisioned "
        "capacity even with zero I/O; delete it once confirmed detached.",
        "Snapshot the volume before deletion so the action stays reversible, then "
        "remove the live volume to stop the per-GB-month charge.",
        "Tag volumes with an owner and a TTL so orphans are caught at creation time "
        "instead of accumulating as untracked spend.",
    ],
    "idle_compute": [
        "Stopped instances still incur charges for attached EBS volumes and any "
        "Elastic IPs; rightsize or terminate rather than leaving them parked.",
        "Sustained low CPU and near-zero network is a strong idle signal; downsize "
        "the instance family or move the workload to a smaller/spot tier.",
        "Schedule non-production compute to auto-stop outside business hours to cut "
        "run-hours without deleting the resource.",
    ],
    "unassociated_network": [
        "Unassociated Elastic IPs / public IPs are billed hourly precisely because "
        "they are not attached; release them to stop the charge immediately.",
        "Reserve static IPs only when an external dependency pins the address; "
        "otherwise prefer dynamic allocation to avoid idle reservation cost.",
    ],
    "idle_network": [
        "NAT gateways bill an hourly base charge plus per-GB processing even at zero "
        "traffic; delete idle gateways or consolidate to a shared egress path.",
        "Replace always-on NAT for low-traffic subnets with VPC endpoints (gateway "
        "endpoints for S3/DynamoDB are free) to remove the standing cost.",
    ],
    "idle_database": [
        "A managed database with zero connections over the observation window is a "
        "stop/snapshot candidate; snapshot then stop to retain data at storage cost.",
        "Rightsize over-provisioned database instance classes and storage to match "
        "the observed working set before paying for unused headroom.",
        "Idle non-production databases should be stopped on a schedule; stopped "
        "instances avoid compute charges while retaining the snapshot.",
    ],
    "stale_snapshot": [
        "Snapshots whose source volume no longer exists are rarely needed; verify "
        "against backup policy, then delete to reclaim per-GB-month storage.",
        "Apply a snapshot lifecycle/retention policy so backups age out automatically "
        "instead of accumulating indefinitely.",
        "Very old snapshots (months) outside a defined retention window are prime "
        "cleanup targets once compliance requirements are confirmed.",
    ],
}


def retrieve(categories: Iterable[str]) -> list[str]:
    """Return de-duplicated best-practice snippets for the given categories.

    Unknown categories are ignored. Order is stable: categories appear in the
    canonical order defined by ``KB``, and within a category in declared order.
    """
    wanted: set[str] = set()
    for category in categories:
        if isinstance(category, str) and category in KB:
            wanted.add(category)

    snippets: list[str] = []
    seen: set[str] = set()
    for category, items in KB.items():
        if category not in wanted:
            continue
        for snippet in items:
            if snippet not in seen:
                seen.add(snippet)
                snippets.append(snippet)
    return snippets


# Defensive sanity check: every KB key is a recognised domain category.
assert set(KB).issubset(CATEGORIES), "knowledge_base keys must be valid CATEGORIES"
