"""Metric source adapters.

Each adapter collects one metric from a configured source and returns
a ``MetricResult`` indicating whether the threshold was breached.

Adapters:
  * ``BuiltinMetricSource``      -- wraps ``mrm.drift.get_detector()``.
  * ``FileMetricSource``         -- reads JSON or CSV files.
  * ``MLflowMetricSource``       -- queries MLflow tracking server.
  * ``CloudWatchMetricSource``   -- queries AWS CloudWatch.
  * ``DatabricksMetricSource``   -- queries Databricks Lakehouse Monitoring.

See §6 of the continuous monitoring spec.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Type

from mrm.monitor.config import MetricConfig

logger = logging.getLogger(__name__)


@dataclass
class MetricResult:
    """Result of collecting and evaluating a single metric."""

    name: str
    source: str
    drifted: bool
    value: float
    threshold: float
    detector: Optional[str] = None
    details: Dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.details is None:
            self.details = {}

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "source": self.source,
            "drifted": self.drifted,
            "value": self.value,
            "threshold": self.threshold,
        }
        if self.detector:
            d["detector"] = self.detector
        if self.details:
            d["details"] = self.details
        return d


# ---------------------------------------------------------------------------
# Metric source ABC + registry
# ---------------------------------------------------------------------------

class MetricSource:
    """Base class for metric source adapters."""

    name: str = ""

    def collect(self, config: MetricConfig) -> MetricResult:
        raise NotImplementedError


_SOURCE_REGISTRY: Dict[str, Type[MetricSource]] = {}


def _register_source(cls: Type[MetricSource]) -> Type[MetricSource]:
    _SOURCE_REGISTRY[cls.name] = cls
    return cls


def get_metric_source(name: str) -> MetricSource:
    """Return an instance of the metric source for ``name``."""
    if name not in _SOURCE_REGISTRY:
        raise KeyError(
            f"Unknown metric source '{name}'. "
            f"Available: {sorted(_SOURCE_REGISTRY)}"
        )
    return _SOURCE_REGISTRY[name]()


# ---------------------------------------------------------------------------
# Builtin — wraps mrm.drift detectors
# ---------------------------------------------------------------------------

@_register_source
class BuiltinMetricSource(MetricSource):
    """Run RiskAttest drift detectors in-process.

    Reads reference and current datasets from CSV, runs the configured
    detector, and returns a ``MetricResult``.
    """

    name = "builtin"

    def collect(self, config: MetricConfig) -> MetricResult:
        import numpy as np
        from mrm.drift import get_detector

        detector_name = config.detector
        if not detector_name:
            raise ValueError(
                f"Builtin metric '{config.name}' must specify a 'detector'"
            )

        detector = get_detector(detector_name)

        # Load data
        ref_data = self._load_csv(config.reference_dataset, config.columns)
        cur_data = self._load_csv(config.current_dataset, config.columns)

        # Run detector — for multi-column, run per-column and take worst
        worst_result = None
        for col_idx, col_name in enumerate(config.columns):
            ref_col = ref_data[:, col_idx] if ref_data.ndim > 1 else ref_data
            cur_col = cur_data[:, col_idx] if cur_data.ndim > 1 else cur_data

            drift_result = detector.fit_detect(
                ref_col, cur_col, threshold=config.threshold
            )
            if worst_result is None or drift_result.drifted:
                worst_result = drift_result

        if worst_result is None:
            raise ValueError(f"No columns specified for metric '{config.name}'")

        return MetricResult(
            name=config.name,
            source="builtin",
            drifted=worst_result.drifted,
            value=worst_result.score,
            threshold=config.threshold,
            detector=detector_name,
            details=worst_result.to_dict(),
        )

    @staticmethod
    def _load_csv(path: Optional[str], columns: list) -> Any:
        import numpy as np

        if not path:
            raise ValueError("Dataset path is required for builtin source")
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Dataset not found: {path}")

        # numpy loadtxt with header skipping
        data = np.loadtxt(p, delimiter=",", skiprows=1)
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        return data


# ---------------------------------------------------------------------------
# File — read JSON or CSV metrics
# ---------------------------------------------------------------------------

@_register_source
class FileMetricSource(MetricSource):
    """Read a metric value from a local JSON or CSV file.

    For JSON: reads ``config.metric_key`` from the top-level object.
    For CSV: reads the last row's value for ``config.metric_key`` column.
    """

    name = "file"

    def collect(self, config: MetricConfig) -> MetricResult:
        path = config.path
        if not path:
            raise ValueError(f"File metric '{config.name}' must specify 'path'")

        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Metric file not found: {path}")

        metric_key = config.metric_key
        if not metric_key:
            raise ValueError(
                f"File metric '{config.name}' must specify 'metric_key'"
            )

        if p.suffix == ".csv":
            value = self._read_csv(p, metric_key)
        else:
            value = self._read_json(p, metric_key)

        drifted = self._evaluate_threshold(
            value, config.threshold, config.comparison
        )

        return MetricResult(
            name=config.name,
            source="file",
            drifted=drifted,
            value=value,
            threshold=config.threshold,
        )

    @staticmethod
    def _read_json(path: Path, key: str) -> float:
        with open(path) as f:
            data = json.load(f)
        if key not in data:
            raise KeyError(f"Key '{key}' not found in {path}")
        return float(data[key])

    @staticmethod
    def _read_csv(path: Path, key: str) -> float:
        with open(path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if not rows:
            raise ValueError(f"CSV file {path} is empty")
        if key not in rows[-1]:
            raise KeyError(f"Column '{key}' not found in {path}")
        return float(rows[-1][key])

    @staticmethod
    def _evaluate_threshold(
        value: float, threshold: float, comparison: str
    ) -> bool:
        if comparison == "greater_than":
            return value > threshold
        elif comparison == "less_than":
            return value < threshold
        else:
            raise ValueError(f"Unknown comparison: {comparison}")


# ---------------------------------------------------------------------------
# MLflow — query MLflow tracking server
# ---------------------------------------------------------------------------

@_register_source
class MLflowMetricSource(MetricSource):
    """Query MLflow tracking server for the latest metric value.

    Requires ``mlflow`` package (already an optional dependency).
    """

    name = "mlflow"

    def collect(self, config: MetricConfig) -> MetricResult:
        try:
            import mlflow
        except ImportError as exc:
            raise ImportError(
                "MLflow metric source requires mlflow: pip install mlflow"
            ) from exc

        tracking_uri = config.tracking_uri
        if tracking_uri:
            mlflow.set_tracking_uri(tracking_uri)

        experiment = config.experiment
        metric_key = config.metric_key
        if not experiment or not metric_key:
            raise ValueError(
                f"MLflow metric '{config.name}' requires 'experiment' "
                f"and 'metric_key'"
            )

        client = mlflow.tracking.MlflowClient()
        exp = client.get_experiment_by_name(experiment)
        if exp is None:
            raise ValueError(f"MLflow experiment '{experiment}' not found")

        # Get latest run
        runs = client.search_runs(
            experiment_ids=[exp.experiment_id],
            order_by=["start_time DESC"],
            max_results=1,
        )
        if not runs:
            raise ValueError(
                f"No runs found in experiment '{experiment}'"
            )

        run = runs[0]
        value = run.data.metrics.get(metric_key)
        if value is None:
            raise ValueError(
                f"Metric '{metric_key}' not found in latest run"
            )

        comparison = config.comparison or "greater_than"
        drifted = FileMetricSource._evaluate_threshold(
            value, config.threshold, comparison
        )

        return MetricResult(
            name=config.name,
            source="mlflow",
            drifted=drifted,
            value=value,
            threshold=config.threshold,
            details={"run_id": run.info.run_id},
        )


# ---------------------------------------------------------------------------
# CloudWatch — query AWS CloudWatch metrics
# ---------------------------------------------------------------------------

@_register_source
class CloudWatchMetricSource(MetricSource):
    """Query AWS CloudWatch for a metric statistic.

    Requires ``boto3`` (optional dependency). This is how SageMaker
    Model Monitor results are consumed.
    """

    name = "cloudwatch"

    def collect(self, config: MetricConfig) -> MetricResult:
        try:
            import boto3
        except ImportError as exc:
            raise ImportError(
                "CloudWatch metric source requires boto3: pip install boto3"
            ) from exc

        from datetime import datetime, timedelta, timezone

        namespace = config.namespace
        metric_name = config.metric_name
        if not namespace or not metric_name:
            raise ValueError(
                f"CloudWatch metric '{config.name}' requires 'namespace' "
                f"and 'metric_name'"
            )

        cw = boto3.client("cloudwatch")
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(seconds=config.period)

        dimensions = [
            {"Name": k, "Value": v}
            for k, v in config.dimensions.items()
        ]

        response = cw.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=dimensions,
            StartTime=start_time,
            EndTime=end_time,
            Period=config.period,
            Statistics=["Average"],
        )

        datapoints = response.get("Datapoints", [])
        if not datapoints:
            raise ValueError(
                f"No CloudWatch datapoints for {namespace}/{metric_name}"
            )

        # Take latest datapoint
        latest = max(datapoints, key=lambda dp: dp["Timestamp"])
        value = latest["Average"]

        comparison = config.comparison or "greater_than"
        drifted = FileMetricSource._evaluate_threshold(
            value, config.threshold, comparison
        )

        return MetricResult(
            name=config.name,
            source="cloudwatch",
            drifted=drifted,
            value=value,
            threshold=config.threshold,
            details={"timestamp": str(latest["Timestamp"])},
        )


# ---------------------------------------------------------------------------
# Databricks — query Lakehouse Monitoring drift_metrics tables
# ---------------------------------------------------------------------------

@_register_source
class DatabricksMetricSource(MetricSource):
    """Query Databricks Lakehouse Monitoring drift_metrics tables.

    Uses the SQL Statement Execution API to read from the
    ``{table_name}_drift_metrics`` Delta table that Lakehouse Monitoring
    creates in Unity Catalog.

    Auth follows the same pattern as the existing UC integration:
    ``DATABRICKS_HOST`` + ``DATABRICKS_TOKEN`` env vars, overridable
    via config.
    """

    name = "databricks"

    def collect(self, config: MetricConfig) -> MetricResult:
        host = config.host or os.environ.get("DATABRICKS_HOST")
        token = config.token or os.environ.get("DATABRICKS_TOKEN")

        if not host:
            raise ValueError(
                f"Databricks metric '{config.name}' requires 'host' "
                f"or DATABRICKS_HOST env var"
            )
        if not token:
            raise ValueError(
                f"Databricks metric '{config.name}' requires 'token' "
                f"or DATABRICKS_TOKEN env var"
            )
        if not config.warehouse_id:
            raise ValueError(
                f"Databricks metric '{config.name}' requires 'warehouse_id'"
            )
        if not config.table_name:
            raise ValueError(
                f"Databricks metric '{config.name}' requires 'table_name'"
            )
        if not config.metric_column:
            raise ValueError(
                f"Databricks metric '{config.name}' requires 'metric_column'"
            )

        host = host.rstrip("/")
        drift_table = f"{config.table_name}_drift_metrics"
        sql = self._build_sql(
            drift_table, config.metric_column, config.filter_column
        )

        response = self._execute_sql(host, token, config.warehouse_id, sql)
        value = self._extract_value(response, config.name)

        comparison = config.comparison or "greater_than"
        drifted = FileMetricSource._evaluate_threshold(
            value, config.threshold, comparison
        )

        return MetricResult(
            name=config.name,
            source="databricks",
            drifted=drifted,
            value=value,
            threshold=config.threshold,
            details={
                "table": drift_table,
                "metric_column": config.metric_column,
                "filter_column": config.filter_column,
            },
        )

    @staticmethod
    def _build_sql(
        drift_table: str,
        metric_column: str,
        filter_column: Optional[str],
    ) -> str:
        """Build the SQL statement to fetch the latest drift metric."""
        if filter_column:
            return (
                f"SELECT {metric_column} "
                f"FROM {drift_table} "
                f"WHERE column_name = '{filter_column}' "
                f"ORDER BY window_end DESC LIMIT 1"
            )
        # No filter — take the MAX across all columns in the latest window
        return (
            f"SELECT MAX({metric_column}) "
            f"FROM {drift_table} "
            f"WHERE window_end = (SELECT MAX(window_end) FROM {drift_table})"
        )

    @staticmethod
    def _build_ssl_context() -> Optional[ssl.SSLContext]:
        """Build an SSL context that respects corporate proxy CA bundles.

        Checks ``SSL_CERT_FILE`` and ``REQUESTS_CA_BUNDLE`` env vars
        (same pattern as webhook.py). Returns ``None`` to use system
        defaults when no custom CA is configured.
        """
        ca_file = os.environ.get("SSL_CERT_FILE") or os.environ.get(
            "REQUESTS_CA_BUNDLE"
        )
        if ca_file:
            ctx = ssl.create_default_context(cafile=ca_file)
            return ctx
        return None

    @staticmethod
    def _execute_sql(
        host: str, token: str, warehouse_id: str, sql: str
    ) -> Dict[str, Any]:
        """Execute SQL via Databricks Statement Execution API."""
        url = f"{host}/api/2.0/sql/statements"
        body = json.dumps(
            {
                "warehouse_id": warehouse_id,
                "statement": sql,
                "wait_timeout": "30s",
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        ssl_ctx = DatabricksMetricSource._build_ssl_context()

        try:
            with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if exc.code == 401:
                raise ValueError(
                    f"Databricks authentication failed (401). "
                    f"Check your token. Response: {body_text}"
                ) from exc
            elif exc.code == 404:
                raise ValueError(
                    f"Databricks endpoint not found (404). "
                    f"Check host URL: {host}. Response: {body_text}"
                ) from exc
            else:
                raise ValueError(
                    f"Databricks SQL API error (HTTP {exc.code}): {body_text}"
                ) from exc
        except urllib.error.URLError as exc:
            raise ConnectionError(
                f"Cannot connect to Databricks at {host}: {exc.reason}"
            ) from exc

    @staticmethod
    def _extract_value(response: Dict[str, Any], metric_name: str) -> float:
        """Extract the metric value from the SQL API response.

        Expected response shape::

            {
              "status": {"state": "SUCCEEDED"},
              "manifest": {"schema": {"columns": [...]}},
              "result": {"data_array": [["0.042"]]}
            }
        """
        status = response.get("status", {})
        state = status.get("state", "UNKNOWN")
        if state != "SUCCEEDED":
            error_msg = status.get("error", {}).get("message", "no details")
            raise ValueError(
                f"Databricks SQL query for metric '{metric_name}' "
                f"did not succeed (state={state}): {error_msg}"
            )

        result = response.get("result", {})
        data_array = result.get("data_array", [])
        if not data_array or not data_array[0]:
            raise ValueError(
                f"Databricks SQL query for metric '{metric_name}' "
                f"returned no data. Check that the drift_metrics table "
                f"exists and contains rows."
            )

        raw_value = data_array[0][0]
        if raw_value is None:
            raise ValueError(
                f"Databricks SQL query for metric '{metric_name}' "
                f"returned NULL. The metric column may not exist or "
                f"the filter_column may not match any rows."
            )

        try:
            return float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Cannot convert Databricks metric value to float: "
                f"{raw_value!r}"
            ) from exc
