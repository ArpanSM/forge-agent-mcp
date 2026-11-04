from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from forge_agent_mcp.corpus.manifest import ManifestError, load_manifest, validate_manifest

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "corpus" / "manifest.json"


def _raw_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _case(raw: dict[str, Any], case_id: str) -> dict[str, Any]:
    return next(case for case in raw["cases"] if case["case_id"] == case_id)


def test_committed_manifest_has_the_fixed_corpus_shape() -> None:
    manifest = load_manifest(MANIFEST_PATH)

    assert manifest.schema_version == 1
    assert manifest.as_of == date(2026, 11, 1)
    assert manifest.watermark == "SYNTHETIC"
    assert len(manifest.carriers) == 8
    assert len(manifest.cases) == 16

    by_type = Counter(case.case_type for case in manifest.cases)
    by_split = Counter(case.split for case in manifest.cases)
    assert by_type == {"onboarding": 8, "invoice": 8}
    assert by_split == {"eval": 12, "held_out": 4}

    per_type: Counter[tuple[str, str]] = Counter(
        (case.carrier_id, case.case_type) for case in manifest.cases
    )
    assert len(per_type) == 16
    assert set(per_type.values()) == {1}
    assert len({carrier.carrier_id for carrier in manifest.carriers}) == 8


def test_committed_manifest_covers_every_required_hard_stop_and_eval_archetype() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    flags_by_code = {flag.code: flag for flag in manifest.flag_catalog}

    hard_stop_codes = {code for code, flag in flags_by_code.items() if flag.severity == "hard_stop"}
    assert {
        "watchlist_hit",
        "authority_inactive",
        "identity_mismatch",
        "insurance_expired",
        "insurance_below_minimum",
        "duplicate_invoice",
    } <= hard_stop_codes

    archetypes = {archetype for case in manifest.cases for archetype in case.archetypes}
    assert set(manifest.required_hard_stop_archetypes) <= archetypes
    assert set(manifest.required_eval_archetypes) <= archetypes


def test_validator_rejects_a_missing_case() -> None:
    raw = _raw_manifest()
    raw["cases"] = [case for case in raw["cases"] if case["case_id"] != "case_inv_meridian"]

    with pytest.raises(ManifestError, match="expected 16 cases"):
        validate_manifest(raw)


def test_validator_rejects_an_unknown_flag_label() -> None:
    raw = _raw_manifest()
    _case(raw, "case_onb_cedar_pine")["expected_flags"] = ["insurance_expired", "made_up_flag"]

    with pytest.raises(ManifestError, match="unknown flag code"):
        validate_manifest(raw)


def test_validator_rejects_a_malformed_severity() -> None:
    raw = _raw_manifest()
    raw["flag_catalog"][0]["severity"] = "critical"

    with pytest.raises(ManifestError, match="severity"):
        validate_manifest(raw)


def test_validator_rejects_an_uncovered_hard_stop_archetype() -> None:
    raw = _raw_manifest()
    raw["required_hard_stop_archetypes"].append("authority_young")

    with pytest.raises(ManifestError, match="authority_young"):
        validate_manifest(raw)


def test_validator_rejects_a_required_hard_stop_archetype_with_no_case() -> None:
    raw = _raw_manifest()
    case = _case(raw, "case_onb_harborline")
    case["archetypes"] = ["identity_mismatch"]
    case["expected_flags"] = ["identity_mismatch"]
    case["expected_recommendation"] = "decline"
    case["expected_decision"] = "decline"
    case["expected_conditions"] = []

    with pytest.raises(ManifestError, match="watchlist_hit.*not covered"):
        validate_manifest(raw)


def test_validator_rejects_an_eval_distribution_that_does_not_match_cases() -> None:
    raw = _raw_manifest()
    raw["eval_distribution"]["onboarding"]["clean"] = 3

    with pytest.raises(ManifestError, match="eval distribution"):
        validate_manifest(raw)


def test_validator_rejects_a_recommendation_that_contradicts_a_hard_stop() -> None:
    raw = _raw_manifest()
    _case(raw, "case_onb_cedar_pine")["expected_recommendation"] = "approve"
    _case(raw, "case_onb_cedar_pine")["expected_decision"] = "approve"

    with pytest.raises(ManifestError, match="recommendation"):
        validate_manifest(raw)


def test_validator_rejects_a_missing_invoice_document_without_a_missing_document_label() -> None:
    raw = _raw_manifest()
    _case(raw, "case_inv_ridgeline")["documents"].remove("proof_of_delivery")

    with pytest.raises(ManifestError, match="missing_document"):
        validate_manifest(raw)


def test_validator_rejects_an_unknown_carrier_reference() -> None:
    raw = _raw_manifest()
    _case(raw, "case_onb_ridgeline")["carrier_id"] = "car_does_not_exist"

    with pytest.raises(ManifestError, match="unknown carrier"):
        validate_manifest(raw)


def test_validator_rejects_a_held_out_hard_stop_that_is_not_multi_flag() -> None:
    raw = _raw_manifest()
    case = _case(raw, "case_onb_quarry_road")
    case["expected_flags"] = ["authority_inactive"]
    case["archetypes"] = ["inactive_authority"]

    with pytest.raises(ManifestError, match="multi-flag"):
        validate_manifest(raw)
