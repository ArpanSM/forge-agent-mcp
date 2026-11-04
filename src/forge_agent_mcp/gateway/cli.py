"""Command-line entry point for the gateway, local issuer and demo client."""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn

from ..config import Settings
from ..corpus.manifest import load_manifest
from .auth import APPROVE_SCOPE, READ_SCOPE, WRITE_SCOPE, LocalTokenIssuer
from .client import main as client_main
from .server import create_gateway
from .store import GatewayStore

DEFAULT_MANIFEST = Path(__file__).resolve().parents[3] / "corpus" / "manifest.json"


def _issuer(settings: Settings) -> LocalTokenIssuer:
    return LocalTokenIssuer(
        secret=settings.gateway_jwt_secret.get_secret_value(),
        issuer=settings.gateway_issuer,
        audience=settings.gateway_audience,
    )


def _open_store(
    settings: Settings,
    *,
    db_path: str | None,
    manifest_path: str | None,
) -> GatewayStore:
    store = GatewayStore(db_path or settings.gateway_db_path)
    store.initialize()
    store.seed_demo(
        load_manifest(manifest_path or DEFAULT_MANIFEST),
        tenant_id=settings.gateway_tenant_id,
    )
    return store


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m forge_agent_mcp.gateway",
        description="Run the synthetic freight MCP gateway.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="serve the gateway")
    serve.add_argument("--transport", choices=["http", "stdio"], default="http")
    serve.add_argument("--db")
    serve.add_argument("--manifest")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--path", default="/mcp")

    issue = subparsers.add_parser("issue", help="mint a local HS256 agent token")
    issue.add_argument("--subject", default="agent-underwriter")
    issue.add_argument("--tenant")
    issue.add_argument(
        "--scope",
        action="append",
        choices=[READ_SCOPE, WRITE_SCOPE, APPROVE_SCOPE],
        help="repeat to add scopes; defaults to freight:read",
    )
    issue.add_argument("--ttl", type=int, default=3600)

    client = subparsers.add_parser("client", help="call a running gateway")
    client.add_argument("--url")
    client.add_argument("--read-token")
    client.add_argument("--admin-token")
    client.add_argument("--case-id")

    demo = subparsers.add_parser("demo", help="run a local server and client demo")
    demo.add_argument("--db")
    demo.add_argument("--manifest")
    demo.add_argument("--case-id")
    demo.add_argument("--host")
    demo.add_argument("--port", type=int)

    return parser


def _serve(args: argparse.Namespace) -> int:
    settings = Settings()
    store = _open_store(settings, db_path=args.db, manifest_path=args.manifest)
    gateway = create_gateway(store, _issuer(settings))
    try:
        if args.transport == "stdio":
            gateway.run(transport="stdio")
        else:
            gateway.run(
                transport="http",
                host=args.host or settings.gateway_host,
                port=args.port or settings.gateway_port,
                path=args.path,
            )
    finally:
        store.close()
    return 0


def _issue(args: argparse.Namespace) -> int:
    settings = Settings()
    scopes = args.scope or [READ_SCOPE]
    token = _issuer(settings).issue(
        subject=args.subject,
        tenant_id=args.tenant or settings.gateway_tenant_id,
        scopes=scopes,
        ttl_seconds=args.ttl,
    )
    print(token)
    return 0


def _client(args: argparse.Namespace) -> int:
    settings = Settings()
    issuer = _issuer(settings)
    read_token = args.read_token or issuer.issue(
        subject="agent-readonly",
        tenant_id=settings.gateway_tenant_id,
        scopes=[READ_SCOPE],
    )
    admin_token = args.admin_token or issuer.issue(
        subject="agent-admin",
        tenant_id=settings.gateway_tenant_id,
        scopes=[READ_SCOPE, WRITE_SCOPE, APPROVE_SCOPE],
    )
    return client_main(
        url=args.url or f"http://{settings.gateway_host}:{settings.gateway_port}/mcp",
        read_token=read_token,
        admin_token=admin_token,
        **({"case_id": args.case_id} if args.case_id else {}),
    )


def _demo(args: argparse.Namespace) -> int:
    settings = Settings()
    host = args.host or settings.gateway_host
    port = args.port or settings.gateway_port
    url = f"http://{host}:{port}/mcp"
    issuer = LocalTokenIssuer(
        secret=settings.gateway_jwt_secret.get_secret_value(),
        issuer=f"http://{host}:{port}",
        audience=settings.gateway_audience,
    )
    store = _open_store(settings, db_path=args.db, manifest_path=args.manifest)
    gateway = create_gateway(store, issuer)
    config = uvicorn.Config(gateway.http_app(), host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.025)
    if not server.started:
        store.close()
        print("error: demo server did not start", file=sys.stderr)
        return 2

    read_token = issuer.issue(
        subject="agent-readonly",
        tenant_id=settings.gateway_tenant_id,
        scopes=[READ_SCOPE],
    )
    admin_token = issuer.issue(
        subject="agent-admin",
        tenant_id=settings.gateway_tenant_id,
        scopes=[READ_SCOPE, WRITE_SCOPE, APPROVE_SCOPE],
    )
    arguments: dict[str, Any] = {
        "url": url,
        "read_token": read_token,
        "admin_token": admin_token,
    }
    if args.case_id:
        arguments["case_id"] = args.case_id
    try:
        return client_main(**arguments)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        store.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        return _serve(args)
    if args.command == "issue":
        return _issue(args)
    if args.command == "client":
        return _client(args)
    return _demo(args)


if __name__ == "__main__":
    raise SystemExit(main())
