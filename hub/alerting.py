"""Orquestacion de notificacion compartida entre `hub/api/routers/alerts.py`
(evaluacion completa via HTTP) y el tick periodico de `hub/service.py`
(solo las reglas que dependen de estado in-process): construir los canales
configurados y notificar respetando cooldown/severidad minima. Separado de
`hub/alerting_store.py` (solo persistencia SQLite) y `hub/alert_rules.py`
(funciones puras) para que ninguno de esos dos dependa de `smtplib`/
`requests`.

Autor: Athan Espinoza
"""
from hub.alert_channels import EmailAlertChannel, WebhookAlertChannel
from hub.alerting_store import mark_notified, should_notify
from hub.config import HubConfig

# Mapea severidad a un rango numerico para poder comparar "al menos tan
# severo como X" con un simple >=, en vez de tener que ordenar strings.
_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def build_channels(config: HubConfig, *, secrets_conn=None, cipher=None) -> list:
    # Cada canal se empareja con su propio umbral minimo de severidad para
    # que, por ejemplo, el webhook pueda recibir todo pero el email solo
    # criticas.
    channels = []
    if config.alert_smtp_host:
        channels.append(
            (
                EmailAlertChannel(
                    host=config.alert_smtp_host,
                    port=config.alert_smtp_port,
                    sender=config.alert_email_from or "hub@localhost",
                    recipients=config.alert_email_to,
                    credential_ref=config.alert_email_credential_ref,
                    secrets_conn=secrets_conn,
                    cipher=cipher,
                ),
                config.alert_email_min_severity,
            )
        )
    if config.alert_webhook_url:
        channels.append(
            (
                WebhookAlertChannel(
                    url=config.alert_webhook_url,
                    credential_ref=config.alert_webhook_credential_ref,
                    secrets_conn=secrets_conn,
                    cipher=cipher,
                ),
                config.alert_webhook_min_severity,
            )
        )
    return channels


def notify_alerts(conn, alerts: list, channels: list, *, cooldown_seconds: int) -> None:
    for alert in alerts:
        # El cooldown evita reenviar la misma alerta en cada tick mientras
        # la condicion siga activa (spam de notificaciones).
        if not should_notify(alert, cooldown_seconds=cooldown_seconds):
            continue
        notified = False
        for channel, min_severity in channels:
            # `.get(..., 0)` deja pasar valores de severidad desconocidos
            # (tratados como "info") en vez de lanzar una excepcion aca.
            if _SEVERITY_RANK.get(alert.severity, 0) >= _SEVERITY_RANK.get(min_severity, 0):
                if channel.send(alert):
                    notified = True
        # Solo se marca como notificada si al menos un canal la entrego; si
        # todos fallan, se reintenta en el proximo tick (dentro del cooldown).
        if notified:
            mark_notified(conn, alert.alert_id)
