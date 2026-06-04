# FinOptic Sample Data

Realistic billing exports that exercise **every** detection rule in the FinOptic rule set,
plus healthy resources that must **not** trigger. These files are the source of truth that the
test-suite asserts against — the totals below are exactly what the detection engine computes.

| File | Format | Provider | Rows |
| --- | --- | --- | --- |
| `aws_cur_sample.csv` | AWS Cost & Usage Report (CSV, RFC4180) | aws | 20 line items |
| `azure_billing_sample.json` | Azure Cost Management export (JSON array) | azure | 12 records |

Billing period for every record: **2026-05-01 → 2026-05-31**.

The `resource_tags` and `state` columns of the CSV contain JSON, wrapped in double quotes with
inner quotes doubled (`""`) per RFC4180, so they parse cleanly with `csv.DictReader`. In the
Azure file the detection signals live under the `properties` object.

---

## AWS resources (`aws_cur_sample.csv`)

| Resource ID | Type | Triggering state signals | Verdict | Rule / Severity | Monthly $ |
| --- | --- | --- | --- | --- | ---: |
| `vol-0a1b2c3d4e5f6a7b` | ebs_volume | `attachment_state=available` | **WASTE** | AWS_EBS_UNATTACHED / high | 42.00 |
| `vol-0f9e8d7c6b5a4321` | ebs_volume | `attachment_state=available` | **WASTE** | AWS_EBS_UNATTACHED / high | 18.50 |
| `vol-0123456789abcdef0` | ebs_volume | `attachment_state=attached` | healthy | — | 64.00 |
| `i-0a1b2c3d4e5f6a7b1` | ec2_instance | `power_state=running, avg_cpu_percent=1.2, avg_network_bytes=12000` | **WASTE** | AWS_EC2_IDLE / high | 138.24 |
| `i-0f1e2d3c4b5a69788` | ec2_instance | `power_state=running, avg_cpu_percent=0.8, avg_network_bytes=4096` | **WASTE** | AWS_EC2_IDLE / high | 71.50 |
| `i-0998877665544332f` | ec2_instance | `power_state=running, avg_cpu_percent=45.0, avg_network_bytes=9000000` | healthy | — | 138.24 |
| `i-0aa11bb22cc33dd44` | ec2_instance | `power_state=stopped` (not running) | healthy | — | 5.00 |
| `eipalloc-0a1b2c3d4e5f6a7b` | elastic_ip | `associated=false` | **WASTE** | AWS_EIP_UNASSOCIATED / medium | 3.60 |
| `eipalloc-0f9e8d7c6b5a4321` | elastic_ip | `associated=true` | healthy | — | 3.60 |
| `prod-analytics-replica` | rds_instance | `connections_avg=0` | **WASTE** | AWS_RDS_IDLE / high | 412.00 |
| `staging-postgres-main` | rds_instance | `connections_avg=120` | healthy | — | 380.00 |
| `arn:…/app/legacy-api/abc123def4567890` | load_balancer | `request_count=0, healthy_targets=0` | **WASTE** | AWS_ELB_IDLE / medium | 22.50 |
| `arn:…/app/checkout-svc/def456abc7890123` | load_balancer | `request_count=1500000, healthy_targets=4` | healthy | — | 24.30 |
| `snap-0a1b2c3d4e5f6a7b` | snapshot | `age_days=210, source_volume_exists=false` | **WASTE** | AWS_SNAPSHOT_STALE / low | 8.40 |
| `snap-0f9e8d7c6b5a4321` | snapshot | `age_days=95, source_volume_exists=true` (age>90) | **WASTE** | AWS_SNAPSHOT_STALE / low | 4.20 |
| `snap-0123456789abcdef0` | snapshot | `age_days=10, source_volume_exists=true` | healthy | — | 6.00 |
| `nat-0a1b2c3d4e5f6a7b` | nat_gateway | `active_connection_count=0` | **WASTE** | AWS_NATGW_IDLE / medium | 32.85 |
| `nat-0f9e8d7c6b5a4321` | nat_gateway | `active_connection_count=1850` | healthy | — | 34.20 |
| `vol-0deadbeef00112233` | ebs_volume | `attachment_state=attached` | healthy | — | 12.00 |
| `i-0c0ffee1234567890` | ec2_instance | `avg_cpu_percent=3.0` but `avg_network_bytes=250000` (≥50000) | healthy | — | 95.00 |

**AWS waste subtotal: 10 findings · $753.79 / month**

> Edge cases intentionally kept healthy: a **stopped** EC2 instance (rule needs `running`), and a
> low-CPU EC2 instance whose **network traffic ≥ 50 000 bytes** (so it fails the AND condition).

---

## Azure resources (`azure_billing_sample.json`)

The `<sub>` prefix below is `/subscriptions/8f4e1c2a-9b3d-4e7f-a1c2-3d4e5f6a7b8c/resourceGroups/<rg>/providers`.

