from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from forge_agent_mcp.corpus.models import (
    BrokerBondRecord,
    PriorInvoiceRecord,
    RecordPayload,
    WatchlistRecord,
)


def test_record_payload_union_selects_the_typed_record() -> None:
    adapter = TypeAdapter(RecordPayload)
    payload = adapter.validate_python(
        {
            "record_id": "REC-ONB-TEST-WATCH",
            "record_type": "watchlist",
            "synthetic": True,
            "carrier_id": "car_test",
            "checked_on": "2026-10-30",
            "list_name": "SYNTHETIC-SANCTIONS-PEP",
            "matches": [],
        }
    )

    assert isinstance(payload, WatchlistRecord)
    assert payload.record_type == "watchlist"


def test_record_payload_union_rejects_an_unknown_record_type() -> None:
    adapter = TypeAdapter(RecordPayload)

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "record_id": "REC-ONB-TEST-BOGUS",
                "record_type": "bogus",
                "synthetic": True,
                "carrier_id": "car_test",
            }
        )


def test_prior_invoice_record_rejects_a_generation_marker() -> None:
    with pytest.raises(ValidationError):
        PriorInvoiceRecord(
            record_id="REC-INV-TEST-PRIOR",
            carrier_id="car_test",
            invoices=[
                {
                    "invoice_number": "INV-TEST-1",
                    "load_number": "LD-TEST-1",
                    "invoice_date": "2026-10-01",
                    "amount": 1000,
                    "duplicate_current_invoice_hash": True,
                }
            ],
        )


def test_broker_bond_record_is_typed() -> None:
    record = BrokerBondRecord(
        record_id="REC-INV-TEST-BOND",
        carrier_id="car_test",
        broker_name="Cascade Brokerage LLC",
        broker_mc="MC-004200",
        bond_active=True,
        bond_amount=75000,
        effective_on="2026-01-01",
        expires_on="2027-06-30",
        surety="Granite Peak Surety",
    )

    assert record.record_type == "broker_bond"
    assert record.bond_active is True
