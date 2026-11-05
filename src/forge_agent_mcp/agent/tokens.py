"""Transient approval-token vault.

The agent may hold the console-minted approval token and the ``freight:approve``
bearer token only for the duration of one execute node. The graph checkpoint
must never contain either secret, so the runner keeps them in this in-memory
vault keyed by an opaque reference. If the process restarts, the vault is empty
by construction and the reviewer is asked for a fresh approval.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class VaultedTokens:
    approval_token: str
    approval_jwt: str


class ApprovalTokenVault:
    """Thread-safe, single-use token holder for one graph execution."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: dict[str, VaultedTokens] = {}

    def put(self, reference: str, *, approval_token: str, approval_jwt: str) -> None:
        with self._lock:
            self._tokens[reference] = VaultedTokens(
                approval_token=approval_token,
                approval_jwt=approval_jwt,
            )

    def take(self, reference: str) -> VaultedTokens | None:
        with self._lock:
            return self._tokens.pop(reference, None)

    def discard(self, reference: str) -> None:
        with self._lock:
            self._tokens.pop(reference, None)

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()


__all__ = ["ApprovalTokenVault", "VaultedTokens"]
