"""Template data shared by PDF rendering and ground-truth generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import CorpusError
from .manifest import Carrier, Case, Manifest
from .pdf import DocumentSpec, FieldSpec

LETTER_DATE = "2026-10-29"
W9_SIGNED_ON = "2026-10-28"
BOL_SIGNED_BY = "J. Ramos"


@dataclass(frozen=True)
class RecordSpec:
    record_id: str
    record_type: str
    title: str
    filename: str
    data: dict[str, Any]


def format_money(value: int) -> str:
    return f"${value:,.2f}"


def format_weight(value: int) -> str:
    return f"{value:,} lbs"


def _case_slug(case_id: str) -> str:
    return case_id.removeprefix("case_").replace("_", "-").upper()


def _field(key: str, label: str, value: str) -> FieldSpec:
    return FieldSpec(key=key, label=label, value=value)


def build_documents(manifest: Manifest, carrier: Carrier, case: Case) -> list[DocumentSpec]:
    slug = _case_slug(case.case_id)
    documents: list[DocumentSpec] = []
    for document_type in case.documents:
        definition = manifest.document_types[document_type]
        documents.append(
            DocumentSpec(
                document_id=f"DOC-{slug}-{definition.code}",
                document_type=document_type,
                title=definition.title,
                carrier_name=carrier.legal_name,
                filename=f"documents/{definition.code}.pdf",
                fields=tuple(_fields_for(document_type, carrier, case)),
            )
        )
    return documents


def _fields_for(document_type: str, carrier: Carrier, case: Case) -> list[FieldSpec]:
    if document_type == "application_kyc":
        fields = [
            _field("legal_name", "Legal name", carrier.legal_name),
            _field("dba", "DBA", carrier.dba),
            _field("ein", "EIN", carrier.ein),
            _field("mc_number", "MC number", carrier.mc_number),
            _field("dot_number", "DOT number", carrier.dot_number),
            _field("address", "Address", carrier.address.one_line()),
            _field("phone", "Phone", carrier.phone),
            _field("email", "Email", carrier.email),
            _field("owner_name", "Owner", carrier.owner_name),
            _field("owner_email", "Owner email", carrier.owner_email),
            _field("formation_date", "Formation date", carrier.formation_date.isoformat()),
            _field("requested_advance", "Requested advance", "90%"),
            _field("requested_reserve", "Requested reserve", "10%"),
        ]
        if carrier.application_notes:
            fields.append(_field("notes", "Notes", carrier.application_notes))
        return fields

    if document_type == "w9":
        if "LLC" in carrier.legal_name:
            tax_classification = "Limited liability company"
        elif "Inc." in carrier.legal_name or "Co." in carrier.legal_name:
            tax_classification = "Corporation"
        else:
            tax_classification = "Partnership"
        return [
            _field(
                "legal_name",
                "Legal name",
                carrier.w9_legal_name or carrier.legal_name,
            ),
            _field("business_name", "Business name", carrier.dba),
            _field("tax_classification", "Tax classification", tax_classification),
            _field("ein", "EIN", carrier.w9_ein or carrier.ein),
            _field("address", "Address", carrier.address.one_line()),
            _field("signature", "Signature", carrier.owner_name),
            _field("signed_on", "Signed on", W9_SIGNED_ON),
        ]

    if document_type == "certificate_of_insurance":
        return [
            _field("insured_name", "Insured", carrier.legal_name),
            _field("insurer", "Insurer", carrier.insurance.insurer),
            _field("policy_number", "Policy number", carrier.insurance.policy_number),
            _field(
                "auto_liability_limit",
                "Auto liability limit",
                format_money(carrier.insurance.auto_liability_limit),
            ),
            _field(
                "cargo_limit",
                "Cargo limit",
                format_money(carrier.insurance.cargo_limit),
            ),
            _field(
                "general_liability_limit",
                "General liability limit",
                format_money(carrier.insurance.general_liability_limit),
            ),
            _field(
                "effective_on",
                "Effective on",
                carrier.insurance.effective_on.isoformat(),
            ),
            _field("expires_on", "Expires on", carrier.insurance.expires_on.isoformat()),
            _field(
                "certificate_holder",
                "Certificate holder",
                carrier.insurance.certificate_holder,
            ),
            _field(
                "additional_insured",
                "Additional insured",
                carrier.insurance.certificate_holder,
            ),
        ]

    if document_type == "bank_letter":
        return [
            _field("account_holder", "Account holder", carrier.bank.account_name),
            _field("bank_name", "Bank", carrier.bank.bank_name),
            _field("routing_number", "Routing number", carrier.bank.routing_number),
            _field("account_number", "Account number", carrier.bank.account_number),
            _field(
                "account_opened_on",
                "Account opened on",
                carrier.bank.opened_on.isoformat(),
            ),
            _field("letter_date", "Letter date", LETTER_DATE),
            _field("signed_by", "Signed by", "Branch Manager, Northwind Test Bank"),
        ]

    if document_type == "mc_authority":
        fields = [
            _field("legal_name", "Legal name", carrier.legal_name),
            _field("mc_number", "MC number", carrier.mc_number),
            _field("dot_number", "DOT number", carrier.dot_number),
            _field("authority_status", "Authority status", carrier.authority.status),
            _field(
                "authority_granted_on",
                "Authority granted on",
                carrier.authority.granted_on.isoformat(),
            ),
            _field(
                "authority_updated_on",
                "Authority updated on",
                carrier.authority.updated_on.isoformat(),
            ),
            _field("operating_radius", "Operating radius", carrier.authority.operating_radius),
            _field("safety_rating", "Safety rating", carrier.authority.safety_rating),
        ]
        if carrier.authority.revoked_on is not None:
            fields.append(
                _field(
                    "authority_notes",
                    "Authority notes",
                    f"Revoked on {carrier.authority.revoked_on.isoformat()}: "
                    f"{carrier.authority.reason or 'synthetic revocation'}",
                )
            )
        return fields

    if document_type == "rate_confirmation":
        rate = carrier.rate_confirmation
        return [
            _field("broker_name", "Broker", carrier.broker_bond.broker_name),
            _field("carrier_name", "Carrier", carrier.legal_name),
            _field(
                "rate_confirmation_number",
                "Rate confirmation number",
                rate.rate_confirmation_number,
            ),
            _field("load_number", "Load number", carrier.load.load_number),
            _field("origin", "Origin", carrier.load.origin),
            _field("destination", "Destination", carrier.load.destination),
            _field("pickup_date", "Pickup date", carrier.load.pickup_date.isoformat()),
            _field("delivery_date", "Delivery date", carrier.load.delivery_date.isoformat()),
            _field("weight_lbs", "Weight", format_weight(carrier.load.weight_lbs)),
            _field("commodity", "Commodity", carrier.load.commodity),
            _field("linehaul", "Linehaul", format_money(rate.linehaul)),
            _field("fuel_surcharge", "Fuel surcharge", format_money(rate.fuel_surcharge)),
            _field("accessorials", "Accessorials", format_money(rate.accessorials)),
            _field("total", "Total", format_money(rate.total)),
            _field("payment_terms", "Payment terms", rate.payment_terms),
        ]

    if document_type == "bill_of_lading":
        return [
            _field(
                "bol_number",
                "BOL number",
                f"BOL-{carrier.load.load_number.split('-')[-1]}",
            ),
            _field("load_number", "Load number", carrier.load.load_number),
            _field("shipper", "Shipper", carrier.load.shipper),
            _field("consignee", "Consignee", carrier.load.consignee),
            _field("origin", "Origin", carrier.load.origin),
            _field("destination", "Destination", carrier.load.destination),
            _field("pickup_date", "Pickup date", carrier.load.pickup_date.isoformat()),
            _field("weight_lbs", "Weight", format_weight(carrier.load.weight_lbs)),
            _field("commodity", "Commodity", carrier.load.commodity),
            _field("carrier_name", "Carrier", carrier.legal_name),
            _field("signed_by", "Signed by", BOL_SIGNED_BY),
        ]

    if document_type == "proof_of_delivery":
        if carrier.pod is None:
            raise CorpusError(f"case {case.case_id}: POD requested but carrier has no POD")
        fields = [
            _field("pod_number", "POD number", carrier.pod.pod_number),
            _field("load_number", "Load number", carrier.load.load_number),
            _field("delivered_on", "Delivered on", carrier.pod.delivered_on.isoformat()),
            _field("consignee", "Consignee", carrier.load.consignee),
            _field("weight_lbs", "Delivered weight", format_weight(carrier.pod.weight_lbs)),
            _field("signed_by", "Signed by", carrier.pod.signed_by),
        ]
        if carrier.pod.notes:
            fields.append(_field("notes", "Notes", carrier.pod.notes))
        return fields

    if document_type == "invoice":
        invoice = carrier.invoice
        return [
            _field("invoice_number", "Invoice number", invoice.invoice_number),
            _field("invoice_date", "Invoice date", invoice.invoice_date.isoformat()),
            _field("due_date", "Due date", invoice.due_date.isoformat()),
            _field("carrier_name", "Carrier", carrier.legal_name),
            _field("broker_name", "Broker", carrier.broker_bond.broker_name),
            _field("load_number", "Load number", carrier.load.load_number),
            _field("linehaul", "Linehaul", format_money(invoice.linehaul)),
            _field("fuel_surcharge", "Fuel surcharge", format_money(invoice.fuel_surcharge)),
            _field("accessorials", "Accessorials", format_money(invoice.accessorials)),
            _field("total", "Total", format_money(invoice.total)),
            _field("remit_to", "Remit to", "Northwind Freight Capital"),
        ]

    raise CorpusError(f"case {case.case_id}: unknown document type {document_type!r}")


def build_records(manifest: Manifest, carrier: Carrier, case: Case) -> list[RecordSpec]:
    slug = _case_slug(case.case_id)
    records: list[RecordSpec] = []
    for record_type in case.structured_records:
        definition = manifest.record_types[record_type]
        if record_type == "ucc_filing":
            data = {
                "record_id": f"REC-{slug}-UCC",
                "record_type": record_type,
                "synthetic": True,
                "carrier_id": carrier.carrier_id,
                **carrier.ucc.model_dump(mode="json"),
            }
        elif record_type == "watchlist":
            data = {
                "record_id": f"REC-{slug}-WATCH",
                "record_type": record_type,
                "synthetic": True,
                "carrier_id": carrier.carrier_id,
                **carrier.watchlist.model_dump(mode="json"),
            }
        elif record_type == "prior_invoices":
            data = {
                "record_id": f"REC-{slug}-PRIOR",
                "record_type": record_type,
                "synthetic": True,
                "carrier_id": carrier.carrier_id,
                "invoices": [invoice.model_dump(mode="json") for invoice in carrier.prior_invoices],
            }
        elif record_type == "broker_bond":
            data = {
                "record_id": f"REC-{slug}-BOND",
                "record_type": record_type,
                "synthetic": True,
                "carrier_id": carrier.carrier_id,
                **carrier.broker_bond.model_dump(mode="json"),
            }
        else:
            raise CorpusError(f"case {case.case_id}: unknown record type {record_type!r}")

        records.append(
            RecordSpec(
                record_id=str(data["record_id"]),
                record_type=record_type,
                title=definition.title,
                filename=f"structured/{record_type}.json",
                data=data,
            )
        )
    return records
