"""Endpoints de gestion de politicas de distribucion: crear, simular,
publicar y revertir versiones. Toda escritura requiere rol `policy-admin`,
ya que una politica mal publicada puede exponer o bloquear IOCs hacia un
destino entero; la lectura solo requiere `viewer`.

Autor: Athan Espinoza
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse

from hub.adapters.factory import build_adapter
from hub.api.audit import write_audit
from hub.api.auth import require_role
from hub.api.deps import APIState, get_graphql_client, get_state
from hub.api.errors import APIError
from hub.api.idempotency import with_idempotency
from hub.api.schemas import DiscardRequest, PolicyAssignmentsUpdate, PolicyCreate, SimulateRequest, VersionRequest
from hub.api.routers.feeds import rebuild_all_feeds_for_destination
from hub.destinations_store import get_destination
from hub.policy_simulation import simulate as run_simulation
from hub.policy_store import (
    create_draft,
    delete_draft_version,
    delete_policy,
    get_active_version,
    get_version,
    list_assignments_for_policy,
    list_policy_ids,
    list_versions,
    publish,
    rollback,
    set_policy_assignments,
)
from hub.resync import resync_destination

router = APIRouter(prefix="/admin/api/v1/policies")


def _resync_newly_assigned(state: APIState, policy_id: str, newly_assigned: list[str], graphql_client) -> None:
    # Un destino recien asignado a una politica que YA tenia una version
    # activa/publicada nunca recibe lo que otro destino ya proceso -- el
    # deduplicado global de hub.pipeline es por evento, no por destino (ver
    # docstring de hub/resync.py). Best-effort, mismo criterio que el gate
    # de simulacion en publish_version: si OpenCTI no esta configurado o la
    # llamada falla, no se bloquea la asignacion por eso -- el destino queda
    # asignado igual, y la reconciliacion periodica lo termina poniendo al
    # dia (mas lento, pero converge).
    if not newly_assigned or graphql_client is None:
        return
    active = get_active_version(state.policies_conn, policy_id)
    if active is None:
        return
    since = datetime.now(timezone.utc) - timedelta(days=state.config.backfill_window_days)
    for destination_id in newly_assigned:
        destination = get_destination(state.destinations_conn, destination_id)
        if destination is None:
            continue
        adapter = build_adapter(
            destination, txt_feed_dir=state.config.txt_feed_dir, taxii_conn=state.taxii_conn,
            secrets_conn=state.secrets_conn, cipher=state.secret_cipher, policy=active,
        )
        try:
            resync_destination(
                graphql_client,
                destination=destination,
                source_id=state.config.source_id,
                since=since,
                max_pages=state.config.backfill_max_pages,
                page_size=state.config.backfill_page_size,
                policies_conn=state.policies_conn,
                adapters={destination_id: adapter},
                ledger_conn=state.ledger_conn,
                circuit_breakers=state.circuit_breakers,
                default_ttl_days=state.config.policy_ttl_days,
                delivery_queue_conn=state.delivery_queue_conn,
            )
        except Exception as e:
            print(f"[hub-api] RESYNC_ERROR: destino={destination_id!r} policy={policy_id!r}: {e}", flush=True)


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
                "destination_ids": list_assignments_for_policy(state.policies_conn, policy_id),
            }
        )
    return out


@router.put("/{policy_id}/assignments")
def update_assignments(
    policy_id: str,
    payload: PolicyAssignmentsUpdate,
    request: Request,
    state: APIState = Depends(get_state),
    graphql_client=Depends(get_graphql_client),
    token=Depends(require_role("policy-admin")),
):
    if not list_versions(state.policies_conn, policy_id):
        raise APIError(404, "Not Found", f"policy '{policy_id}' no existe", error_code="policy_not_found")
    for destination_id in payload.destination_ids:
        if get_destination(state.destinations_conn, destination_id) is None:
            raise APIError(
                422, "Unprocessable Entity", f"destination '{destination_id}' no existe", error_code="destination_not_found",
            )

    before = list_assignments_for_policy(state.policies_conn, policy_id)
    set_policy_assignments(state.policies_conn, policy_id, payload.destination_ids)
    # El cambio de asignacion tiene el mismo efecto practico que publicar una
    # nueva version para esos destinos (un destino recien asignado empieza a
    # aplicar esta politica, uno recien sacado deja de hacerlo) -- mismo
    # motivo que rebuild_all_feeds_for_destination en publish/rollback: sin
    # esto, el cambio quedaba guardado pero invisible en los feeds ya
    # materializados hasta el proximo evento real.
    for destination_id in set(before) | set(payload.destination_ids):
        rebuild_all_feeds_for_destination(state, destination_id)
    # Los destinos NUEVOS en el set (no los que ya estaban) se ponen al dia
    # con lo que la politica ya venia entregando -- ver docstring de
    # hub/resync.py y _resync_newly_assigned arriba.
    _resync_newly_assigned(state, policy_id, list(set(payload.destination_ids) - set(before)), graphql_client)

    write_audit(
        request, state, actor=token, action="policy.update_assignments",
        resource_type="policy", resource_id=policy_id,
        before={"destination_ids": before}, after={"destination_ids": payload.destination_ids},
    )
    return {"policy_id": policy_id, "destination_ids": payload.destination_ids}


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
    graphql_client=Depends(get_graphql_client),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    token=Depends(require_role("policy-admin")),
):
    # El efecto mutante se envuelve en un closure (`compute`) para que
    # `with_idempotency` pueda decidir SI ejecutarlo (clave nueva) o
    # devolver la respuesta cacheada de un reintento sin correrlo de nuevo.
    def compute():
        for destination_id in payload.destination_ids:
            if get_destination(state.destinations_conn, destination_id) is None:
                raise APIError(
                    422, "Unprocessable Entity", f"destination '{destination_id}' no existe",
                    error_code="destination_not_found",
                )
        # Se lee ANTES de reasignar: "Editar politica" (UI) crea un draft
        # nuevo con los mismos u otros destinos de una politica que puede
        # llevar horas activa -- sin esta lectura previa, no habria forma de
        # saber cuales de payload.destination_ids son realmente nuevos.
        before = list_assignments_for_policy(state.policies_conn, payload.policy_id)
        version = create_draft(
            state.policies_conn,
            policy_id=payload.policy_id,
            allowed_iocs=payload.allowed_iocs,
            ttl_days=payload.ttl_days,
            max_records=payload.max_records,
        )
        if payload.destination_ids:
            set_policy_assignments(state.policies_conn, version.policy_id, payload.destination_ids)
            _resync_newly_assigned(
                state, version.policy_id, list(set(payload.destination_ids) - set(before)), graphql_client,
            )
        write_audit(
            request, state, actor=token, action="policy.create_draft",
            resource_type="policy", resource_id=f"{version.policy_id}@v{version.version}",
            after={**version.model_dump(mode="json"), "destination_ids": payload.destination_ids},
        )
        return 201, {**version.model_dump(mode="json"), "destination_ids": payload.destination_ids}

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
        # (bug real reportado por el operador, 2026-08-18). Una politica
        # puede servir a varios destinos (2026-08-18): se reconstruyen todos
        # los que la tengan asignada, no solo uno.
        for destination_id in list_assignments_for_policy(state.policies_conn, policy_id):
            rebuild_all_feeds_for_destination(state, destination_id)
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
        for destination_id in list_assignments_for_policy(state.policies_conn, policy_id):
            rebuild_all_feeds_for_destination(state, destination_id)
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
