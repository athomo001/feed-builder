"""Endpoints de gestion de politicas de distribucion: crear, simular,
publicar y revertir versiones. Toda escritura requiere rol `policy-admin`,
ya que una politica mal publicada puede exponer o bloquear IOCs hacia un
destino entero; la lectura solo requiere `viewer`.

Autor: Athan Espinoza
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse

from hub.api.audit import write_audit
from hub.api.auth import require_role
from hub.api.deps import APIState, get_graphql_client, get_state
from hub.api.errors import APIError
from hub.api.idempotency import with_idempotency
from hub.api.schemas import DiscardRequest, PolicyCreate, SimulateRequest, VersionRequest
from hub.api.routers.feeds import rebuild_all_feeds_for_destination
from hub.destinations_store import get_destination
from hub.policy_simulation import simulate as run_simulation
from hub.policy_store import (
    create_draft,
    delete_draft_version,
    delete_policy,
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


# Borrado real de TODAS las versiones de una politica, sin importar si
# alguna estuvo publicada -- pedido explicitamente por el operador
# (2026-08-18): "si quiero la borro y hago una nueva". Distinto del borrado
# de un solo draft (endpoint de abajo), que protege el historial de
# auditoria; este es irreversible a proposito. Ver rationale completo en
# hub/policy_store.py::delete_policy.
@router.delete("/{policy_id}", status_code=204)
def delete_policy_endpoint(
    policy_id: str,
    payload: DiscardRequest,
    request: Request,
    state: APIState = Depends(get_state),
    token=Depends(require_role("policy-admin")),
):
    existing = list_versions(state.policies_conn, policy_id)
    if not existing:
        raise APIError(404, "Not Found", f"policy '{policy_id}' no existe", error_code="policy_not_found")
    delete_policy(state.policies_conn, policy_id)
    write_audit(
        request, state, actor=token, action="policy.delete",
        resource_type="policy", resource_id=policy_id,
        before={"version_count": len(existing)}, reason=payload.reason,
    )
    return Response(status_code=204)


# Solo borra un draft que TODAVIA nunca se publico (ver rationale en
# hub/policy_store.py::delete_draft_version) -- una version que alguna vez
# estuvo activa queda referenciada por policy_version en el ledger, y
# borrarla rompería esa trazabilidad de auditoria.
@router.delete("/{policy_id}/versions/{version}", status_code=204)
def delete_version(
    policy_id: str,
    version: int,
    payload: DiscardRequest,
    request: Request,
    state: APIState = Depends(get_state),
    token=Depends(require_role("policy-admin")),
):
    target = get_version(state.policies_conn, policy_id, version)
    if target is None:
        raise APIError(404, "Not Found", f"policy '{policy_id}' version {version} no existe", error_code="policy_version_not_found")
    if target.status != "draft":
        raise APIError(
            409, "Conflict",
            f"policy '{policy_id}' version {version} esta en estado '{target.status}' -- solo se puede borrar un draft "
            "que nunca se publico (una version que estuvo activa queda referenciada en el ledger).",
            error_code="not_a_draft",
        )
    delete_draft_version(state.policies_conn, policy_id, version)
    write_audit(
        request, state, actor=token, action="policy.delete_draft",
        resource_type="policy", resource_id=f"{policy_id}@v{version}",
        before={"status": target.status}, after=None, reason=payload.reason,
    )
    # 204 No Content nunca debe llevar cuerpo (JSONResponse(content=None)
    # serializa "null" como body, lo que produce un Content-Length que no
    # coincide con lo que un 204 real debe mandar).
    return Response(status_code=204)


@router.post("", status_code=201)
def create(
    payload: PolicyCreate,
    request: Request,
    state: APIState = Depends(get_state),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    token=Depends(require_role("policy-admin")),
):
    # El efecto mutante se envuelve en un closure (`compute`) para que
    # `with_idempotency` pueda decidir SI ejecutarlo (clave nueva) o
    # devolver la respuesta cacheada de un reintento sin correrlo de nuevo.
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
            max_records=payload.max_records,
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
    graphql_client=Depends(get_graphql_client),
    _token=Depends(require_role("policy-admin")),
):
    versions_list = list_versions(state.policies_conn, policy_id)
    if not versions_list:
        raise APIError(404, "Not Found", f"policy '{policy_id}' no existe", error_code="policy_not_found")
    # Se simula la ultima version creada, no la activa: el flujo normal es
    # crear un draft y simularlo antes de decidir si publicarlo, asi que la
    # version mas nueva es la candidata por defecto a evaluar.
    candidate = versions_list[-1]
    active = get_active_version(state.policies_conn, policy_id)

    if payload.sample is None and graphql_client is None:
        raise APIError(
            409, "Conflict", "OpenCTI no esta configurado todavia (o mandar 'sample' para simular sin el)",
            error_code="opencti_not_configured",
        )

    # Si el caller no manda una muestra propia, se consulta OpenCTI en vivo
    # para tener datos reales con los que simular; si manda `sample`, se usa
    # esa (por ejemplo en tests) y no hace falta el cliente GraphQL.
    report = run_simulation(
        candidate=candidate,
        active=active,
        sample_envelopes=payload.sample,
        graphql_client=graphql_client if payload.sample is None else None,
        sample_size=payload.sample_size,
    )
    return {"policy_id": policy_id, "candidate_version": candidate.version, **report}


@router.post("/{policy_id}/publish")
def publish_version(
    policy_id: str,
    payload: VersionRequest,
    request: Request,
    state: APIState = Depends(get_state),
    graphql_client=Depends(get_graphql_client),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    token=Depends(require_role("policy-admin")),
):
    def compute():
        previously_active = get_active_version(state.policies_conn, policy_id)
        candidate = get_version(state.policies_conn, policy_id, payload.version)
        if candidate is None:
            raise APIError(404, "Not Found", f"policy '{policy_id}' version {payload.version} no existe", error_code="policy_version_not_found")

        # spec/04 "Exigir confirmacion para publicar si el volumen cambia de
        # forma significativa": antes de aplicar, se simula la candidata
        # contra la version activa con una muestra en vivo. Si el cambio
        # supera el umbral y el caller no mando confirm_significant_change,
        # se rechaza en vez de publicar a ciegas -- el caller reintenta el
        # mismo POST con ese campo en true una vez que decide seguir.
        # Best-effort: si no hay OpenCTI configurado o la simulacion falla
        # por cualquier motivo, no se bloquea el publish por eso (el gate es
        # una red de seguridad, no una dependencia dura de OpenCTI).
        if not payload.confirm_significant_change and graphql_client is not None:
            try:
                report = run_simulation(candidate=candidate, active=previously_active, graphql_client=graphql_client)
            except Exception:
                report = None
            if report is not None and report["threshold_alert"]:
                raise APIError(
                    409,
                    "Conflict",
                    f"Publicar {policy_id} v{payload.version} cambiaria el volumen aceptado un {report['delta_pct']}% "
                    f"respecto a la version activa ({report['before']['accepted']} -> {report['after']['accepted']} "
                    f"aceptados sobre una muestra de {report['sample_size']}). Repeti el publish con "
                    "confirm_significant_change=true para confirmar.",
                    error_code="significant_volume_change",
                )

        version = publish(state.policies_conn, policy_id, payload.version)
        # Sin esto, un cambio de politica (TTL, cantidad, tipos permitidos)
        # quedaba guardado pero invisible en los feeds ya materializados
        # hasta que llegara un evento nuevo o alguien reconstruyera a mano
        # (bug real reportado por el operador, 2026-08-18).
        rebuild_all_feeds_for_destination(state, version.destination_id)
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
        rebuild_all_feeds_for_destination(state, version.destination_id)
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
