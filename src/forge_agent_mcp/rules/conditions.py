"""Specific remedial conditions for non-approval recommendations."""

from __future__ import annotations

from .catalog import DOCUMENT_TITLES
from .models import RuleFlag


def _missing_document_condition(flag: RuleFlag) -> str:
    document_type = flag.evidence.missing_document_type
    if document_type == "proof_of_delivery":
        return "Obtain a signed proof of delivery."
    if document_type == "certificate_of_insurance":
        return "Obtain a current certificate of insurance."
    title = DOCUMENT_TITLES.get(document_type or "", document_type or "missing document")
    return f"Obtain a current {title[0].lower()}{title[1:]}."


def condition_for(flag: RuleFlag) -> str | None:
    if flag.rule_id == "authority.age":
        return "Obtain six months of authority history or adjust reserve."
    if flag.rule_id == "bank.account_age":
        return "Verify the new bank account with a dated bank letter."
    if flag.rule_id == "bank.account_name":
        return "Resolve the bank account-name mismatch."
    if flag.rule_id == "invoice.arithmetic":
        return "Reconcile the invoice to the rate confirmation."
    if flag.rule_id == "pod.weight":
        return "Obtain a POD matching the BOL weight."
    if flag.rule_id == "pod.delivery_date":
        return "Obtain a POD matching the BOL delivery date."
    if flag.rule_id == "pod.signature":
        return "Obtain a signed proof of delivery."
    if flag.rule_id == "invoice.round_dollar":
        return "Adjust reserve for the round-dollar invoice."
    if flag.rule_id == "invoice.optional_reference":
        return "Confirm the optional invoice reference before funding."
    if flag.rule_id == "documents.required":
        return _missing_document_condition(flag)
    return None


__all__ = ["condition_for"]
