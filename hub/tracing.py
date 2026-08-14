"""Instrumentacion OpenTelemetry: 7 spans principales --
`opencti.stream.receive`, `opencti.event.normalize`, `policy.evaluate`,
`delivery.render`, `delivery.send`, `delivery.acknowledge`, `feed.rebuild`
-- con `event_id`/`stix_id`/`delivery_id` como atributos cuando
corresponda, para poder correlacionar un mismo evento a lo largo de todo
el flujo.

Aditivo y opcional por diseño: la observabilidad avanzada (trazas via un
Collector externo) no debe ser un requisito para poder operar el Hub. Sin
`OTEL_EXPORTER_OTLP_ENDPOINT` configurado, el tracer global de OpenTelemetry
ya es un no-op por diseño de la propia libreria -- el resto del codigo
instrumentado (`span(...)` de abajo) no necesita ningun guard condicional
para funcionar sin el Collector.

Autor: Athan Espinoza
"""
from contextlib import contextmanager
from typing import Iterator, Optional

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_TRACER_NAME = "opencti-ioc-hub"


def configure_tracing(config) -> None:
    """Instala un TracerProvider real solo si hay endpoint configurado. Se
    llama una vez al arrancar `hub.service`/`hub.api` (idempotente en la
    practica: cada proceso lo llama una sola vez)."""
    if not config.otel_exporter_endpoint:
        # Sin endpoint no hay a donde exportar spans: se deja el tracer
        # global por defecto (no-op) en vez de instalar un BatchSpanProcessor
        # que solo acumularia spans sin destino.
        return
    provider = TracerProvider(resource=Resource.create({"service.name": config.otel_service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=config.otel_exporter_endpoint)))
    trace.set_tracer_provider(provider)


def get_tracer():
    return trace.get_tracer(_TRACER_NAME)


@contextmanager
def span(
    name: str,
    *,
    event_id: Optional[str] = None,
    stix_id: Optional[str] = None,
    delivery_id: Optional[str] = None,
    **extra_attributes: str,
) -> Iterator[None]:
    """Wrapper delgado sobre `start_as_current_span` con los 3 atributos de
    correlacion (event_id/stix_id/delivery_id) que interesa propagar cuando
    corresponda -- no todos los spans tienen los 3 (por ejemplo
    `feed.rebuild` no tiene un event_id puntual, es por subtipo/destino: usa
    `**extra_attributes` para esos)."""
    attributes = dict(extra_attributes)
    if event_id is not None:
        attributes["event_id"] = event_id
    if stix_id is not None:
        attributes["stix_id"] = stix_id
    if delivery_id is not None:
        attributes["delivery_id"] = delivery_id
    with get_tracer().start_as_current_span(name, attributes=attributes):
        yield
