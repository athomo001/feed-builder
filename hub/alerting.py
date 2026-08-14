"""Orquestacion de notificacion compartida entre `hub/api/routers/alerts.py`
(evaluacion completa via HTTP) y el tick periodico de `hub/service.py`
(solo las reglas que dependen de estado in-process): construir los canales
configurados y notificar respetando cooldown/severidad minima. Separado de
`hub/alerting_store.py` (solo persistencia SQLite) y `hub/alert_rules.py`
(funciones puras) para que ninguno de esos dos dependa de `smtplib`/
`requests`.
"""
from hub.alert_channels import EmailAlertChannel, WebhookAlertChannel
from hub.alerting_store import mark_notified, should_notify
from hub.config import HubConfig

_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def build_channels(config: HubConfig) -> list:
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
                ),
                config.alert_email_min_severity,
            )
        )
    if config.alert_webhook_url:
        channels.append(
            (
                WebhookAlertChannel(url=config.alert_webhook_url, credential_ref=config.alert_webhook_credential_ref),
                config.alert_webhook_min_severity,
            )
        )
    return channels


def notify_alerts(conn, alerts: list, channels: list, *, cooldown_seconds: int) -> None:
    for alert in alerts:
        if not should_notify(alert, cooldown_seconds=cooldown_seconds):
            continue
        notified = False
        for channel, min_severity in channels:
            if _SEVERITY_RANK.get(alert.severity, 0) >= _SEVERITY_RANK.get(min_severity, 0):
                if channel.send(alert):
                    notified = True
        if notified:
            mark_notified(conn, alert.alert_id)
