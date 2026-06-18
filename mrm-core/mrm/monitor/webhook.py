"""Webhook notification sender.

POST monitoring events to configured HTTP endpoints (Slack, PagerDuty,
ServiceNow, Splunk). Uses stdlib ``urllib`` — no requests dependency.

Bank environments route outbound HTTP through SSL-inspecting proxies
(Zscaler, Blue Coat). The sender respects ``HTTPS_PROXY`` /
``HTTP_PROXY`` env vars and ``SSL_CERT_FILE`` / ``REQUESTS_CA_BUNDLE``
for custom CA bundles so that ``CERTIFICATE_VERIFY_FAILED`` errors
do not occur behind corporate proxies.

See §9 of the continuous monitoring spec.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mrm.monitor.config import WebhookConfig
from mrm.monitor.metrics import MetricResult

logger = logging.getLogger(__name__)


def _build_ssl_context() -> Optional[ssl.SSLContext]:
    """Build an ``ssl.SSLContext`` if custom CA env vars are set.

    Respects ``SSL_CERT_FILE`` and ``REQUESTS_CA_BUNDLE`` (the latter
    is the de-facto standard set by corporate proxy tooling).

    Returns ``None`` when neither env var is set, so the caller can
    fall back to stdlib defaults.
    """
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get(
        "REQUESTS_CA_BUNDLE"
    )
    if not cafile:
        return None

    ctx = ssl.create_default_context(cafile=cafile)
    logger.debug("Using custom CA bundle: %s", cafile)
    return ctx


def _build_opener(
    ssl_ctx: Optional[ssl.SSLContext] = None,
) -> Optional[urllib.request.OpenerDirector]:
    """Build an ``OpenerDirector`` with proxy + TLS handlers when needed.

    Returns ``None`` when the environment has no proxy or custom-cert
    configuration, signalling the caller to use the simple
    ``urlopen()`` path.
    """
    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get(
        "https_proxy"
    )
    http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get(
        "http_proxy"
    )

    need_opener = bool(https_proxy or http_proxy or ssl_ctx)
    if not need_opener:
        return None

    handlers: List[urllib.request.BaseHandler] = []

    # Proxy handler
    proxies: Dict[str, str] = {}
    if https_proxy:
        proxies["https"] = https_proxy
    if http_proxy:
        proxies["http"] = http_proxy
    if proxies:
        handlers.append(urllib.request.ProxyHandler(proxies))
        logger.debug("Proxy configured: %s", proxies)

    # HTTPS handler with custom SSL context
    if ssl_ctx:
        handlers.append(urllib.request.HTTPSHandler(context=ssl_ctx))

    return urllib.request.build_opener(*handlers)


def build_webhook_payload(
    event: str,
    model_name: str,
    run_id: str,
    metric_results: List[MetricResult],
    evidence_packet_id: Optional[str] = None,
    compliance_references: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a webhook payload matching the spec (§9).

    Returns
    -------
    dict
        JSON-serialisable payload.
    """
    return {
        "event": event,
        "model_name": model_name,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_id": run_id,
        "metrics": [
            {
                "name": r.name,
                "drifted": r.drifted,
                "value": r.value,
                "threshold": r.threshold,
            }
            for r in metric_results
        ],
        "evidence_packet_id": evidence_packet_id,
        "riskattest_version": "0.1.0",
        "compliance_references": compliance_references or [],
    }


def send_webhook(
    webhook_cfg: WebhookConfig,
    payload: Dict[str, Any],
) -> bool:
    """Send a webhook notification.

    Parameters
    ----------
    webhook_cfg:
        Target webhook configuration (url, events, headers).
    payload:
        JSON payload to POST.

    Returns
    -------
    bool
        True if sent successfully, False if filtered out or failed.
    """
    event = payload.get("event", "")
    if event not in webhook_cfg.events:
        logger.debug(
            "Webhook %s: event '%s' not in %s, skipping",
            webhook_cfg.url,
            event,
            webhook_cfg.events,
        )
        return False

    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        **webhook_cfg.headers,
    }

    req = urllib.request.Request(
        webhook_cfg.url,
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        ssl_ctx = _build_ssl_context()
        opener = _build_opener(ssl_ctx)

        if opener:
            with opener.open(req, timeout=webhook_cfg.timeout) as resp:
                logger.info(
                    "Webhook %s: %s (status %d, via opener)",
                    webhook_cfg.url,
                    event,
                    resp.status,
                )
                return True
        else:
            with urllib.request.urlopen(req, timeout=webhook_cfg.timeout) as resp:
                logger.info(
                    "Webhook %s: %s (status %d)",
                    webhook_cfg.url,
                    event,
                    resp.status,
                )
                return True
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        logger.warning("Webhook %s failed: %s", webhook_cfg.url, exc)
        return False
