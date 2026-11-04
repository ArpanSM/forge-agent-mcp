"""Field definitions for the document types in the synthetic corpus.

The registry is the single source of truth for required fields, deterministic
template parsing and the provider schema. It describes the fields the rule
engine needs; it does not contain any values.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import DocumentType


@dataclass(frozen=True)
class FieldDefinition:
    key: str
    label: str
    required: bool = True


DOCUMENT_FIELDS: dict[DocumentType, tuple[FieldDefinition, ...]] = {
    "application_kyc": (
        FieldDefinition("legal_name", "Legal name"),
        FieldDefinition("dba", "DBA"),
        FieldDefinition("ein", "EIN"),
        FieldDefinition("mc_number", "MC number"),
        FieldDefinition("dot_number", "DOT number"),
        FieldDefinition("address", "Address"),
        FieldDefinition("phone", "Phone"),
        FieldDefinition("email", "Email"),
        FieldDefinition("owner_name", "Owner"),
        FieldDefinition("owner_email", "Owner email"),
        FieldDefinition("formation_date", "Formation date"),
        FieldDefinition("requested_advance", "Requested advance"),
        FieldDefinition("requested_reserve", "Requested reserve"),
        FieldDefinition("notes", "Notes", required=False),
    ),
    "w9": (
        FieldDefinition("legal_name", "Legal name"),
        FieldDefinition("business_name", "Business name"),
        FieldDefinition("tax_classification", "Tax classification"),
        FieldDefinition("ein", "EIN"),
        FieldDefinition("address", "Address"),
        FieldDefinition("signature", "Signature"),
        FieldDefinition("signed_on", "Signed on"),
    ),
    "certificate_of_insurance": (
        FieldDefinition("insured_name", "Insured"),
        FieldDefinition("insurer", "Insurer"),
        FieldDefinition("policy_number", "Policy number"),
        FieldDefinition("auto_liability_limit", "Auto liability limit"),
        FieldDefinition("cargo_limit", "Cargo limit"),
        FieldDefinition("general_liability_limit", "General liability limit"),
        FieldDefinition("effective_on", "Effective on"),
        FieldDefinition("expires_on", "Expires on"),
        FieldDefinition("certificate_holder", "Certificate holder"),
        FieldDefinition("additional_insured", "Additional insured"),
    ),
    "bank_letter": (
        FieldDefinition("account_holder", "Account holder"),
        FieldDefinition("bank_name", "Bank"),
        FieldDefinition("routing_number", "Routing number"),
        FieldDefinition("account_number", "Account number"),
        FieldDefinition("account_opened_on", "Account opened on"),
        FieldDefinition("letter_date", "Letter date"),
        FieldDefinition("signed_by", "Signed by"),
    ),
    "mc_authority": (
        FieldDefinition("legal_name", "Legal name"),
        FieldDefinition("mc_number", "MC number"),
        FieldDefinition("dot_number", "DOT number"),
        FieldDefinition("authority_status", "Authority status"),
        FieldDefinition("authority_granted_on", "Authority granted on"),
        FieldDefinition("authority_updated_on", "Authority updated on"),
        FieldDefinition("operating_radius", "Operating radius"),
        FieldDefinition("safety_rating", "Safety rating"),
        FieldDefinition("authority_notes", "Authority notes", required=False),
    ),
    "rate_confirmation": (
        FieldDefinition("broker_name", "Broker"),
        FieldDefinition("carrier_name", "Carrier"),
        FieldDefinition("rate_confirmation_number", "Rate confirmation number"),
        FieldDefinition("load_number", "Load number"),
        FieldDefinition("origin", "Origin"),
        FieldDefinition("destination", "Destination"),
        FieldDefinition("pickup_date", "Pickup date"),
        FieldDefinition("delivery_date", "Delivery date"),
        FieldDefinition("weight_lbs", "Weight"),
        FieldDefinition("commodity", "Commodity"),
        FieldDefinition("linehaul", "Linehaul"),
        FieldDefinition("fuel_surcharge", "Fuel surcharge"),
        FieldDefinition("accessorials", "Accessorials"),
        FieldDefinition("total", "Total"),
        FieldDefinition("payment_terms", "Payment terms"),
    ),
    "bill_of_lading": (
        FieldDefinition("bol_number", "BOL number"),
        FieldDefinition("load_number", "Load number"),
        FieldDefinition("shipper", "Shipper"),
        FieldDefinition("consignee", "Consignee"),
        FieldDefinition("origin", "Origin"),
        FieldDefinition("destination", "Destination"),
        FieldDefinition("pickup_date", "Pickup date"),
        FieldDefinition("weight_lbs", "Weight"),
        FieldDefinition("commodity", "Commodity"),
        FieldDefinition("carrier_name", "Carrier"),
        FieldDefinition("signed_by", "Signed by"),
    ),
    "proof_of_delivery": (
        FieldDefinition("pod_number", "POD number"),
        FieldDefinition("load_number", "Load number"),
        FieldDefinition("delivered_on", "Delivered on"),
        FieldDefinition("consignee", "Consignee"),
        FieldDefinition("weight_lbs", "Delivered weight"),
        FieldDefinition("signed_by", "Signed by"),
        FieldDefinition("notes", "Notes", required=False),
    ),
    "invoice": (
        FieldDefinition("invoice_number", "Invoice number"),
        FieldDefinition("invoice_date", "Invoice date"),
        FieldDefinition("due_date", "Due date"),
        FieldDefinition("carrier_name", "Carrier"),
        FieldDefinition("broker_name", "Broker"),
        FieldDefinition("load_number", "Load number"),
        FieldDefinition("linehaul", "Linehaul"),
        FieldDefinition("fuel_surcharge", "Fuel surcharge"),
        FieldDefinition("accessorials", "Accessorials"),
        FieldDefinition("total", "Total"),
        FieldDefinition("remit_to", "Remit to"),
        FieldDefinition("reference_number", "Reference", required=False),
    ),
}


def field_definitions(document_type: DocumentType) -> tuple[FieldDefinition, ...]:
    return DOCUMENT_FIELDS.get(document_type, ())


def required_field_keys(document_type: DocumentType) -> tuple[str, ...]:
    return tuple(field.key for field in field_definitions(document_type) if field.required)


__all__ = [
    "DOCUMENT_FIELDS",
    "FieldDefinition",
    "field_definitions",
    "required_field_keys",
]
