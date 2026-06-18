"""Monitoring configuration parsing.

Parses the ``monitoring:`` block from model YAML into typed
dataclasses. Follows the schema in §4 of the continuous monitoring
spec.

Environment variable expansion
------------------------------
String values support ``${VAR_NAME}`` syntax (matching the spec
examples and standard shell convention).  This supplements the
``{{ env_var('VAR') }}`` syntax that ``load_yaml`` already handles
— both work.  Banks never hardcode secrets in YAML; this makes
``${MLFLOW_TRACKING_URI}``, ``${WEBHOOK_TOKEN}`` etc. work out of
the box.  ``${VAR:-default}`` is also supported.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Pattern for ${VAR_NAME} with optional default: ${VAR:-default}
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def _expand_env(value: Any) -> Any:
    """Recursively expand ``${VAR}`` references in strings.

    Supports ``${VAR}`` and ``${VAR:-default}``.  Non-string values
    (int, float, bool, list, dict) are returned unchanged.
    """
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            var_name = m.group(1)
            default = m.group(2) if m.group(2) is not None else ""
            return os.environ.get(var_name, default)
        return _ENV_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


@dataclass
class MetricConfig:
    """Configuration for a single monitored metric."""

    name: str
    source: str  # "builtin" | "mlflow" | "cloudwatch" | "file" | "databricks"
    threshold: float = 0.05

    # --- builtin source ---
    detector: Optional[str] = None
    reference_dataset: Optional[str] = None
    current_dataset: Optional[str] = None
    columns: List[str] = field(default_factory=list)

    # --- mlflow source ---
    tracking_uri: Optional[str] = None
    experiment: Optional[str] = None
    metric_key: Optional[str] = None

    # --- cloudwatch source ---
    namespace: Optional[str] = None
    metric_name: Optional[str] = None
    dimensions: Dict[str, str] = field(default_factory=dict)
    period: int = 86400

    # --- file source ---
    path: Optional[str] = None

    # --- databricks source ---
    host: Optional[str] = None            # Databricks workspace URL
    token: Optional[str] = None           # PAT token (prefer env var)
    warehouse_id: Optional[str] = None    # SQL Warehouse ID
    table_name: Optional[str] = None      # Fully qualified table name
    metric_column: Optional[str] = None   # Column from drift_metrics (ks_statistic, js_divergence, etc.)
    filter_column: Optional[str] = None   # Optional: only check drift for this column

    # --- comparison ---
    comparison: str = "less_than"  # "greater_than" | "less_than"


@dataclass
class OnDriftConfig:
    """What to do when drift is detected."""

    revalidate: bool = True
    freeze_evidence: bool = True
    resolve_triggers: bool = False


@dataclass
class WebhookConfig:
    """Webhook notification target."""

    url: str
    events: List[str] = field(default_factory=lambda: ["drift_detected"])
    headers: Dict[str, str] = field(default_factory=dict)
    timeout: int = 30  # seconds; configurable for bank environments


@dataclass
class MonitoringConfig:
    """Parsed monitoring configuration for a model."""

    enabled: bool = False
    schedule: str = "daily"  # informational only
    metrics: List[MetricConfig] = field(default_factory=list)
    on_drift: OnDriftConfig = field(default_factory=OnDriftConfig)
    webhooks: List[WebhookConfig] = field(default_factory=list)


def parse_monitoring_config(raw: Dict[str, Any]) -> MonitoringConfig:
    """Parse a raw YAML dict into a ``MonitoringConfig``.

    Parameters
    ----------
    raw:
        The ``monitoring:`` block from model YAML.

    Returns
    -------
    MonitoringConfig
        Validated configuration object.
    """
    # Expand ${VAR} references before parsing.
    raw = _expand_env(raw)

    enabled = raw.get("enabled", False)
    if not enabled:
        return MonitoringConfig(enabled=False)

    schedule = raw.get("schedule", "daily")

    # Parse metrics
    metrics: List[MetricConfig] = []
    for m in raw.get("metrics", []):
        metrics.append(
            MetricConfig(
                name=m["name"],
                source=m["source"],
                threshold=m.get("threshold", 0.05),
                detector=m.get("detector"),
                reference_dataset=m.get("reference_dataset"),
                current_dataset=m.get("current_dataset"),
                columns=m.get("columns", []),
                tracking_uri=m.get("tracking_uri"),
                experiment=m.get("experiment"),
                metric_key=m.get("metric_key"),
                namespace=m.get("namespace"),
                metric_name=m.get("metric_name"),
                dimensions=m.get("dimensions", {}),
                period=m.get("period", 86400),
                path=m.get("path"),
                host=m.get("host"),
                token=m.get("token"),
                warehouse_id=m.get("warehouse_id"),
                table_name=m.get("table_name"),
                metric_column=m.get("metric_column"),
                filter_column=m.get("filter_column"),
                comparison=m.get("comparison", "less_than"),
            )
        )

    # Parse on_drift
    on_drift_raw = raw.get("on_drift", {})
    on_drift = OnDriftConfig(
        revalidate=on_drift_raw.get("revalidate", True),
        freeze_evidence=on_drift_raw.get("freeze_evidence", True),
        resolve_triggers=on_drift_raw.get("resolve_triggers", False),
    )

    # Parse webhooks
    webhooks: List[WebhookConfig] = []
    for w in raw.get("webhooks", []):
        webhooks.append(
            WebhookConfig(
                url=w["url"],
                events=w.get("events", ["drift_detected"]),
                headers=w.get("headers", {}),
                timeout=w.get("timeout", 30),
            )
        )

    return MonitoringConfig(
        enabled=True,
        schedule=schedule,
        metrics=metrics,
        on_drift=on_drift,
        webhooks=webhooks,
    )
