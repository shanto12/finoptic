"""Ingest layer: parsing AWS CUR CSV and Azure billing JSON into NormalizedRecord."""

from __future__ import annotations

from pathlib import Path

import pytest

from finoptic.ingest import parse, parse_aws_cur, parse_azure_billing

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "sample_data"


@pytest.fixture(scope="module")
def aws_text() -> str:
    return (SAMPLE_DIR / "aws_cur_sample.csv").read_text()


@pytest.fixture(scope="module")
def azure_text() -> str:
    return (SAMPLE_DIR / "azure_billing_sample.json").read_text()


def test_aws_cur_parses_all_rows(aws_text: str) -> None:
    records = parse_aws_cur(aws_text)
    assert len(records) == 20
    assert all(r.provider == "aws" for r in records)
    assert all(r.resource_id for r in records)
    # tags/state must be dicts (parsed from embedded JSON columns)
    assert all(isinstance(r.state, dict) and isinstance(r.tags, dict) for r in records)


def test_azure_billing_parses_all_objects(azure_text: str) -> None:
    records = parse_azure_billing(azure_text)
    assert len(records) == 12
    assert all(r.provider == "azure" for r in records)
    assert all(r.resource_id.startswith("/subscriptions/") for r in records)


def test_parse_autodetects_provider(aws_text: str, azure_text: str) -> None:
    provider_aws, recs_aws = parse(aws_text, "aws_cur_sample.csv")
    provider_azure, recs_azure = parse(azure_text, "azure_billing_sample.json")
    assert provider_aws == "aws" and len(recs_aws) == 20
    assert provider_azure == "azure" and len(recs_azure) == 12


def test_parse_accepts_bytes(aws_text: str) -> None:
    provider, records = parse(aws_text.encode("utf-8"), "x.csv")
    assert provider == "aws" and len(records) == 20


def test_ingest_is_defensive_against_garbage() -> None:
    # Never raises; returns no records for unusable input.
    assert parse_aws_cur("") == []
    assert parse_azure_billing("not json at all") == []
    _provider, records = parse(b"\xff\xfe garbage", "weird.bin")
    assert isinstance(records, list)


def test_cost_is_coerced_to_float(aws_text: str) -> None:
    records = parse_aws_cur(aws_text)
    assert all(isinstance(r.monthly_cost, float) for r in records)
    assert sum(r.monthly_cost for r in records) > 0
