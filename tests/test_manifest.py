from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from forge_agent_mcp.corpus.manifest import (
    ARCHETYPE_FLAGS,
    ManifestError,
    load_manifest,
    validate_manifest,
)

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


def test_held_out_split_is_carrier_disjoint() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    eval_carriers = {case.carrier_id for case in manifest.cases if case.split == "eval"}
    held_out_carriers = {case.carrier_id for case in manifest.cases if case.split == "held_out"}

    assert len(eval_carriers) == 6
    assert len(held_out_carriers) == 2
    assert not eval_carriers & held_out_carriers


def test_committed_manifest_covers_every_required_archetype() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    flags_by_code = {flag.code: flag for flag in manifest.flag_catalog}
    eval_archetypes = {
        archetype
        for case in manifest.cases
        if case.split == "eval"
        for archetype in case.archetypes
    }
    all_archetypes = {archetype for case in manifest.cases for archetype in case.archetypes}

    hard_stop_codes = {code for code, flag in flags_by_code.items() if flag.severity == "hard_stop"}
    assert {
        "watchlist_hit",
        "authority_inactive",
        "identity_mismatch",
        "insurance_expired",
        "insurance_below_minimum",
        "duplicate_invoice",
    } <= hard_stop_codes

    assert set(ARCHETYPE_FLAGS) <= all_archetypes
    assert set(manifest.required_hard_stop_archetypes) <= eval_archetypes
    assert set(manifest.required_eval_archetypes) <= eval_archetypes


def _drop_case(raw: dict[str, Any]) -> None:
    raw["cases"] = [case for case in raw["cases"] if case["case_id"] != "case_inv_meridian"]


def _unknown_flag(raw: dict[str, Any]) -> None:
    _case(raw, "case_onb_cedar_pine")["expected_flags"].append("made_up_flag")


def _malformed_severity(raw: dict[str, Any]) -> None:
    raw["flag_catalog"][0]["severity"] = "critical"


def _unknown_hard_stop_archetype(raw: dict[str, Any]) -> None:
    raw["required_hard_stop_archetypes"].append("made_up_archetype")


def _uncovered_hard_stop_archetype(raw: dict[str, Any]) -> None:
    case = _case(raw, "case_onb_northgate")
    case["archetypes"] = ["clean"]
    case["expected_flags"] = []
    case["category"] = "clean"
    case["expected_recommendation"] = "approve"
    case["expected_decision"] = "approve"
    case["expected_conditions"] = []


def _uncovered_medium_archetype(raw: dict[str, Any]) -> None:
    case = _case(raw, "case_onb_quarry_road")
    case["archetypes"] = ["clean"]
    case["expected_flags"] = []
    case["category"] = "clean"
    case["expected_recommendation"] = "approve"
    case["expected_decision"] = "approve"
    case["expected_conditions"] = []


def _downgraded_hard_stop_flag(raw: dict[str, Any]) -> None:
    flag = next(flag for flag in raw["flag_catalog"] if flag["code"] == "watchlist_hit")
    flag["severity"] = "high"


def _hard_stop_with_injection(raw: dict[str, Any]) -> None:
    case = _case(raw, "case_onb_cedar_pine")
    case["archetypes"].append("prompt_injection")
    case["expected_flags"].append("document_instruction_injection")
    case["category"] = "injection"
    case["expected_recommendation"] = "hold"
    case["expected_decision"] = "request_documents"
    case["expected_conditions"] = ["Review the packet manually."]


def _hard_stop_approved(raw: dict[str, Any]) -> None:
    case = _case(raw, "case_onb_cedar_pine")
    case["expected_recommendation"] = "approve"
    case["expected_decision"] = "approve"


def _eval_distribution_mismatch(raw: dict[str, Any]) -> None:
    raw["eval_distribution"]["onboarding"]["clean"] = 2


def _missing_invoice_document(raw: dict[str, Any]) -> None:
    _case(raw, "case_inv_ridgeline")["documents"].remove("proof_of_delivery")


def _missing_onboarding_document(raw: dict[str, Any]) -> None:
    case = _case(raw, "case_onb_ridgeline")
    case["documents"].remove("certificate_of_insurance")


def _unknown_carrier(raw: dict[str, Any]) -> None:
    _case(raw, "case_onb_ridgeline")["carrier_id"] = "car_does_not_exist"


def _archetype_without_flag(raw: dict[str, Any]) -> None:
    case = _case(raw, "case_onb_northgate")
    case["expected_flags"] = []
    case["expected_recommendation"] = "decline"
    case["expected_decision"] = "decline"
    case["expected_conditions"] = []


def _held_out_not_disjoint(raw: dict[str, Any]) -> None:
    _case(raw, "case_onb_blue_alder")["split"] = "eval"


def _required_hard_stop_not_eval(raw: dict[str, Any]) -> None:
    _case(raw, "case_onb_sagebrush")["split"] = "held_out"


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (_drop_case, "expected 16 cases"),
        (_unknown_flag, "unknown flag code"),
        (_malformed_severity, "severity"),
        (_unknown_hard_stop_archetype, "made_up_archetype"),
        (_uncovered_hard_stop_archetype, "watchlist_hit.*not covered"),
        (_uncovered_medium_archetype, "authority_young.*not covered"),
        (_downgraded_hard_stop_flag, "downgraded"),
        (_hard_stop_with_injection, "hard stop requires"),
        (_hard_stop_approved, "hard stop requires"),
        (_eval_distribution_mismatch, "eval distribution"),
        (_missing_invoice_document, "missing_document"),
        (_missing_onboarding_document, "missing_document"),
        (_unknown_carrier, "unknown carrier"),
        (_archetype_without_flag, "no matching flag"),
        (_held_out_not_disjoint, "carrier-disjoint"),
        (_required_hard_stop_not_eval, "not in the eval split"),
    ],
)
def test_validator_rejects_malformed_manifests(mutate, match) -> None:
    raw = _raw_manifest()
    mutate(raw)

    with pytest.raises(ManifestError, match=match):
        validate_manifest(raw)
