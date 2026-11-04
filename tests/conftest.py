from __future__ import annotations

import shutil
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from forge_agent_mcp.corpus.generator import GenerationResult, generate_corpus
from forge_agent_mcp.corpus.manifest import Manifest, load_manifest
from forge_agent_mcp.gateway.auth import LocalTokenIssuer
from forge_agent_mcp.gateway.server import create_gateway
from forge_agent_mcp.gateway.store import GatewayStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "corpus" / "manifest.json"

ISSUER = "http://127.0.0.1:8765"
AUDIENCE = "forge-freight-gateway"
SECRET = "gateway-http-test-secret-at-least-32-bytes"
TENANT = "tenant_northwind"
OTHER_TENANT = "tenant_other"


@pytest.fixture(scope="session")
def generated_corpus(tmp_path_factory: pytest.TempPathFactory) -> tuple[Manifest, GenerationResult]:
    manifest = load_manifest(MANIFEST_PATH)
    root = tmp_path_factory.mktemp("generated-corpus") / "out"
    return manifest, generate_corpus(manifest, root)


@pytest.fixture
def provider_payload():
    def build(truth, document) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for field in truth.expected_fields:
            document_type, key = field.field.split(".", 1)
            if document_type != document.document_type:
                continue
            payload[key] = {
                "value": field.value,
                "citation": {
                    "document_id": field.citation.document_id,
                    "page": field.citation.page,
                    "bbox": field.citation.bbox,
                    "quote": field.value,
                },
                "confidence": 0.9,
            }
        return payload

    return build


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def seeded_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("gateway-seed") / "gateway.db"
    store = GatewayStore(path)
    store.initialize()
    store.seed_demo(load_manifest(MANIFEST_PATH), tenant_id=TENANT)
    store.close()
    return path


@pytest.fixture
def seeded_db(tmp_path: Path, seeded_template: Path) -> Path:
    path = tmp_path / "gateway.db"
    shutil.copy2(seeded_template, path)
    return path


@dataclass
class RunningGateway:
    url: str
    issuer: LocalTokenIssuer
    store: GatewayStore

    def token(
        self,
        *,
        subject: str = "agent-underwriter",
        tenant_id: str = TENANT,
        scopes: list[str] | None = None,
    ) -> str:
        return self.issuer.issue(
            subject=subject,
            tenant_id=tenant_id,
            scopes=scopes or ["freight:read"],
        )


@pytest.fixture
def gateway(seeded_db: Path) -> RunningGateway:
    store = GatewayStore(seeded_db)
    issuer = LocalTokenIssuer(secret=SECRET, issuer=ISSUER, audience=AUDIENCE)
    gateway_server = create_gateway(store, issuer)
    port = _free_port()
    config = uvicorn.Config(
        gateway_server.http_app(),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.025)
    if not server.started:
        raise RuntimeError("gateway did not start")
    running = RunningGateway(
        url=f"http://127.0.0.1:{port}/mcp",
        issuer=issuer,
        store=store,
    )
    try:
        yield running
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        store.close()
