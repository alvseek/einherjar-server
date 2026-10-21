"""The write face: the CLI records a consult here, and nothing else does.

Authentication and the tenant come from the token, never from the body: a caller cannot name a
user id, so a row cannot be written into somebody else's registry. The contract header is checked
so a CLI from before a breaking change gets an upgrade instruction instead of a silent wrong write.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from einherjar.auth.verifier import verify_bearer

#: The wire contract this server speaks. A client that does not name it is not assumed to speak it.
CONTRACT = "1"
CONTRACT_HEADER = "x-einherjar-contract"

SessionStatus = Literal["active", "completed", "failed"]


class SessionCreate(BaseModel):
    """What a CLI sends when a consult starts. No user id, deliberately."""

    machine: str = Field(min_length=1)
    session_ref: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    project: str | None = None
    machine_label: str | None = None
    domain: str | None = None
    theme: str | None = None


class SessionUpdate(BaseModel):
    """What a CLI sends when the turn ends."""

    status: SessionStatus
    error: str | None = None


async def require_caller(request: Request) -> str:
    """The tenant this call writes as, from the verified token and nowhere else."""
    verifier = request.app.state.verifier
    if verifier is None:
        raise HTTPException(
            status_code=401,
            detail=(
                "authentication required: this server has no issuer configured, "
                "so it has no tenant"
            ),
        )
    token = await verify_bearer(verifier, request.headers.get("authorization"))
    if token is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return request.app.state.resolver.for_token(token)


def require_contract(request: Request) -> None:
    """Refuse a client that does not name the contract it speaks."""
    spoken = request.headers.get(CONTRACT_HEADER)
    if spoken != CONTRACT:
        raise HTTPException(
            status_code=400,
            detail=(
                f"contract {spoken or 'missing'} is not accepted; this server speaks {CONTRACT}. "
                "Upgrade the einherjar CLI."
            ),
        )


router = APIRouter()


@router.post("/fleet/sessions")
async def create_session(
    payload: SessionCreate,
    request: Request,
    user_id: str = Depends(require_caller),
    _: None = Depends(require_contract),
) -> dict:
    """Create or refresh the row for one consult. Idempotent on the natural key."""
    session = request.app.state.repository.upsert_session(
        user_id=user_id,
        **payload.model_dump(),
    )
    return asdict(session)


@router.patch("/fleet/sessions/{session_uuid}")
async def update_session(
    session_uuid: str,
    payload: SessionUpdate,
    request: Request,
    user_id: str = Depends(require_caller),
    _: None = Depends(require_contract),
) -> dict:
    """Set the row's status, for the caller's own tenant only."""
    session = request.app.state.repository.update_session(
        uuid=session_uuid,
        user_id=user_id,
        status=payload.status,
        error=payload.error,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="no such session for this caller")
    return asdict(session)


@router.get("/fleet/sessions/{session_uuid}")
async def read_session(
    session_uuid: str,
    request: Request,
    user_id: str = Depends(require_caller),
) -> dict:
    """One row, for the flush and for diagnosis."""
    session = request.app.state.repository.get_session(uuid=session_uuid, user_id=user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="no such session for this caller")
    return asdict(session)
