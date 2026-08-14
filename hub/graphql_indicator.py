"""Adaptador GraphQL indicator -> envelope STIX-like.

Backfill y reconciliacion usan GraphQL, no el Live Stream, asi que el shape
de respuesta es distinto (campos directos como `x_opencti_main_observable_type`
en vez de `extensions.*`). En vez de escribir un segundo clasificador,
este modulo re-empaqueta el nodo GraphQL en el mismo envelope que ya
entiende `hub.normalize.normalize_stix_indicator`, para que exista un unico
punto de clasificacion de IOC en todo el sistema.

Nota de alcance: los nombres de campo GraphQL de abajo siguen la
documentacion publica de OpenCTI, pero no fueron validados contra una
instancia real en este entorno; queda pendiente confirmarlos y versionar
el adaptador contra la version de OpenCTI soportada en cuanto se pueda
correr contra una instancia real.

Autor: Athan Espinoza
"""
INDICATOR_FIELDS = """
    id
    pattern
    pattern_type
    created
    modified
    valid_until
    revoked
    confidence
    x_opencti_score
    x_opencti_detection
    x_opencti_main_observable_type
    objectLabel {
        edges { node { value } }
    }
    objectMarking {
        edges { node { definition } }
    }
    observables {
        edges { node { observable_value } }
    }
"""

BACKFILL_INDICATORS_QUERY = f"""
query BackfillIndicators($first: Int!, $after: ID, $orderBy: IndicatorsOrdering, $orderMode: OrderingMode) {{
  indicators(first: $first, after: $after, orderBy: $orderBy, orderMode: $orderMode) {{
    edges {{
      node {{ {INDICATOR_FIELDS} }}
    }}
    pageInfo {{
      endCursor
      hasNextPage
    }}
  }}
}}
"""

# Usado para re-derivar el indicador por stix_id al reintentar una entrega,
# en vez de cachear el payload completo: OpenCTI sigue siendo la fuente de
# verdad, asi que un reintento siempre relee el estado actual del indicador.
GET_INDICATOR_QUERY = f"""
query GetIndicator($id: String!) {{
  indicator(id: $id) {{ {INDICATOR_FIELDS} }}
}}
"""


def _edge_values(node: dict, field: str, value_key: str) -> list[str]:
    # GraphQL Relay-style connections envuelven cada valor en edges[].node;
    # este helper desenvuelve eso una sola vez para los tres campos (labels,
    # markings, observables) que comparten la misma forma.
    edges = ((node.get(field) or {}).get("edges")) or []
    values = []
    for edge in edges:
        n = (edge or {}).get("node") or {}
        v = n.get(value_key)
        if v:
            values.append(v)
    return values


def indicator_node_to_envelope(node: dict, action: str) -> dict:
    """Convierte un nodo `Indicator` de GraphQL en el envelope que ya consume
    `hub.normalize.normalize_stix_indicator` (mismo shape que produce el Live
    Stream: action + data.data + extensions.main_observable_type/observable_values)."""
    labels = _edge_values(node, "objectLabel", "value")
    # normalize._split_labels_and_markings ya separa por prefijo "tlp:"; no se
    # reescriben marcados no-TLP (PAP, statement, etc.) para no inventarles semantica.
    markings = _edge_values(node, "objectMarking", "definition")
    observable_values = [
        {"value": v} for v in _edge_values(node, "observables", "observable_value")
    ]

    main_observable_type = node.get("x_opencti_main_observable_type")

    stix: dict = {
        "id": node["id"],
        "type": "indicator",
        "pattern": node.get("pattern"),
        "pattern_type": node.get("pattern_type"),
        "created": node.get("created"),
        "modified": node.get("modified"),
        "valid_until": node.get("valid_until"),
        "revoked": bool(node.get("revoked", False)),
        "confidence": node.get("confidence"),
        "labels": [*labels, *markings],
        "extensions": {
            "extension-definition--opencti": {
                "extension_type": "property-extension",
                "score": node.get("x_opencti_score"),
                "detection": bool(node.get("x_opencti_detection", False)),
                "main_observable_type": main_observable_type,
                "observable_values": observable_values,
            }
        },
    }

    return {"action": action, "data": {"data": stix}}


def indicators_since_to_envelopes(nodes: list[dict], *, action: str = "create") -> list[dict]:
    return [indicator_node_to_envelope(node, action) for node in nodes]
