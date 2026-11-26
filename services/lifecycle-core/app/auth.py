from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Actor:
    username: str
    roles: tuple[str, ...]


def require_actor(request: Request) -> Actor:
    """Resolve the trusted server-side caller.

    Browser JavaScript never calls lifecycle-core directly. The portal backend
    sets these headers after validating the Keycloak session. The optional
    shared token prevents unrelated in-cluster workloads from calling the API.
    """
    expected = os.environ.get("LIFECYCLE_INTERNAL_TOKEN")
    if expected and request.headers.get("x-lifecycle-token") != expected:
        raise HTTPException(status_code=401, detail="Invalid lifecycle internal token")

    username = request.headers.get("x-lifecycle-actor", "anonymous")
    roles = tuple(
        role.strip()
        for role in request.headers.get("x-lifecycle-roles", "").split(",")
        if role.strip()
    )
    return Actor(username=username, roles=roles)