| Resource (ARM leaf name) | Type | Triggering state signals | Verdict | Rule / Severity | Monthly $ |
| --- | --- | --- | --- | --- | ---: |
| `disks/orphaned-data-disk-01` | managed_disk | `attachment_state=available` | **WASTE** | AZURE_DISK_UNATTACHED / high | 56.32 |
| `disks/app-os-disk-prod` | managed_disk | `attachment_state=attached` | healthy | — | 48.00 |
| `publicIPAddresses/unused-lb-ip` | public_ip | `associated=false` | **WASTE** | AZURE_PUBLIC_IP_UNASSOCIATED / low | 3.65 |
| `publicIPAddresses/gateway-ip-prod` | public_ip | `associated=true` | healthy | — | 3.65 |
| `virtualMachines/legacy-batch-vm` | vm | `power_state=deallocated` | **WASTE** | AZURE_VM_DEALLOCATED / medium | 140.16 |
| `virtualMachines/web-frontend-01` | vm | `power_state=running, avg_cpu_percent=38.0` | healthy | — | 175.20 |
| `snapshots/db-backup-2025q4` | disk_snapshot | `age_days=240` | **WASTE** | AZURE_SNAPSHOT_STALE / low | 11.20 |
| `snapshots/weekly-os-snap` | disk_snapshot | `age_days=10` | healthy | — | 7.50 |
| `disks/detached-temp-disk` | managed_disk | `attachment_state=available` | **WASTE** | AZURE_DISK_UNATTACHED / high | 24.00 |
| `virtualMachines/analytics-vm-02` | vm | `power_state=deallocated` | **WASTE** | AZURE_VM_DEALLOCATED / medium | 96.40 |
| `publicIPAddresses/active-app-ip` | public_ip | `associated=true` | healthy | — | 4.10 |
| `snapshots/archive-snap-old` | disk_snapshot | `age_days=400` | **WASTE** | AZURE_SNAPSHOT_STALE / low | 9.80 |

**Azure waste subtotal: 7 findings · $341.53 / month**

---

## Rule coverage

All 11 detection rules are triggered at least once:

| # | Rule ID | Triggered by | Count |
| --- | --- | --- | ---: |
| 1 | AWS_EBS_UNATTACHED | `vol-0a1b…`, `vol-0f9e…` | 2 |
| 2 | AWS_EC2_IDLE | `i-0a1b…`, `i-0f1e…` | 2 |
| 3 | AWS_EIP_UNASSOCIATED | `eipalloc-0a1b…` | 1 |
| 4 | AWS_RDS_IDLE | `prod-analytics-replica` | 1 |
| 5 | AWS_ELB_IDLE | `…/legacy-api/…` | 1 |
| 6 | AWS_SNAPSHOT_STALE | `snap-0a1b…` (age+orphan), `snap-0f9e…` (age) | 2 |
| 7 | AWS_NATGW_IDLE | `nat-0a1b…` | 1 |
| 8 | AZURE_DISK_UNATTACHED | `orphaned-data-disk-01`, `detached-temp-disk` | 2 |
| 9 | AZURE_PUBLIC_IP_UNASSOCIATED | `unused-lb-ip` | 1 |
| 10 | AZURE_VM_DEALLOCATED | `legacy-batch-vm`, `analytics-vm-02` | 2 |
| 11 | AZURE_SNAPSHOT_STALE | `db-backup-2025q4`, `archive-snap-old` | 2 |

---

## Expected totals (test assertions)

| Metric | Value |
| --- | ---: |
| **Expected number of findings** | **17** |
| **Expected total monthly waste** | **$1,095.32** |
| **Expected total annual waste** (monthly × 12) | **$13,143.84** |

Breakdown by severity (monthly $):

| Severity | Monthly $ | Findings |
| --- | ---: | ---: |
| critical | 0.00 | 0 |
| high | 762.56 | 6 |
| medium | 295.51 | 4 |
| low | 37.25 | 7 |
| **Total** | **1,095.32** | **17** |

Breakdown by provider (monthly $):

| Provider | Monthly $ | Findings |
| --- | ---: | ---: |
| aws | 753.79 | 10 |
| azure | 341.53 | 7 |

Breakdown by category (monthly $):

| Category | Monthly $ | Findings |
| --- | ---: | ---: |
| orphaned_storage | 140.82 | 4 |
| idle_compute | 446.30 | 4 |
| unassociated_network | 7.25 | 2 |
| idle_network | 55.35 | 2 |
| idle_database | 412.00 | 1 |
| stale_snapshot | 33.60 | 4 |
| **Total** | **1,095.32** | **17** |

> **Note on `monthly_cost`:** the detection engine reads `monthly_cost` straight from each record,
> which equals the unblended/USD cost for the May 2026 billing period above. `annual_savings` is
> `monthly_cost × 12` (per `FindingResult.annual_savings`).
