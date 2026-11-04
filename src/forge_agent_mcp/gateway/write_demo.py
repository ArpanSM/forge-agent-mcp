"""CLI walkthrough for the approval-token and idempotent decision write.

Every negative path in this script runs against the same real gateway and store
the MCP tools use, with no mocks and no network beyond localhost.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from ..corpus.vocab import Decision
from .approval import ApprovalTokenIssuer

DEFAULT_RATIONALE = "Synthetic Ridgeline packet is clean; approve onboarding."
DEFAULT_APPROVER = "underwriter@example.test"


def _summary(result: Any) -> dict[str, Any]:
    return result.structured_content or {}


def _print_step(index: int, label: str, outcome: str) -> None:
    print(f"  {index}. {label:<46} {outcome}")


async def run_write_demo(
    *,
    url: str,
    case_id: str,
    agent_token: str,
    approval: ApprovalTokenIssuer,
    tenant_id: str,
    approver: str = DEFAULT_APPROVER,
) -> int:
    expected: list[str] = []
    approvals: dict[str, str] = {}

    def token(name: str, *, decision: Decision, rationale: str, ttl: int, jti: str) -> str:
        if name not in approvals:
            approvals[name] = approval.issue_for_decision(
                approver=approver,
                tenant_id=tenant_id,
                case_id=case_id,
                decision=decision,
                rationale=rationale,
                conditions=[],
                ttl_seconds=ttl,
                jti=jti,
            )
        return approvals[name]

    def arguments(
        *, rationale: str, approval_token: str, key: str, decision: Decision = "approve"
    ) -> dict[str, Any]:
        return {
            "case_id": case_id,
            "decision": decision,
            "rationale": rationale,
            "conditions": [],
            "approval_token": approval_token,
            "idempotency_key": key,
        }

    async with Client(StreamableHttpTransport(url, auth=agent_token)) as client:

        async def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return _summary(await client.call_tool_mcp(name, args))

        print(f"Approval and idempotency write demo for {case_id}\n")

        missing = await call(
            "create_decision",
            arguments(rationale=DEFAULT_RATIONALE, approval_token="", key="demo-idem-1"),
        )
        expected.append("approval_required")
        _print_step(
            1,
            "missing approval token",
            f"refused with {missing.get('error_code')}",
        )

        committed = await call(
            "create_decision",
            arguments(
                rationale=DEFAULT_RATIONALE,
                approval_token=token(
                    "valid",
                    decision="approve",
                    rationale=DEFAULT_RATIONALE,
                    ttl=600,
                    jti="jti_demo_commit",
                ),
                key="demo-idem-1",
            ),
        )
        expected.append("committed")
        decision_id = (committed.get("data") or {}).get("decision_id")
        _print_step(
            2,
            "valid approval token",
            f"committed {decision_id}",
        )

        retry = await call(
            "create_decision",
            arguments(
                rationale=DEFAULT_RATIONALE,
                approval_token=token(
                    "valid",
                    decision="approve",
                    rationale=DEFAULT_RATIONALE,
                    ttl=600,
                    jti="jti_demo_commit",
                ),
                key="demo-idem-1",
            ),
        )
        expected.append("replayed")
        _print_step(
            3,
            "same key, same arguments",
            f"replayed={str((retry.get('data') or {}).get('replayed')).lower()}",
        )

        changed_rationale = "Changed rationale that must not reuse the committed result."
        conflict = await call(
            "create_decision",
            arguments(
                rationale=changed_rationale,
                approval_token=token(
                    "changed",
                    decision="approve",
                    rationale=changed_rationale,
                    ttl=600,
                    jti="jti_demo_changed",
                ),
                key="demo-idem-1",
            ),
        )
        expected.append("idempotency_conflict")
        _print_step(
            4,
            "same key, changed arguments",
            f"refused with {conflict.get('error_code')}",
        )

        expired = await call(
            "create_decision",
            arguments(
                rationale=DEFAULT_RATIONALE,
                approval_token=token(
                    "expired",
                    decision="approve",
                    rationale=DEFAULT_RATIONALE,
                    ttl=-5,
                    jti="jti_demo_expired",
                ),
                key="demo-idem-expired",
            ),
        )
        expected.append("approval_expired")
        _print_step(
            5,
            "expired approval token",
            f"refused with {expired.get('error_code')}",
        )

        mismatch = await call(
            "create_decision",
            arguments(
                rationale=DEFAULT_RATIONALE,
                approval_token=token(
                    "mismatch",
                    decision="decline",
                    rationale=DEFAULT_RATIONALE,
                    ttl=600,
                    jti="jti_demo_mismatch",
                ),
                key="demo-idem-mismatch",
            ),
        )
        expected.append("approval_mismatch")
        _print_step(
            6,
            "approval token for another decision",
            f"refused with {mismatch.get('error_code')}",
        )

        reused = await call(
            "create_decision",
            arguments(
                rationale=DEFAULT_RATIONALE,
                approval_token=token(
                    "valid",
                    decision="approve",
                    rationale=DEFAULT_RATIONALE,
                    ttl=600,
                    jti="jti_demo_commit",
                ),
                key="demo-idem-reused",
            ),
        )
        expected.append("approval_reused")
        _print_step(
            7,
            "reused approval token, new key",
            f"refused with {reused.get('error_code')}",
        )

        case = await call("read_case", {"case_id": case_id})
        audit = await call("get_audit", {"case_id": case_id, "limit": 100})

    case_data = case.get("data") or {}
    stage = (case_data.get("crm_stage") or {}).get("stage")
    decision_count = len(
        [
            event
            for event in (audit.get("data") or {}).get("events", [])
            if event["event"] == "write_committed"
        ]
    )

    print("\n  Audit chain (create_decision):")
    for event in (audit.get("data") or {}).get("events", []):
        if event["tool"] != "create_decision":
            continue
        detail = event.get("detail") or {}
        linked = detail.get("decision_id") or detail.get("error_code") or ""
        print(f"    #{event['event_id']:<3} {event['event']:<22} {event['outcome']:<7} {linked}")

    print(
        f"\n  Final case: status={case_data.get('status')} crm_stage={stage} "
        f"decision={decision_id} commits={decision_count}"
    )

    actual = [
        missing.get("error_code"),
        "committed" if committed.get("ok") and decision_id else "not_committed",
        "replayed" if (retry.get("data") or {}).get("replayed") else "not_replayed",
        conflict.get("error_code"),
        expired.get("error_code"),
        mismatch.get("error_code"),
        reused.get("error_code"),
    ]
    if actual != expected:
        print(f"\n  ERROR: expected {expected}, got {actual}")
        return 1
    if decision_count != 1 or stage != "approved":
        print("\n  ERROR: expected exactly one committed decision and CRM stage approved")
        return 1

    print("\n  Every expected refusal, commit, replay and audit row is present.")
    return 0


def main(
    *,
    url: str,
    case_id: str,
    agent_token: str,
    approval: ApprovalTokenIssuer,
    tenant_id: str,
    approver: str = DEFAULT_APPROVER,
) -> int:
    return asyncio.run(
        run_write_demo(
            url=url,
            case_id=case_id,
            agent_token=agent_token,
            approval=approval,
            tenant_id=tenant_id,
            approver=approver,
        )
    )


__all__ = ["DEFAULT_APPROVER", "DEFAULT_RATIONALE", "main", "run_write_demo"]
