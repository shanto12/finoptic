"""CLI smoke tests — the offline scan/sample commands are a first-class deliverable."""

from __future__ import annotations

from pathlib import Path

import pytest

from finoptic.cli import main

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "sample_data"


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "finoptic" in capsys.readouterr().out


def test_cli_sample(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--no-color", "sample"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "17 findings" in out
    assert "Total recoverable" in out


def test_cli_scan_file(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--no-color", "scan", str(SAMPLE_DIR / "aws_cur_sample.csv")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "findings" in out
    assert "AWS_" in out
