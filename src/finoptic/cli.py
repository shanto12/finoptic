"""FinOptic command-line interface.

Offline-friendly: ``scan`` and ``sample`` run the detection pipeline without a server or
database, printing findings and the projected savings. ``serve`` launches the API.
"""

from __future__ import annotations

import argparse
import sys

from finoptic import __version__
from finoptic.detect import detect, summarize
from finoptic.ingest import parse, parse_aws_cur, parse_azure_billing
from finoptic.remediate import attach_remediations

_SEV_COLOR = {"critical": "\033[1;31m", "high": "\033[31m", "medium": "\033[33m", "low": "\033[36m"}
_RESET = "\033[0m"


def _print_report(records: list, *, use_color: bool = True) -> int:
    findings = detect(records)
    attach_remediations(findings)
    agg = summarize(findings, resource_count=len(records))

    if not findings:
        print(f"Scanned {len(records)} resources — no waste detected. ✅")
        return 0

    print(f"\nFinOptic scan — {len(records)} resources, {len(findings)} findings\n" + "-" * 72)
    for f in findings:
        color = _SEV_COLOR.get(f.severity, "") if use_color else ""
        reset = _RESET if use_color else ""
        print(f"{color}[{f.severity.upper():8}]{reset} {f.title}")
        print(
            f"            ${f.monthly_cost:,.2f}/mo  (${f.annual_savings:,.2f}/yr)  · {f.rule_id}"
        )
        if f.remediation and f.remediation.cli_commands:
            print(f"            ↳ {f.remediation.cli_commands[0]}")
    print("-" * 72)
    print(
        f"Total recoverable: ${agg['total_monthly_waste']:,.2f}/mo  "
        f"(${agg['total_annual_savings']:,.2f}/yr)\n"
    )
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    with open(args.file, "rb") as fh:
        _provider, records = parse(fh.read(), args.file)
    return _print_report(records, use_color=not args.no_color)


def _cmd_sample(args: argparse.Namespace) -> int:
    from finoptic.service import _read_sample  # reuse sample resolution

    records: list = []
    aws = _read_sample("aws_cur_sample.csv")
    azure = _read_sample("azure_billing_sample.json")
    if aws:
        records.extend(parse_aws_cur(aws))
    if azure:
        records.extend(parse_azure_billing(azure))
    if not records:
        print("No bundled sample data found.", file=sys.stderr)
        return 1
    return _print_report(records, use_color=not args.no_color)


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("finoptic.api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finoptic", description="FinOptic — Cloud Cost Optimizer")
    parser.add_argument("--version", action="version", version=f"finoptic {__version__}")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Scan a billing export file")
    p_scan.add_argument("file", help="Path to an AWS CUR CSV or Azure billing JSON export")
    p_scan.set_defaults(func=_cmd_scan)

    p_sample = sub.add_parser("sample", help="Scan the bundled sample data")
    p_sample.set_defaults(func=_cmd_sample)

    p_serve = sub.add_parser("serve", help="Run the FinOptic API + dashboard")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=_cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # --no-color is global; ensure it exists on the namespace for subcommands.
    if not hasattr(args, "no_color"):
        args.no_color = False
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
