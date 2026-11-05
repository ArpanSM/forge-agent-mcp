"""Local end-to-end demo: gateway, agent, checkpointer and reviewer console.

The gateway is a real FastMCP Streamable HTTP server in a background thread; the
agent and console share a process but reach the gateway only through the typed
client and scoped tokens. No browser is opened automatically.
"""

from __future__ import annotations

import argparse
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn

from ..agent.gateway import FastMCPGatewayClient
from ..agent.graph import CaseRunner
from ..agent.models import CaseResult
from ..config import Settings
from ..corpus.generator import generate_corpus
from ..corpus.manifest import Manifest, load_manifest
from ..corpus.models import CorpusIndex
from ..gateway.approval import ApprovalTokenIssuer
from ..gateway.auth import READ_SCOPE, WRITE_SCOPE, LocalTokenIssuer
from ..gateway.server import create_gateway
from ..gateway.store import GatewayStore
from .app import create_console_app
from .service import ConsoleService, catalog_from_corpus

DEMO_CASES = ("case_onb_ridgeline", "case_inv_northgate")
DEFAULT_APPROVER = "underwriter@example.test"


def _start_server(app: Any, *, host: str, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(300):
        if server.started:
            return server, thread
        time.sleep(0.025)
    raise RuntimeError(f"server did not start on {host}:{port}")


def _stop_server(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=5)


def _choose_port(host: str, preferred: int) -> int:
    """Use the preferred port when free, otherwise a free ephemeral port."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, preferred))
        except OSError:
            probe.bind((host, 0))
        return int(probe.getsockname()[1])


def _ensure_corpus(manifest: Manifest, corpus_dir: Path) -> CorpusIndex:
    index_path = corpus_dir / "corpus.json"
    if not index_path.is_file():
        generate_corpus(manifest, corpus_dir)
    return CorpusIndex.model_validate_json(index_path.read_text(encoding="utf-8"))


def _reset(paths: list[Path]) -> None:
    for path in paths:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)


def _approval_secret(settings: Settings) -> str:
    if settings.forge_approval_secret is not None:
        secret = settings.forge_approval_secret.get_secret_value()
        if secret.strip():
            return secret
    return secrets.token_urlsafe(32)


def _print_pending(results: list[CaseResult]) -> None:
    print("\nTwo cases are waiting for a human decision:")
    for result in results:
        rec = result.recommendation
        decision = rec.decision if rec is not None else "no recommendation"
        carrier = result.case.carrier.legal_name if result.case is not None else result.case_id
        print(f"  - {carrier} · {result.case_id} · {result.case_type} · recommended {decision}")


def _print_walkthrough(console_url: str, gateway_url: str) -> None:
    print(f"\nForge reviewer console: {console_url}")
    print(f"Gateway (MCP):         {gateway_url}")
    print(
        "\nWalkthrough:\n"
        f"  1. Open {console_url} in a browser.\n"
        "  2. Pick the Ridgeline onboarding case and read the documents, cited fields, flags\n"
        "     and recommendation. Approve it and watch the audit trail gain write_committed.\n"
        "  3. Pick the Northgate invoice case. It recommends request documents because of the\n"
        "     arithmetic and POD mismatch; request documents and confirm awaiting_documents.\n"
        "  4. Open Cedar Pine to see a hard stop: only decline is available.\n"
        "  5. Press Ctrl+C here when you are done.\n"
        "\n  Headless completion: make demo-auto\n"
    )


def _print_completed_walkthrough(console_url: str, gateway_url: str) -> None:
    print(f"\nForge reviewer console: {console_url}")
    print(f"Gateway (MCP):         {gateway_url}")
    print(
        "\nBoth demo cases are already completed. Open the console to inspect the\n"
        "decision, cited evidence and audit chain, or run another case from the queue.\n"
        "Press Ctrl+C here when you are done.\n"
    )


def _print_completed(results: list[CaseResult]) -> None:
    print("\nCompleted decisions:")
    for result in results:
        decision = result.decision
        label = decision.decision if decision is not None else "failed"
        print(
            f"  - {result.case_id}: {result.status} ({label}); "
            f"audit_verified={result.audit_verified}; events={len(result.audit)}"
        )


def run_demo(args: argparse.Namespace) -> int:
    settings = Settings()
    manifest = load_manifest(Path(args.manifest))
    corpus_dir = Path(args.corpus_dir)
    demo_dir = Path(args.demo_dir)
    demo_dir.mkdir(parents=True, exist_ok=True)
    index = _ensure_corpus(manifest, corpus_dir)

    gateway_db = demo_dir / "gateway.db"
    checkpoint_db = demo_dir / "checkpoints.db"
    if not args.keep:
        _reset([gateway_db, checkpoint_db])

    host = args.gateway_host or settings.gateway_host
    gateway_port = args.gateway_port or _choose_port(host, settings.gateway_port)
    console_host = args.console_host or settings.console_host
    console_port = args.console_port or _choose_port(console_host, settings.console_port)
    gateway_url = f"http://{host}:{gateway_port}/mcp"
    console_url = f"http://{console_host}:{console_port}"

    tenant_id = settings.gateway_tenant_id
    store = GatewayStore(gateway_db)
    store.initialize()
    store.seed_demo(manifest, tenant_id=tenant_id)
    approval_secret = _approval_secret(settings)
    issuer = LocalTokenIssuer(
        secret=settings.gateway_jwt_secret.get_secret_value(),
        issuer=f"http://{host}:{gateway_port}",
        audience=settings.gateway_audience,
    )
    gateway = create_gateway(
        store,
        issuer,
        approval_secret=approval_secret,
        approval_audience=settings.gateway_audience,
        approval_issuer=settings.gateway_console_issuer,
    )
    gateway_server, gateway_thread = _start_server(gateway.http_app(), host=host, port=gateway_port)

    agent_token = issuer.issue(
        subject="agent-underwriter",
        tenant_id=tenant_id,
        scopes=[READ_SCOPE, WRITE_SCOPE],
    )
    console_token = issuer.issue(
        subject="review-console",
        tenant_id=tenant_id,
        scopes=[READ_SCOPE],
    )
    approval_issuer = ApprovalTokenIssuer(
        secret=approval_secret,
        issuer=settings.gateway_console_issuer,
        audience=settings.gateway_audience,
        default_ttl_seconds=settings.gateway_approval_ttl_seconds,
    )
    runner = CaseRunner(
        FastMCPGatewayClient(gateway_url, agent_token),
        corpus_dir=corpus_dir,
        checkpoint_path=checkpoint_db,
    )
    service = ConsoleService(
        runner=runner,
        gateway=FastMCPGatewayClient(gateway_url, console_token),
        catalog=catalog_from_corpus(manifest, index),
        approval_issuer=approval_issuer,
        token_issuer=issuer,
        tenant_id=tenant_id,
        default_approver=args.approver,
    )

    started: list[CaseResult] = []
    try:
        for case_id in DEMO_CASES:
            started.append(runner.start(case_id))

        if args.auto:
            completed: list[CaseResult] = []
            for pending in started:
                rec = pending.recommendation
                if rec is None:
                    completed.append(pending)
                    continue
                conditions = list(rec.conditions)
                if rec.decision == "conditional_approve" and not conditions:
                    conditions = ["Reviewer confirmed the conditional approval."]
                completed.append(
                    service.decide(
                        pending.case_id,
                        action=rec.decision,
                        approver=args.approver,
                        note="Completed by the headless demo runner.",
                        conditions=conditions,
                    )
                )
            _print_completed(completed)
            if not args.serve:
                return 0
            _print_completed_walkthrough(console_url, gateway_url)
        else:
            _print_pending(started)
            _print_walkthrough(console_url, gateway_url)

        print("\nPress Ctrl+C to stop the demo.\n")
        uvicorn.run(
            create_console_app(service),
            host=console_host,
            port=console_port,
            log_level="warning",
        )
        return 0
    except KeyboardInterrupt:
        print("\nStopping the demo.")
        return 0
    finally:
        runner.close()
        _stop_server(gateway_server, gateway_thread)
        store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m forge_agent_mcp.console",
        description="Run the reviewer console and local demo.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("demo", help="start the gateway, agent and console")
    demo.add_argument("--manifest", default="corpus/manifest.json")
    demo.add_argument("--corpus-dir", default="data/corpus")
    demo.add_argument("--demo-dir", default="data/console-demo")
    demo.add_argument("--gateway-host")
    demo.add_argument("--gateway-port", type=int)
    demo.add_argument("--console-host")
    demo.add_argument("--console-port", type=int)
    demo.add_argument("--approver", default=DEFAULT_APPROVER)
    demo.add_argument(
        "--keep",
        action="store_true",
        help="keep the demo databases and checkpoints from a previous run",
    )
    demo.add_argument(
        "--auto",
        action="store_true",
        help="complete both demo cases with a scripted reviewer instead of waiting",
    )
    demo.add_argument(
        "--serve",
        action="store_true",
        help="serve the reviewer console instead of exiting after the run",
    )
    args = parser.parse_args(argv)
    if args.command == "demo":
        return run_demo(args)
    parser.error("unknown command")
    return 2


__all__ = ["DEFAULT_APPROVER", "DEMO_CASES", "main", "run_demo"]
