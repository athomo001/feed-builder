"""Adaptador GraphQL StixCyberObservable -> envelope STIX-like.

La mayoria del IOC real de una instancia OpenCTI puede vivir como Observable
crudo (`stixCyberObservables`), sin nunca promoverse a un Indicator con
patron STIX -- confirmado contra una instancia real: 144868 IPv4-Addr como
Observable contra apenas 149 como Indicator, mismo orden de magnitud para
StixFile/Domain-Name/Url. `hub/graphql_indicator.py` (y todo lo que dependia
de el: backfill, reconciliacion, Live Stream) solo miraba `indicators`, asi
que esa mayoria nunca llegaba a los feeds.

En vez de escribir un clasificador aparte, este modulo sintetiza un STIX
"indicator-like" (mismo shape que `indicator_node_to_envelope`: `extensions.
*.main_observable_type` + `observable_values`, y para StixFile un `pattern`
`hashes.'ALGO' = 'valor'`) para poder reusar `hub.normalize.classify_stix`
sin cambios -- unico punto de clasificacion de IOC en todo el sistema, igual
que para Indicators.

Un StixFile puede traer varios algoritmos de hash a la vez (MD5 Y SHA-256 en
el mismo observable, por ejemplo): a diferencia del Indicator (que por regex
solo puede rescatar el primero que aparece en el patron), cada algoritmo acá
se emite como su propio envelope -- por eso `observable_node_to_envelopes`
devuelve una lista, no un unico envelope.

Autor: Athan Espinoza
"""
from hub.graphql_indicator import BACKFILL_SUPPORTED_OBSERVABLE_TYPES, _plain_values

OBSERVABLE_FIELDS = """
    id
    standard_id
    entity_type
    observable_value
    created_at
    updated_at
    x_opencti_score
    objectLabel {
        value
    }
    objectMarking {
        definition
    }
    ... on StixFile {
        hashes {
            algorithm
            hash
        }
    }
"""

BACKFILL_OBSERVABLES_QUERY = f"""
query BackfillObservables($first: Int!, $after: ID, $orderBy: StixCyberObservablesOrdering, $orderMode: OrderingMode, $types: [String]) {{
  stixCyberObservables(first: $first, after: $after, orderBy: $orderBy, orderMode: $orderMode, types: $types) {{
    edges {{
      node {{ {OBSERVABLE_FIELDS} }}
    }}
    pageInfo {{
      endCursor
      hasNextPage
    }}
  }}
}}
"""

# `types` filtra server-side por entity_type (mas simple que armar un
# FilterGroup para esto, ver hub/graphql_indicator.py::BACKFILL_ACTIVE_ONLY_FILTERS
# para el motivo equivalente del lado Indicator): mismo catalogo de tipos
# soportados que Indicator, importado en vez de duplicado para que agregar
# un tipo nuevo a `hub.normalize` alcance con tocarlo en un solo lugar.
BACKFILL_OBSERVABLE_TYPES = BACKFILL_SUPPORTED_OBSERVABLE_TYPES


def _synthetic_stix(*, synthetic_id: str, main_observable_type: str, value: str, pattern, labels, markings, created, modified, score) -> dict:
    return {
        "id": synthetic_id,
        "type": main_observable_type.lower(),
        "pattern": pattern,
        "created": created,
        "modified": modified,
        # Un Observable crudo no tiene "revocado"/"confianza" propios (son
        # campos de opinion, especificos de Indicator) -- se normalizan a
        # los mismos defaults "neutros" que ya usa normalize.py para un
        # campo ausente (revoked=False, confidence=0 via `None`).
        "revoked": False,
        "confidence": None,
        "labels": [*labels, *markings],
        "extensions": {
            "extension-definition--opencti": {
                "extension_type": "property-extension",
                "score": score,
                "detection": False,
                "main_observable_type": main_observable_type,
                "observable_values": [{"value": value}],
            }
        },
    }


def observable_node_to_envelopes(node: dict, action: str) -> list[dict]:
    """Convierte un nodo `StixCyberObservable` de GraphQL en 0+ envelopes
    (uno por algoritmo de hash si es StixFile, uno solo para el resto) con
    el mismo shape que ya consume `hub.normalize.normalize_stix_indicator`."""
    labels = _plain_values(node, "objectLabel", "value")
    markings = _plain_values(node, "objectMarking", "definition")
    entity_type = node.get("entity_type")
    stix_id = node.get("standard_id") or node["id"]
    created = node.get("created_at")
    modified = node.get("updated_at")
    score = node.get("x_opencti_score")

    envelopes = []

    if entity_type == "StixFile":
        for h in node.get("hashes") or []:
            algorithm, value = h.get("algorithm"), h.get("hash")
            if not algorithm or not value:
                continue
            # Mismo formato de patron que genera OpenCTI para un Indicator
            # de archivo (ver hub/graphql_indicator.py): reusa el regex de
            # `classify_stix` con confianza 1.0 en vez de caer al camino de
            # "adivinar el algoritmo por el largo del hash" (confianza 0.6)
            # -- el algoritmo ya lo dice `hashes[].algorithm`, no hace falta
            # adivinar nada.
            pattern = f"[file:hashes.'{algorithm}' = '{value}']"
            envelopes.append({
                "action": action,
                "data": {"data": _synthetic_stix(
                    synthetic_id=f"{stix_id}/{algorithm.lower()}",
                    main_observable_type="StixFile",
                    value=value,
                    pattern=pattern,
                    labels=labels, markings=markings, created=created, modified=modified, score=score,
                )},
            })
        return envelopes

    value = node.get("observable_value")
    if not value:
        return envelopes
    envelopes.append({
        "action": action,
        "data": {"data": _synthetic_stix(
            synthetic_id=stix_id,
            main_observable_type=entity_type,
            value=value,
            pattern=None,
            labels=labels, markings=markings, created=created, modified=modified, score=score,
        )},
    })
    return envelopes
