"""spec/08-API-SECURITY.md endpoints de `/policies`; rol `policy-admin`
("crear, simular y publicar politicas") para toda escritura, `viewer` para
lectura.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from hub.api.audit import write_audit
from hub.api.auth import require_role
from hub.api.deps import APIState, get_state
from hub.api.errors import APIError
from hub.api.idempotency import with_idempotency
from hub.api.schemas import PolicyCreate, SimulateRequest, VersionRequest
from hub.destinations_store import get_destination
from hub.policy_simulation import simulate as run_simulation
from hub.policy_store import (
    create_draft,
    get_active_version,
    get_version,
    list_policy_ids,
    list_versions,
    publish,
    rollback,
)

router = APIRouter(prefix="/admin/api/v1/policies")


@router.get("")
def list_all(state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    out = []
    for policy_id in list_policy_ids(state.policies_conn):
        active = get_active_version(state.policies_conn, policy_id)
        versions = list_versions(state.policies_conn, policy_id)
        out.append(
            {
                "policy_id": policy_id,
                "active_version": active.version if active else None,
                "version_count": len(versions),
            }
        )
    return out


@router.get("/{policy_id}/versions")
def versions(policy_id: str, state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    return [v.model_dump(mode="json") for v in list_versions(state.policies_conn, policy_id)]


@router.post("", status_code=201)
def create(
    payload: PolicyCreate,
    request: Request,
    state: APIState = Depends(get_state),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    token=Depends(require_role("policy-admin")),
):
    def compute():
        if get_destination(state.destinations_conn, payload.destination_id) is None:
            raise APIError(
                422, "Unprocessable Entity", f"destination '{payload.destination_id}' no existe",
                error_code="destination_not_found",
            )
        version = create_draft(
            state.policies_conn,
            policy_id=payload.policy_id,
            destination_id=payload.destination_id,
            allowed_iocs=payload.allowed_iocs,
            ttl_days=payload.ttl_days,
        )
        write_audit(
            request, state, actor=token, action="policy.create_draft",
            resource_type="policy", resource_id=f"{version.policy_id}@v{version.version}",
            after=version.model_dump(mode="json"),
        )
        return 201, version.model_dump(mode="json")

    status_code, body = with_idempotency(
        state, key=idempotency_key, endpoint="POST /policies", payload=payload.model_dump(mode="json"), compute=compute
    )
    return JSONResponse(status_code=status_code, content=body)


@router.post("/{policy_id}/simulate")
def simulate(
    policy_id: str,
    payload: SimulateRequest,
    state: APIState = Depends(get_state),
    _token=Depends(require_role("policy-admin")),
):
    versions_list = list_versions(state.policies_conn, policy_id)
    if not versions_list:
        raise APIError(404, "Not Found", f"policy '{policy_id}' no existe", error_code="policy_not_found")
    candidate = versions_list[-1]  # ultima version creada (tipicamente el draft a evaluar)
    active = get_active_version(state.policies_conn, policy_id)

    report = run_simulation(
        candidate=candidate,
        active=active,
        sample_envelopes=payload.sample,
        graphql_client=state.graphql_client if payload.sample is None else None,
        sample_size=payload.sample_size,
    )
    return {"policy_id": policy_id, "candidate_version": candidate.version, **report}


@router.post("/{policy_id}/publish")
def publish_version(
    policy_id: str,
    payload: VersionRequest,
    request: Request,
    state: APIState = Depends(get_state),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    token=Depends(require_role("policy-admin")),
):
    def compute():
        previously_active = get_active_version(state.policies_conn, policy_id)
        if get_version(state.policies_conn, policy_id, payload.version) is None:
            raise APIError(404, "Not Found", f"policy '{policy_id}' version {payload.version} no existe", error_code="policy_version_not_found")
        version = publish(state.policies_conn, policy_id, payload.version)
        write_audit(
            request, state, actor=token, action="policy.publish",
            resource_type="policy", resource_id=f"{policy_id}@v{payload.version}",
            before={"active_version": previously_active.version if previously_active else None},
            after={"active_version": version.version},
            reason=payload.reason,
        )
        return 200, version.model_dump(mode="json")

    status_code, body = with_idempotency(
        state,
        key=idempotency_key,
        endpoint=f"POST /policies/{policy_id}/publish",
        payload=payload.model_dump(mode="json"),
        compute=compute,
    )
    return JSONResponse(status_code=status_code, content=body)


@router.post("/{policy_id}/rollback")
def rollback_version(
    policy_id: str,
    payload: VersionRequest,
    request: Request,
    state: APIState = Depends(get_state),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    token=Depends(require_role("policy-admin")),
):
    def compute():
        previously_active = get_active_version(state.policies_conn, policy_id)
        if get_version(state.policies_conn, policy_id, payload.version) is None:
            raise APIError(404, "Not Found", f"policy '{policy_id}' version {payload.version} no existe", error_code="policy_version_not_found")
        version = rollback(state.policies_conn, policy_id, payload.version)
        write_audit(
            request, state, actor=token, action="policy.rollback",
            resource_type="policy", resource_id=f"{policy_id}@v{payload.version}",
            before={"active_version": previously_active.version if previously_active else None},
            after={"active_version": version.version},
            reason=payload.reason,
        )
        return 200, version.model_dump(mode="json")

    status_code, body = with_idempotency(
        state,
        key=idempotency_key,
        endpoint=f"POST /policies/{policy_id}/rollback",
        payload=payload.model_dump(mode="json"),
        compute=compute,
    )
    return JSONResponse(status_code=status_code, content=body)
