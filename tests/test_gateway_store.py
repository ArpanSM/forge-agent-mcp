from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from forge_agent_mcp.corpus.manifest import load_manifest
from forge_agent_mcp.gateway.store import GatewayStore
from tests.conftest import MANIFEST_PATH, OTHER_TENANT, TENANT


@pytest.fixture
def store(tmp_path: Path) -> GatewayStore:
    gateway_store = GatewayStore(tmp_path / "gateway.db")
    gateway_store.initialize()
    gateway_store.seed_demo(load_manifest(MANIFEST_PATH), tenant_id=TENANT)
    return gateway_store


def test_seed_writes_every_corpus_case_for_one_tenant(store: GatewayStore) -> None:
    cases = store.list_cases(TENANT)

    assert len(cases) == 16
    assert {case.case_type for case in cases} == {"onboarding", "invoice"}
    assert store.list_cases("tenant_other") == []


def test_case_and_document_reads_are_tenant_scoped_and_typed(store: GatewayStore) -> None:
    case = store.get_case(TENANT, "case_onb_ridgeline")

    assert case is not None
    assert case.carrier.legal_name == "Ridgeline Haulage LLC"
    assert case.status == "pending_review"
    assert len(store.list_documents(TENANT, case.case_id)) == 5

    document = store.get_document(TENANT, case.case_id, "DOC-ONB-RIDGELINE-APP")
    assert document is not None
    assert document.document_type == "application_kyc"
    assert "Ridgeline Haulage LLC" in document.text
    assert document.content_hash
    assert document.bboxes
    assert store.get_document("tenant_other", case.case_id, document.document_id) is None


def test_seed_records_the_invoice_identity_and_hash_for_duplicate_checks(
    store: GatewayStore,
) -> None:
    case = store.get_case(TENANT, "case_inv_cedar_pine")

    assert case is not None
    assert case.invoice_number == "INV-CED-1002"
    assert case.invoice_content_hash


def test_audit_is_append_only_and_ordered(store: GatewayStore, tmp_path: Path) -> None:
    first = store.append_audit(
        tenant_id=TENANT,
        case_id="case_onb_ridgeline",
        tool="read_case",
        event="read",
        outcome="ok",
        principal="agent-reader",
    )
    second = store.append_audit(
        tenant_id=TENANT,
        case_id="case_onb_ridgeline",
        tool="get_document",
        event="read",
        outcome="ok",
        principal="agent-reader",
    )

    events = store.list_audit(TENANT, "case_onb_ridgeline")
    assert [event.event_id for event in events] == [first, second]
    assert [event.tool for event in events] == ["read_case", "get_document"]

    with sqlite3.connect(tmp_path / "gateway.db") as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE audit SET outcome = 'tampered' WHERE seq = ?", (first,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM audit WHERE seq = ?", (first,))


def test_audit_limit_returns_the_earliest_events(store: GatewayStore) -> None:
    for index in range(3):
        store.append_audit(
            tenant_id=TENANT,
            case_id="case_onb_ridgeline",
            tool="read_case",
            event="read",
            outcome="ok",
            principal=f"agent-{index}",
        )

    assert len(store.list_audit(TENANT, "case_onb_ridgeline", limit=2)) == 2


def test_case_owners_are_exact_per_tenant(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "two-tenants.db")
    store.initialize()
    manifest = load_manifest(MANIFEST_PATH)
    store.seed_demo(
        manifest,
        tenant_id=TENANT,
        case_ids={"case_onb_ridgeline"},
    )
    store.seed_demo(
        manifest,
        tenant_id=OTHER_TENANT,
        case_ids={"case_onb_meridian"},
    )

    assert store.case_tenants("case_onb_ridgeline") == [TENANT]
    assert store.case_tenants("case_onb_meridian") == [OTHER_TENANT]
    assert store.case_tenants("case_missing") == []
