"""Tests for the continuous monitoring module.

Covers:
  - MonitoringConfig YAML parsing
  - Metric source adapters (builtin, file, mlflow, cloudwatch)
  - MonitoringLog append-only semantics
  - Monitor run pipeline (drift → revalidate → freeze evidence → webhook)
  - Webhook payload format
  - Exit codes (0 = no drift, 1 = drift, 2 = error)
  - CLI integration

Follows spec: docs/spec/continuous-monitoring-v1.md
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from mrm.monitor.config import (
    MetricConfig,
    MonitoringConfig,
    OnDriftConfig,
    WebhookConfig,
    parse_monitoring_config,
)
from mrm.monitor.log import MonitoringLog, MonitoringLogEntry
from mrm.monitor.metrics import (
    BuiltinMetricSource,
    DatabricksMetricSource,
    FileMetricSource,
    MetricResult,
    get_metric_source,
)
from mrm.monitor.runner import MonitorRunner, MonitorRunResult


# ---------------------------------------------------------------------------
# §1  MonitoringConfig parsing
# ---------------------------------------------------------------------------


class TestMonitoringConfig:
    """Parse the ``monitoring:`` block from model YAML."""

    def test_parse_minimal_config(self):
        raw = {
            "enabled": True,
            "metrics": [
                {
                    "name": "data_drift",
                    "source": "builtin",
                    "detector": "ks",
                    "reference_dataset": "data/ref.csv",
                    "current_dataset": "data/cur.csv",
                    "columns": ["exposure"],
                    "threshold": 0.05,
                }
            ],
        }
        cfg = parse_monitoring_config(raw)
        assert cfg.enabled is True
        assert len(cfg.metrics) == 1
        assert cfg.metrics[0].name == "data_drift"
        assert cfg.metrics[0].source == "builtin"
        assert cfg.metrics[0].threshold == 0.05

    def test_parse_full_config(self):
        raw = {
            "enabled": True,
            "schedule": "daily",
            "metrics": [
                {
                    "name": "data_drift",
                    "source": "builtin",
                    "detector": "ks",
                    "reference_dataset": "data/ref.csv",
                    "current_dataset": "data/cur.csv",
                    "columns": ["exposure", "notional"],
                    "threshold": 0.05,
                },
                {
                    "name": "prediction_quality",
                    "source": "mlflow",
                    "tracking_uri": "http://localhost:5000",
                    "experiment": "ccr_monte_carlo",
                    "metric_key": "rmse",
                    "threshold": 0.15,
                    "comparison": "greater_than",
                },
                {
                    "name": "feature_drift_psi",
                    "source": "cloudwatch",
                    "namespace": "SageMaker/Endpoints",
                    "metric_name": "FeatureDriftPSI",
                    "dimensions": {"EndpointName": "ccr-prod"},
                    "threshold": 0.25,
                    "comparison": "greater_than",
                    "period": 86400,
                },
                {
                    "name": "custom_metric",
                    "source": "file",
                    "path": "/data/monitoring/latest.json",
                    "metric_key": "gini_coefficient",
                    "threshold": 0.60,
                    "comparison": "less_than",
                },
            ],
            "on_drift": {
                "revalidate": True,
                "freeze_evidence": True,
                "resolve_triggers": True,
            },
            "webhooks": [
                {
                    "url": "https://hooks.slack.com/test",
                    "events": ["drift_detected"],
                }
            ],
        }
        cfg = parse_monitoring_config(raw)
        assert cfg.enabled is True
        assert cfg.schedule == "daily"
        assert len(cfg.metrics) == 4
        assert cfg.on_drift.revalidate is True
        assert cfg.on_drift.freeze_evidence is True
        assert cfg.on_drift.resolve_triggers is True
        assert len(cfg.webhooks) == 1
        assert cfg.webhooks[0].url == "https://hooks.slack.com/test"
        assert "drift_detected" in cfg.webhooks[0].events

    def test_parse_disabled_config(self):
        raw = {"enabled": False}
        cfg = parse_monitoring_config(raw)
        assert cfg.enabled is False
        assert cfg.metrics == []

    def test_parse_defaults(self):
        """Missing on_drift and webhooks get sensible defaults."""
        raw = {
            "enabled": True,
            "metrics": [
                {
                    "name": "m1",
                    "source": "file",
                    "path": "/tmp/m.json",
                    "metric_key": "acc",
                    "threshold": 0.9,
                    "comparison": "less_than",
                }
            ],
        }
        cfg = parse_monitoring_config(raw)
        assert cfg.on_drift.revalidate is True
        assert cfg.on_drift.freeze_evidence is True
        assert cfg.webhooks == []

    def test_comparison_enum_values(self):
        raw = {
            "enabled": True,
            "metrics": [
                {
                    "name": "m1",
                    "source": "file",
                    "path": "/tmp/m.json",
                    "metric_key": "val",
                    "threshold": 0.5,
                    "comparison": "greater_than",
                },
                {
                    "name": "m2",
                    "source": "file",
                    "path": "/tmp/m.json",
                    "metric_key": "val2",
                    "threshold": 0.3,
                    "comparison": "less_than",
                },
            ],
        }
        cfg = parse_monitoring_config(raw)
        assert cfg.metrics[0].comparison == "greater_than"
        assert cfg.metrics[1].comparison == "less_than"

    def test_parse_databricks_metric(self):
        """Databricks metric fields are parsed correctly from YAML."""
        raw = {
            "enabled": True,
            "metrics": [
                {
                    "name": "databricks_drift",
                    "source": "databricks",
                    "host": "https://my-workspace.cloud.databricks.com",
                    "token": "dapi_test_token",
                    "warehouse_id": "abc123def456",
                    "table_name": "risk_models.ccr.portfolio_data",
                    "metric_column": "ks_statistic",
                    "filter_column": "notional",
                    "threshold": 0.05,
                    "comparison": "greater_than",
                }
            ],
        }
        cfg = parse_monitoring_config(raw)
        assert len(cfg.metrics) == 1
        m = cfg.metrics[0]
        assert m.source == "databricks"
        assert m.host == "https://my-workspace.cloud.databricks.com"
        assert m.token == "dapi_test_token"
        assert m.warehouse_id == "abc123def456"
        assert m.table_name == "risk_models.ccr.portfolio_data"
        assert m.metric_column == "ks_statistic"
        assert m.filter_column == "notional"
        assert m.threshold == 0.05
        assert m.comparison == "greater_than"


# ---------------------------------------------------------------------------
# §2  MetricResult
# ---------------------------------------------------------------------------


class TestMetricResult:
    def test_metric_result_no_drift(self):
        r = MetricResult(
            name="data_drift",
            source="builtin",
            drifted=False,
            value=0.032,
            threshold=0.05,
        )
        d = r.to_dict()
        assert d["drifted"] is False
        assert d["value"] == 0.032
        assert d["threshold"] == 0.05

    def test_metric_result_drift_detected(self):
        r = MetricResult(
            name="prediction_quality",
            source="mlflow",
            drifted=True,
            value=0.18,
            threshold=0.15,
        )
        assert r.drifted is True


# ---------------------------------------------------------------------------
# §3  Builtin metric source (wraps mrm.drift detectors)
# ---------------------------------------------------------------------------


class TestBuiltinMetricSource:
    def test_ks_no_drift(self, tmp_path):
        """KS detector reports no drift for same distribution."""
        rng = np.random.default_rng(seed=42)
        ref = rng.normal(0, 1, size=500)
        cur = rng.normal(0, 1, size=500)

        ref_path = tmp_path / "ref.csv"
        cur_path = tmp_path / "cur.csv"
        np.savetxt(ref_path, ref, header="exposure", comments="")
        np.savetxt(cur_path, cur, header="exposure", comments="")

        metric_cfg = MetricConfig(
            name="data_drift",
            source="builtin",
            detector="ks",
            reference_dataset=str(ref_path),
            current_dataset=str(cur_path),
            columns=["exposure"],
            threshold=0.05,
        )
        source = BuiltinMetricSource()
        result = source.collect(metric_cfg)
        assert result.drifted is False
        assert result.source == "builtin"

    def test_ks_detects_drift(self, tmp_path):
        """KS detector catches a shifted distribution."""
        rng = np.random.default_rng(seed=42)
        ref = rng.normal(0, 1, size=500)
        cur = rng.normal(3, 1, size=500)

        ref_path = tmp_path / "ref.csv"
        cur_path = tmp_path / "cur.csv"
        np.savetxt(ref_path, ref, header="exposure", comments="")
        np.savetxt(cur_path, cur, header="exposure", comments="")

        metric_cfg = MetricConfig(
            name="data_drift",
            source="builtin",
            detector="ks",
            reference_dataset=str(ref_path),
            current_dataset=str(cur_path),
            columns=["exposure"],
            threshold=0.05,
        )
        source = BuiltinMetricSource()
        result = source.collect(metric_cfg)
        assert result.drifted is True

    def test_page_hinkley_no_drift(self, tmp_path):
        """Page-Hinkley reports no drift for stationary stream."""
        rng = np.random.default_rng(seed=42)
        ref = rng.normal(0, 0.1, size=200)
        cur = rng.normal(0, 0.1, size=200)

        ref_path = tmp_path / "ref.csv"
        cur_path = tmp_path / "cur.csv"
        np.savetxt(ref_path, ref, header="residual", comments="")
        np.savetxt(cur_path, cur, header="residual", comments="")

        metric_cfg = MetricConfig(
            name="model_drift",
            source="builtin",
            detector="page_hinkley",
            reference_dataset=str(ref_path),
            current_dataset=str(cur_path),
            columns=["residual"],
            threshold=50.0,
        )
        source = BuiltinMetricSource()
        result = source.collect(metric_cfg)
        assert result.drifted is False

    def test_unknown_detector_raises(self):
        metric_cfg = MetricConfig(
            name="bad",
            source="builtin",
            detector="nonexistent_detector",
            reference_dataset="/dev/null",
            current_dataset="/dev/null",
            columns=["x"],
            threshold=0.05,
        )
        source = BuiltinMetricSource()
        with pytest.raises(KeyError):
            source.collect(metric_cfg)


# ---------------------------------------------------------------------------
# §4  File metric source
# ---------------------------------------------------------------------------


class TestFileMetricSource:
    def test_json_metric_no_drift(self, tmp_path):
        """Read a metric from JSON, below threshold → no drift."""
        metrics_file = tmp_path / "metrics.json"
        metrics_file.write_text(json.dumps({"gini_coefficient": 0.72}))

        metric_cfg = MetricConfig(
            name="custom_metric",
            source="file",
            path=str(metrics_file),
            metric_key="gini_coefficient",
            threshold=0.60,
            comparison="less_than",
        )
        source = FileMetricSource()
        result = source.collect(metric_cfg)
        assert result.drifted is False
        assert result.value == 0.72

    def test_json_metric_drift_detected(self, tmp_path):
        """Read a metric from JSON, below threshold → drift."""
        metrics_file = tmp_path / "metrics.json"
        metrics_file.write_text(json.dumps({"gini_coefficient": 0.45}))

        metric_cfg = MetricConfig(
            name="custom_metric",
            source="file",
            path=str(metrics_file),
            metric_key="gini_coefficient",
            threshold=0.60,
            comparison="less_than",
        )
        source = FileMetricSource()
        result = source.collect(metric_cfg)
        assert result.drifted is True
        assert result.value == 0.45

    def test_greater_than_comparison(self, tmp_path):
        metrics_file = tmp_path / "metrics.json"
        metrics_file.write_text(json.dumps({"rmse": 0.20}))

        metric_cfg = MetricConfig(
            name="quality",
            source="file",
            path=str(metrics_file),
            metric_key="rmse",
            threshold=0.15,
            comparison="greater_than",
        )
        source = FileMetricSource()
        result = source.collect(metric_cfg)
        assert result.drifted is True
        assert result.value == 0.20

    def test_missing_file_raises(self):
        metric_cfg = MetricConfig(
            name="missing",
            source="file",
            path="/nonexistent/path.json",
            metric_key="x",
            threshold=0.5,
            comparison="greater_than",
        )
        source = FileMetricSource()
        with pytest.raises(FileNotFoundError):
            source.collect(metric_cfg)

    def test_csv_metric(self, tmp_path):
        """Read latest metric value from a CSV file."""
        csv_file = tmp_path / "metrics.csv"
        csv_file.write_text("date,psi\n2026-06-17,0.12\n2026-06-18,0.30\n")

        metric_cfg = MetricConfig(
            name="feature_psi",
            source="file",
            path=str(csv_file),
            metric_key="psi",
            threshold=0.25,
            comparison="greater_than",
        )
        source = FileMetricSource()
        result = source.collect(metric_cfg)
        assert result.drifted is True
        assert result.value == 0.30


# ---------------------------------------------------------------------------
# §4b  Databricks Lakehouse Monitoring metric source
# ---------------------------------------------------------------------------


class TestDatabricksMetricSource:
    """Tests for the Databricks Lakehouse Monitoring metric source adapter."""

    def _make_config(self, **overrides):
        """Build a MetricConfig with sensible Databricks defaults."""
        defaults = dict(
            name="databricks_drift",
            source="databricks",
            host="https://my-workspace.cloud.databricks.com",
            token="dapi_test_token",
            warehouse_id="abc123def456",
            table_name="risk_models.ccr.portfolio_data",
            metric_column="ks_statistic",
            filter_column="notional",
            threshold=0.05,
            comparison="greater_than",
        )
        defaults.update(overrides)
        return MetricConfig(**defaults)

    @staticmethod
    def _make_sql_response(value="0.042", state="SUCCEEDED"):
        """Build a fake Databricks SQL Statement Execution API response."""
        resp = {
            "status": {"state": state},
            "manifest": {
                "schema": {
                    "columns": [{"name": "ks_statistic", "type_name": "DOUBLE"}]
                }
            },
            "result": {"data_array": [[value]]},
        }
        if state != "SUCCEEDED":
            resp["status"]["error"] = {"message": "Table not found"}
            resp["result"] = {"data_array": []}
        return resp

    def test_databricks_source_registered(self):
        """Databricks source is available in the registry."""
        source = get_metric_source("databricks")
        assert isinstance(source, DatabricksMetricSource)

    def test_databricks_missing_warehouse_id_raises(self):
        """Missing warehouse_id raises ValueError."""
        config = self._make_config(warehouse_id=None)
        source = DatabricksMetricSource()
        with pytest.raises(ValueError, match="warehouse_id"):
            source.collect(config)

    def test_databricks_missing_table_name_raises(self):
        """Missing table_name raises ValueError."""
        config = self._make_config(table_name=None)
        source = DatabricksMetricSource()
        with pytest.raises(ValueError, match="table_name"):
            source.collect(config)

    def test_databricks_missing_metric_column_raises(self):
        """Missing metric_column raises ValueError."""
        config = self._make_config(metric_column=None)
        source = DatabricksMetricSource()
        with pytest.raises(ValueError, match="metric_column"):
            source.collect(config)

    def test_databricks_missing_host_raises(self):
        """Missing host (and no env var) raises ValueError."""
        config = self._make_config(host=None)
        source = DatabricksMetricSource()
        with patch.dict(os.environ, {}, clear=True):
            # Clear any DATABRICKS_HOST that might be set
            env = os.environ.copy()
            env.pop("DATABRICKS_HOST", None)
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(ValueError, match="host"):
                    source.collect(config)

    def test_databricks_missing_token_raises(self):
        """Missing token (and no env var) raises ValueError."""
        config = self._make_config(token=None)
        source = DatabricksMetricSource()
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("DATABRICKS_TOKEN", None)
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(ValueError, match="token"):
                    source.collect(config)

    def test_databricks_query_success_no_drift(self):
        """Mock the REST API: value below threshold means no drift."""
        config = self._make_config(threshold=0.05, comparison="greater_than")
        source = DatabricksMetricSource()
        fake_response = self._make_sql_response(value="0.032")

        with patch.object(
            DatabricksMetricSource, "_execute_sql", return_value=fake_response
        ):
            result = source.collect(config)

        assert result.drifted is False
        assert result.value == 0.032
        assert result.source == "databricks"
        assert result.threshold == 0.05
        assert result.details["table"] == "risk_models.ccr.portfolio_data_drift_metrics"
        assert result.details["metric_column"] == "ks_statistic"
        assert result.details["filter_column"] == "notional"

    def test_databricks_query_success_drift_detected(self):
        """Mock the REST API: value above threshold means drift."""
        config = self._make_config(threshold=0.05, comparison="greater_than")
        source = DatabricksMetricSource()
        fake_response = self._make_sql_response(value="0.18")

        with patch.object(
            DatabricksMetricSource, "_execute_sql", return_value=fake_response
        ):
            result = source.collect(config)

        assert result.drifted is True
        assert result.value == 0.18

    def test_databricks_query_failed_state_raises(self):
        """SQL query in FAILED state raises ValueError."""
        config = self._make_config()
        source = DatabricksMetricSource()
        fake_response = self._make_sql_response(state="FAILED")

        with patch.object(
            DatabricksMetricSource, "_execute_sql", return_value=fake_response
        ):
            with pytest.raises(ValueError, match="did not succeed"):
                source.collect(config)

    def test_databricks_query_empty_result_raises(self):
        """Empty data_array raises ValueError."""
        config = self._make_config()
        source = DatabricksMetricSource()
        fake_response = {
            "status": {"state": "SUCCEEDED"},
            "manifest": {"schema": {"columns": []}},
            "result": {"data_array": []},
        }

        with patch.object(
            DatabricksMetricSource, "_execute_sql", return_value=fake_response
        ):
            with pytest.raises(ValueError, match="returned no data"):
                source.collect(config)

    def test_databricks_query_null_value_raises(self):
        """NULL metric value raises ValueError."""
        config = self._make_config()
        source = DatabricksMetricSource()
        fake_response = {
            "status": {"state": "SUCCEEDED"},
            "manifest": {"schema": {"columns": []}},
            "result": {"data_array": [[None]]},
        }

        with patch.object(
            DatabricksMetricSource, "_execute_sql", return_value=fake_response
        ):
            with pytest.raises(ValueError, match="returned NULL"):
                source.collect(config)

    def test_databricks_sql_with_filter_column(self):
        """SQL includes WHERE column_name clause when filter_column is set."""
        sql = DatabricksMetricSource._build_sql(
            "catalog.schema.tbl_drift_metrics", "ks_statistic", "notional"
        )
        assert "WHERE column_name = 'notional'" in sql
        assert "ORDER BY window_end DESC LIMIT 1" in sql
        assert "ks_statistic" in sql

    def test_databricks_sql_without_filter_column(self):
        """SQL uses MAX() across all columns when no filter_column."""
        sql = DatabricksMetricSource._build_sql(
            "catalog.schema.tbl_drift_metrics", "js_divergence", None
        )
        assert "MAX(js_divergence)" in sql
        assert "column_name" not in sql

    def test_databricks_host_from_env_var(self):
        """Host falls back to DATABRICKS_HOST env var."""
        config = self._make_config(host=None)
        source = DatabricksMetricSource()
        fake_response = self._make_sql_response(value="0.01")

        with patch.dict(
            os.environ,
            {"DATABRICKS_HOST": "https://env-workspace.cloud.databricks.com"},
        ):
            with patch.object(
                DatabricksMetricSource, "_execute_sql", return_value=fake_response
            ) as mock_exec:
                result = source.collect(config)
                # Verify the host from env var was used
                call_args = mock_exec.call_args
                assert "env-workspace" in call_args[0][0]

        assert result.value == 0.01

    def test_databricks_token_from_env_var(self):
        """Token falls back to DATABRICKS_TOKEN env var."""
        config = self._make_config(token=None)
        source = DatabricksMetricSource()
        fake_response = self._make_sql_response(value="0.02")

        with patch.dict(
            os.environ, {"DATABRICKS_TOKEN": "dapi_env_token"}
        ):
            with patch.object(
                DatabricksMetricSource, "_execute_sql", return_value=fake_response
            ) as mock_exec:
                result = source.collect(config)
                call_args = mock_exec.call_args
                assert call_args[0][1] == "dapi_env_token"

        assert result.value == 0.02

    def test_databricks_execute_sql_http_401(self):
        """HTTP 401 from Databricks raises ValueError with auth message."""
        import io
        import urllib.error

        mock_exc = urllib.error.HTTPError(
            url="https://host/api/2.0/sql/statements",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b'{"error": "invalid token"}'),
        )

        with patch(
            "mrm.monitor.metrics.urllib.request.urlopen", side_effect=mock_exc
        ):
            with pytest.raises(ValueError, match="authentication failed"):
                DatabricksMetricSource._execute_sql(
                    "https://host", "bad_token", "wh123", "SELECT 1"
                )

    def test_databricks_execute_sql_connection_error(self):
        """Connection refused raises ConnectionError."""
        import urllib.error

        mock_exc = urllib.error.URLError("Connection refused")

        with patch(
            "mrm.monitor.metrics.urllib.request.urlopen", side_effect=mock_exc
        ):
            with pytest.raises(ConnectionError, match="Cannot connect"):
                DatabricksMetricSource._execute_sql(
                    "https://unreachable", "tok", "wh123", "SELECT 1"
                )

    def test_databricks_ssl_context_with_custom_ca(self):
        """SSL context picks up SSL_CERT_FILE env var."""
        with patch.dict(
            os.environ, {"SSL_CERT_FILE": "/path/to/custom-ca.pem"}
        ):
            with patch("mrm.monitor.metrics.ssl.create_default_context") as mock_ctx:
                DatabricksMetricSource._build_ssl_context()
                mock_ctx.assert_called_once_with(cafile="/path/to/custom-ca.pem")

    def test_databricks_ssl_context_default(self):
        """No custom CA env var returns None (use system defaults)."""
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("SSL_CERT_FILE", None)
            env.pop("REQUESTS_CA_BUNDLE", None)
            with patch.dict(os.environ, env, clear=True):
                ctx = DatabricksMetricSource._build_ssl_context()
                assert ctx is None

    def test_databricks_execute_sql_builds_correct_request(self):
        """Verify the HTTP request shape sent to the SQL API."""
        fake_json = json.dumps(
            self._make_sql_response(value="0.05")
        ).encode("utf-8")

        with patch("mrm.monitor.metrics.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = fake_json
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            DatabricksMetricSource._execute_sql(
                "https://my-host", "dapi_tok", "wh_id", "SELECT x FROM t"
            )

            # Inspect the Request object passed to urlopen
            call_args = mock_urlopen.call_args
            req = call_args[0][0]
            assert req.full_url == "https://my-host/api/2.0/sql/statements"
            assert req.get_header("Authorization") == "Bearer dapi_tok"
            assert req.get_header("Content-type") == "application/json"

            body = json.loads(req.data.decode("utf-8"))
            assert body["warehouse_id"] == "wh_id"
            assert body["statement"] == "SELECT x FROM t"
            assert body["wait_timeout"] == "30s"


# ---------------------------------------------------------------------------
# §5  get_metric_source factory
# ---------------------------------------------------------------------------


class TestMetricSourceFactory:
    def test_builtin_source(self):
        source = get_metric_source("builtin")
        assert isinstance(source, BuiltinMetricSource)

    def test_file_source(self):
        source = get_metric_source("file")
        assert isinstance(source, FileMetricSource)

    def test_databricks_source(self):
        source = get_metric_source("databricks")
        assert isinstance(source, DatabricksMetricSource)

    def test_unknown_source_raises(self):
        with pytest.raises(KeyError):
            get_metric_source("nonexistent_source")


# ---------------------------------------------------------------------------
# §6  MonitoringLog (append-only JSONL)
# ---------------------------------------------------------------------------


class TestMonitoringLog:
    def test_append_and_read(self, tmp_path):
        log = MonitoringLog(tmp_path / "test_model")
        entry = MonitoringLogEntry(
            run_id="run-001",
            model_name="test_model",
            timestamp=datetime.now(timezone.utc).isoformat(),
            metrics=[
                {
                    "name": "data_drift",
                    "source": "builtin",
                    "drifted": False,
                    "value": 0.032,
                    "threshold": 0.05,
                }
            ],
            overall_drifted=False,
            action_taken="none",
            exit_code=0,
        )
        log.append(entry)
        entries = log.read_all()
        assert len(entries) == 1
        assert entries[0]["run_id"] == "run-001"
        assert entries[0]["overall_drifted"] is False

    def test_append_only_semantics(self, tmp_path):
        """Multiple appends accumulate — never overwrite."""
        log = MonitoringLog(tmp_path / "test_model")
        for i in range(3):
            entry = MonitoringLogEntry(
                run_id=f"run-{i:03d}",
                model_name="test_model",
                timestamp=datetime.now(timezone.utc).isoformat(),
                metrics=[],
                overall_drifted=False,
                action_taken="none",
                exit_code=0,
            )
            log.append(entry)
        entries = log.read_all()
        assert len(entries) == 3
        assert [e["run_id"] for e in entries] == [
            "run-000",
            "run-001",
            "run-002",
        ]

    def test_read_empty_log(self, tmp_path):
        log = MonitoringLog(tmp_path / "test_model")
        entries = log.read_all()
        assert entries == []

    def test_read_last_n(self, tmp_path):
        log = MonitoringLog(tmp_path / "test_model")
        for i in range(10):
            entry = MonitoringLogEntry(
                run_id=f"run-{i:03d}",
                model_name="test_model",
                timestamp=datetime.now(timezone.utc).isoformat(),
                metrics=[],
                overall_drifted=False,
                action_taken="none",
                exit_code=0,
            )
            log.append(entry)
        last_3 = log.read_last(3)
        assert len(last_3) == 3
        assert last_3[0]["run_id"] == "run-007"

    def test_log_entry_serialises_to_valid_json(self, tmp_path):
        log = MonitoringLog(tmp_path / "test_model")
        entry = MonitoringLogEntry(
            run_id="run-json",
            model_name="test_model",
            timestamp="2026-06-18T00:05:00Z",
            metrics=[
                {"name": "drift", "drifted": True, "value": 0.18, "threshold": 0.15}
            ],
            overall_drifted=True,
            action_taken="revalidated",
            evidence_packet_id="pkt-001",
            triggers_fired=["DRIFT-test-20260618"],
            webhooks_sent=["https://hooks.slack.com/test"],
            exit_code=1,
        )
        log.append(entry)
        # Re-read raw JSONL and parse each line
        raw = (tmp_path / "test_model" / "log.jsonl").read_text()
        for line in raw.strip().split("\n"):
            parsed = json.loads(line)
            assert "run_id" in parsed


# ---------------------------------------------------------------------------
# §7  MonitorRunner — the orchestrator
# ---------------------------------------------------------------------------


class TestMonitorRunner:
    @pytest.fixture
    def model_config(self, tmp_path):
        """Minimal model YAML config with monitoring section."""
        rng = np.random.default_rng(seed=42)
        ref = rng.normal(0, 1, size=500)
        cur = rng.normal(0, 1, size=500)

        ref_path = tmp_path / "ref.csv"
        cur_path = tmp_path / "cur.csv"
        np.savetxt(ref_path, ref, header="exposure", comments="")
        np.savetxt(cur_path, cur, header="exposure", comments="")

        return {
            "model": {
                "name": "test_model",
                "version": "1.0",
                "risk_tier": "tier_1",
            },
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "data_drift",
                        "source": "builtin",
                        "detector": "ks",
                        "reference_dataset": str(ref_path),
                        "current_dataset": str(cur_path),
                        "columns": ["exposure"],
                        "threshold": 0.05,
                    }
                ],
                "on_drift": {
                    "revalidate": True,
                    "freeze_evidence": True,
                },
            },
        }

    @pytest.fixture
    def drift_model_config(self, tmp_path):
        """Model config where drift WILL be detected."""
        rng = np.random.default_rng(seed=42)
        ref = rng.normal(0, 1, size=500)
        cur = rng.normal(5, 1, size=500)  # massive shift

        ref_path = tmp_path / "ref.csv"
        cur_path = tmp_path / "cur.csv"
        np.savetxt(ref_path, ref, header="exposure", comments="")
        np.savetxt(cur_path, cur, header="exposure", comments="")

        return {
            "model": {
                "name": "drifting_model",
                "version": "1.0",
                "risk_tier": "tier_1",
            },
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "data_drift",
                        "source": "builtin",
                        "detector": "ks",
                        "reference_dataset": str(ref_path),
                        "current_dataset": str(cur_path),
                        "columns": ["exposure"],
                        "threshold": 0.05,
                    }
                ],
                "on_drift": {
                    "revalidate": False,
                    "freeze_evidence": False,
                },
                "webhooks": [],
            },
        }

    def test_run_no_drift_returns_exit_0(self, model_config, tmp_path):
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(model_config)
        assert result.exit_code == 0
        assert result.overall_drifted is False
        assert len(result.metric_results) == 1
        assert result.metric_results[0].drifted is False

    def test_run_drift_returns_exit_1(self, drift_model_config, tmp_path):
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(drift_model_config)
        assert result.exit_code == 1
        assert result.overall_drifted is True
        assert result.metric_results[0].drifted is True

    def test_run_writes_log_entry(self, model_config, tmp_path):
        runner = MonitorRunner(log_dir=tmp_path)
        runner.run(model_config)
        log = MonitoringLog(tmp_path / "test_model")
        entries = log.read_all()
        assert len(entries) == 1
        assert entries[0]["model_name"] == "test_model"

    def test_run_disabled_monitoring_skips(self, tmp_path):
        cfg = {
            "model": {"name": "disabled_model", "version": "1.0"},
            "monitoring": {"enabled": False},
        }
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(cfg)
        assert result.exit_code == 0
        assert result.overall_drifted is False
        assert result.skipped is True

    def test_run_missing_monitoring_section_skips(self, tmp_path):
        cfg = {"model": {"name": "no_monitoring", "version": "1.0"}}
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(cfg)
        assert result.skipped is True

    def test_run_error_returns_exit_2(self, tmp_path):
        """Metric source error → exit code 2, not a crash."""
        cfg = {
            "model": {"name": "error_model", "version": "1.0"},
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "broken",
                        "source": "file",
                        "path": "/nonexistent/file.json",
                        "metric_key": "x",
                        "threshold": 0.5,
                        "comparison": "greater_than",
                    }
                ],
            },
        }
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(cfg)
        assert result.exit_code == 2
        assert result.error is not None

    def test_multiple_metrics_any_drift_triggers(self, tmp_path):
        """If ANY metric drifts, overall_drifted is True."""
        rng = np.random.default_rng(seed=42)
        ref = rng.normal(0, 1, size=500)
        cur_same = rng.normal(0, 1, size=500)
        cur_drift = rng.normal(5, 1, size=500)

        ref_path = tmp_path / "ref.csv"
        cur_same_path = tmp_path / "cur_same.csv"
        cur_drift_path = tmp_path / "cur_drift.csv"
        np.savetxt(ref_path, ref, header="exposure", comments="")
        np.savetxt(cur_same_path, cur_same, header="exposure", comments="")
        np.savetxt(cur_drift_path, cur_drift, header="notional", comments="")

        cfg = {
            "model": {"name": "multi_metric", "version": "1.0"},
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "stable_metric",
                        "source": "builtin",
                        "detector": "ks",
                        "reference_dataset": str(ref_path),
                        "current_dataset": str(cur_same_path),
                        "columns": ["exposure"],
                        "threshold": 0.05,
                    },
                    {
                        "name": "drifting_metric",
                        "source": "builtin",
                        "detector": "ks",
                        "reference_dataset": str(ref_path),
                        "current_dataset": str(cur_drift_path),
                        "columns": ["notional"],
                        "threshold": 0.05,
                    },
                ],
                "on_drift": {"revalidate": False, "freeze_evidence": False},
            },
        }
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(cfg)
        assert result.overall_drifted is True
        assert result.exit_code == 1
        # One drifted, one not
        drifted = [m for m in result.metric_results if m.drifted]
        stable = [m for m in result.metric_results if not m.drifted]
        assert len(drifted) == 1
        assert len(stable) == 1


# ---------------------------------------------------------------------------
# §8  Webhook payload
# ---------------------------------------------------------------------------


class TestWebhookPayload:
    def test_webhook_payload_shape(self):
        from mrm.monitor.webhook import build_webhook_payload

        payload = build_webhook_payload(
            event="drift_detected",
            model_name="ccr_monte_carlo",
            run_id="run-001",
            metric_results=[
                MetricResult(
                    name="prediction_quality",
                    source="mlflow",
                    drifted=True,
                    value=0.18,
                    threshold=0.15,
                )
            ],
            evidence_packet_id="pkt-001",
            compliance_references=["CPS 230 Para 35", "SR 26-2 §II.AI.D"],
        )
        assert payload["event"] == "drift_detected"
        assert payload["model_name"] == "ccr_monte_carlo"
        assert payload["run_id"] == "run-001"
        assert len(payload["metrics"]) == 1
        assert payload["metrics"][0]["drifted"] is True
        assert payload["evidence_packet_id"] == "pkt-001"
        assert "riskattest_version" in payload
        assert "timestamp" in payload
        assert "compliance_references" in payload

    def test_webhook_payload_no_compliance(self):
        from mrm.monitor.webhook import build_webhook_payload

        payload = build_webhook_payload(
            event="revalidation_complete",
            model_name="test",
            run_id="run-002",
            metric_results=[],
        )
        assert payload["event"] == "revalidation_complete"
        assert payload["compliance_references"] == []


# ---------------------------------------------------------------------------
# §9  Webhook sender (mocked HTTP)
# ---------------------------------------------------------------------------


class TestWebhookSender:
    def test_send_webhook_success(self):
        from mrm.monitor.webhook import send_webhook

        webhook_cfg = WebhookConfig(
            url="https://hooks.slack.com/test",
            events=["drift_detected"],
        )
        payload = {"event": "drift_detected", "model_name": "test"}

        with patch("mrm.monitor.webhook.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.read.return_value = b"ok"
            mock_urlopen.return_value.__enter__ = MagicMock(return_value=mock_response)
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

            result = send_webhook(webhook_cfg, payload)
            assert result is True

    def test_send_webhook_filters_events(self):
        from mrm.monitor.webhook import send_webhook

        webhook_cfg = WebhookConfig(
            url="https://hooks.slack.com/test",
            events=["drift_detected"],
        )
        payload = {"event": "revalidation_complete", "model_name": "test"}

        # Should be a no-op — event not in the webhook's event list
        result = send_webhook(webhook_cfg, payload)
        assert result is False  # filtered out, not sent


# ---------------------------------------------------------------------------
# §10  MonitorRunResult — aggregate result
# ---------------------------------------------------------------------------


class TestMonitorRunResult:
    def test_no_drift_result(self):
        result = MonitorRunResult(
            run_id="r1",
            model_name="test",
            overall_drifted=False,
            metric_results=[],
            exit_code=0,
        )
        assert result.exit_code == 0
        assert result.overall_drifted is False

    def test_drift_result(self):
        result = MonitorRunResult(
            run_id="r2",
            model_name="test",
            overall_drifted=True,
            metric_results=[
                MetricResult("m1", "builtin", True, 0.18, 0.15)
            ],
            exit_code=1,
        )
        assert result.exit_code == 1
        assert result.overall_drifted is True

    def test_error_result(self):
        result = MonitorRunResult(
            run_id="r3",
            model_name="test",
            overall_drifted=False,
            metric_results=[],
            exit_code=2,
            error="Connection refused",
        )
        assert result.exit_code == 2
        assert result.error is not None


# ---------------------------------------------------------------------------
# §11  Integration: full pipeline no-drift
# ---------------------------------------------------------------------------


class TestMonitorPipelineIntegration:
    def test_full_pipeline_no_drift(self, tmp_path):
        """End-to-end: no drift → exit 0, log written, no webhooks."""
        rng = np.random.default_rng(seed=42)
        ref = rng.normal(0, 1, size=500)
        cur = rng.normal(0, 1, size=500)

        ref_path = tmp_path / "ref.csv"
        cur_path = tmp_path / "cur.csv"
        np.savetxt(ref_path, ref, header="exposure", comments="")
        np.savetxt(cur_path, cur, header="exposure", comments="")

        cfg = {
            "model": {"name": "pipeline_model", "version": "1.0"},
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "data_drift",
                        "source": "builtin",
                        "detector": "ks",
                        "reference_dataset": str(ref_path),
                        "current_dataset": str(cur_path),
                        "columns": ["exposure"],
                        "threshold": 0.05,
                    }
                ],
                "on_drift": {"revalidate": False, "freeze_evidence": False},
                "webhooks": [
                    {"url": "https://hooks.slack.com/test", "events": ["drift_detected"]}
                ],
            },
        }
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(cfg)

        # Exit 0
        assert result.exit_code == 0
        assert result.overall_drifted is False

        # Log written
        log = MonitoringLog(tmp_path / "pipeline_model")
        entries = log.read_all()
        assert len(entries) == 1
        assert entries[0]["overall_drifted"] is False
        assert entries[0]["exit_code"] == 0

    def test_full_pipeline_drift_detected(self, tmp_path):
        """End-to-end: drift → exit 1, log records drift, webhook would fire."""
        rng = np.random.default_rng(seed=42)
        ref = rng.normal(0, 1, size=500)
        cur = rng.normal(5, 1, size=500)

        ref_path = tmp_path / "ref.csv"
        cur_path = tmp_path / "cur.csv"
        np.savetxt(ref_path, ref, header="exposure", comments="")
        np.savetxt(cur_path, cur, header="exposure", comments="")

        cfg = {
            "model": {"name": "drift_pipeline", "version": "1.0"},
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "data_drift",
                        "source": "builtin",
                        "detector": "ks",
                        "reference_dataset": str(ref_path),
                        "current_dataset": str(cur_path),
                        "columns": ["exposure"],
                        "threshold": 0.05,
                    }
                ],
                "on_drift": {"revalidate": False, "freeze_evidence": False},
                "webhooks": [],
            },
        }
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(cfg)

        assert result.exit_code == 1
        assert result.overall_drifted is True

        log = MonitoringLog(tmp_path / "drift_pipeline")
        entries = log.read_all()
        assert len(entries) == 1
        assert entries[0]["overall_drifted"] is True
        assert entries[0]["exit_code"] == 1

    def test_file_metric_pipeline(self, tmp_path):
        """End-to-end using a file metric source."""
        metrics_file = tmp_path / "metrics.json"
        metrics_file.write_text(json.dumps({"rmse": 0.08}))

        cfg = {
            "model": {"name": "file_pipeline", "version": "1.0"},
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "prediction_quality",
                        "source": "file",
                        "path": str(metrics_file),
                        "metric_key": "rmse",
                        "threshold": 0.15,
                        "comparison": "greater_than",
                    }
                ],
                "on_drift": {"revalidate": False, "freeze_evidence": False},
            },
        }
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(cfg)
        assert result.exit_code == 0
        assert result.overall_drifted is False


# ---------------------------------------------------------------------------
# §12  Evidence freeze and trigger integration
# ---------------------------------------------------------------------------


class TestEvidenceAndTriggerIntegration:
    """Verify that evidence packets and triggers fire when drift is detected."""

    @pytest.fixture
    def drift_config_with_evidence(self, tmp_path):
        """Model config where drift WILL be detected, with freeze_evidence on."""
        rng = np.random.default_rng(seed=42)
        ref = rng.normal(0, 1, size=500)
        cur = rng.normal(5, 1, size=500)  # massive shift

        ref_path = tmp_path / "ref.csv"
        cur_path = tmp_path / "cur.csv"
        np.savetxt(ref_path, ref, header="exposure", comments="")
        np.savetxt(cur_path, cur, header="exposure", comments="")

        return {
            "model": {
                "name": "evidence_model",
                "version": "2.1",
                "risk_tier": "tier_1",
            },
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "data_drift",
                        "source": "builtin",
                        "detector": "ks",
                        "reference_dataset": str(ref_path),
                        "current_dataset": str(cur_path),
                        "columns": ["exposure"],
                        "threshold": 0.05,
                    }
                ],
                "on_drift": {
                    "revalidate": False,
                    "freeze_evidence": True,
                    "resolve_triggers": True,
                },
                "webhooks": [],
            },
        }

    @pytest.fixture
    def no_drift_config(self, tmp_path):
        """Model config where NO drift is detected."""
        rng = np.random.default_rng(seed=42)
        ref = rng.normal(0, 1, size=500)
        cur = rng.normal(0, 1, size=500)

        ref_path = tmp_path / "ref.csv"
        cur_path = tmp_path / "cur.csv"
        np.savetxt(ref_path, ref, header="exposure", comments="")
        np.savetxt(cur_path, cur, header="exposure", comments="")

        return {
            "model": {
                "name": "stable_model",
                "version": "1.0",
                "risk_tier": "tier_1",
            },
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "data_drift",
                        "source": "builtin",
                        "detector": "ks",
                        "reference_dataset": str(ref_path),
                        "current_dataset": str(cur_path),
                        "columns": ["exposure"],
                        "threshold": 0.05,
                    }
                ],
                "on_drift": {
                    "revalidate": False,
                    "freeze_evidence": True,
                    "resolve_triggers": True,
                },
                "webhooks": [],
            },
        }

    def test_drift_creates_evidence_packet(
        self, drift_config_with_evidence, tmp_path
    ):
        """When freeze_evidence is True and drift detected, an EvidencePacket is created."""
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(drift_config_with_evidence)

        # Evidence packet ID must be populated
        assert result.evidence_packet_id is not None

        # Evidence JSON file must exist on disk
        evidence_dir = tmp_path / "evidence_model" / "evidence"
        assert evidence_dir.exists()
        evidence_files = list(evidence_dir.glob("*.json"))
        assert len(evidence_files) == 1

        # Verify the packet content
        packet_data = json.loads(evidence_files[0].read_text())
        assert packet_data["packet_id"] == result.evidence_packet_id
        assert packet_data["model_name"] == "evidence_model"
        assert packet_data["model_version"] == "2.1"
        assert packet_data["model_artifact_hash"] == "monitoring-cycle"
        assert packet_data["created_by"] == "mrm-monitor"
        assert "drift_metrics" in packet_data["test_results"]
        assert "monitoring" in packet_data["compliance_mappings"]
        assert "SR 26-2 §II.AI.D" in packet_data["compliance_mappings"]["monitoring"]
        assert "CPS 230 Para 35" in packet_data["compliance_mappings"]["monitoring"]
        assert packet_data["metadata"]["trigger"] == "drift_detected"
        # content_hash must be computed
        assert packet_data["content_hash"] is not None

    def test_drift_fires_trigger(self, drift_config_with_evidence, tmp_path):
        """When drift detected, a DRIFT trigger is fired."""
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(drift_config_with_evidence)

        assert len(result.triggers_fired) == 1
        trigger_id = result.triggers_fired[0]
        assert trigger_id.startswith("DRIFT-evidence_model-")

    def test_no_drift_no_evidence(self, no_drift_config, tmp_path):
        """When no drift, no evidence packet or trigger."""
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(no_drift_config)

        assert result.evidence_packet_id is None
        assert result.triggers_fired == []
        assert result.overall_drifted is False

        # No evidence directory should be created
        evidence_dir = tmp_path / "stable_model" / "evidence"
        assert not evidence_dir.exists()

    def test_drift_without_freeze_evidence_skips_packet(self, tmp_path):
        """When freeze_evidence is False, no evidence packet even on drift."""
        rng = np.random.default_rng(seed=42)
        ref = rng.normal(0, 1, size=500)
        cur = rng.normal(5, 1, size=500)

        ref_path = tmp_path / "ref.csv"
        cur_path = tmp_path / "cur.csv"
        np.savetxt(ref_path, ref, header="exposure", comments="")
        np.savetxt(cur_path, cur, header="exposure", comments="")

        cfg = {
            "model": {"name": "no_freeze_model", "version": "1.0"},
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "data_drift",
                        "source": "builtin",
                        "detector": "ks",
                        "reference_dataset": str(ref_path),
                        "current_dataset": str(cur_path),
                        "columns": ["exposure"],
                        "threshold": 0.05,
                    }
                ],
                "on_drift": {
                    "revalidate": False,
                    "freeze_evidence": False,
                },
                "webhooks": [],
            },
        }
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(cfg)

        # Drift is detected
        assert result.overall_drifted is True
        # But no evidence packet since freeze_evidence is False
        assert result.evidence_packet_id is None
        # Trigger still fires regardless of freeze_evidence flag
        assert len(result.triggers_fired) == 1

    def test_log_records_evidence_and_triggers(
        self, drift_config_with_evidence, tmp_path
    ):
        """Monitoring log entry includes evidence_packet_id and triggers_fired."""
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(drift_config_with_evidence)

        log = MonitoringLog(tmp_path / "evidence_model")
        entries = log.read_all()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["evidence_packet_id"] == result.evidence_packet_id
        assert entry["triggers_fired"] == result.triggers_fired


# ---------------------------------------------------------------------------
# §13  Webhook proxy/TLS support (Gap 1)
# ---------------------------------------------------------------------------


class TestWebhookProxyTLS:
    """Verify that send_webhook() builds SSL contexts and proxy handlers
    when bank-environment env vars are set."""

    def test_ssl_context_built_when_ssl_cert_file_set(self):
        """SSL_CERT_FILE env var causes an SSLContext with custom cafile."""
        from mrm.monitor.webhook import _build_ssl_context
        import ssl

        mock_ctx = MagicMock(spec=ssl.SSLContext)
        with patch.dict(os.environ, {"SSL_CERT_FILE": "/etc/ssl/custom-ca.pem"}, clear=False), \
             patch("mrm.monitor.webhook.ssl.create_default_context", return_value=mock_ctx) as mock_create:
            ctx = _build_ssl_context()
            assert ctx is not None
            assert ctx is mock_ctx
            mock_create.assert_called_once_with(cafile="/etc/ssl/custom-ca.pem")

    def test_ssl_context_built_when_requests_ca_bundle_set(self):
        """REQUESTS_CA_BUNDLE env var (Zscaler convention) also works."""
        from mrm.monitor.webhook import _build_ssl_context
        import ssl

        mock_ctx = MagicMock(spec=ssl.SSLContext)
        with patch.dict(os.environ, {"REQUESTS_CA_BUNDLE": "/usr/share/ca.pem"}, clear=False), \
             patch("mrm.monitor.webhook.ssl.create_default_context", return_value=mock_ctx) as mock_create:
            ctx = _build_ssl_context()
            assert ctx is not None
            mock_create.assert_called_once_with(cafile="/usr/share/ca.pem")

    def test_ssl_context_none_when_no_env_vars(self):
        """No SSL env vars → returns None (use stdlib defaults)."""
        from mrm.monitor.webhook import _build_ssl_context

        with patch.dict(os.environ, {}, clear=True):
            ctx = _build_ssl_context()
            assert ctx is None

    def test_proxy_handler_built_when_https_proxy_set(self):
        """HTTPS_PROXY env var creates an OpenerDirector with ProxyHandler."""
        from mrm.monitor.webhook import _build_opener

        with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.bank.com:8080"}, clear=True):
            opener = _build_opener(ssl_ctx=None)
            assert opener is not None
            # Verify it has a ProxyHandler in its handler chain
            handler_types = [type(h).__name__ for h in opener.handlers]
            assert "ProxyHandler" in handler_types

    def test_proxy_handler_built_when_http_proxy_set(self):
        """HTTP_PROXY env var also triggers opener creation."""
        from mrm.monitor.webhook import _build_opener

        with patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.bank.com:8080"}, clear=True):
            opener = _build_opener(ssl_ctx=None)
            assert opener is not None

    def test_opener_none_when_no_proxy_or_ssl(self):
        """No env vars and no SSL context → None (use simple urlopen)."""
        from mrm.monitor.webhook import _build_opener

        with patch.dict(os.environ, {}, clear=True):
            opener = _build_opener(ssl_ctx=None)
            assert opener is None

    def test_opener_built_for_ssl_context_only(self):
        """Even without proxy, an SSLContext triggers an opener with HTTPSHandler."""
        import ssl
        from mrm.monitor.webhook import _build_opener

        ctx = ssl.create_default_context()
        with patch.dict(os.environ, {}, clear=True):
            opener = _build_opener(ssl_ctx=ctx)
            assert opener is not None
            handler_types = [type(h).__name__ for h in opener.handlers]
            assert "HTTPSHandler" in handler_types

    def test_webhook_config_timeout_from_yaml(self):
        """WebhookConfig.timeout is parsed from YAML and defaults to 30."""
        raw = {
            "enabled": True,
            "metrics": [],
            "webhooks": [
                {"url": "https://hooks.slack.com/a", "timeout": 60},
                {"url": "https://hooks.slack.com/b"},
            ],
        }
        cfg = parse_monitoring_config(raw)
        assert cfg.webhooks[0].timeout == 60
        assert cfg.webhooks[1].timeout == 30  # default


# ---------------------------------------------------------------------------
# §14  Audit provenance in monitoring log (Gap 2)
# ---------------------------------------------------------------------------


class TestAuditProvenance:
    """Verify that MonitoringLogEntry captures provenance fields and that
    the runner populates them from env vars / system info."""

    def test_log_entry_includes_provenance_fields(self, tmp_path):
        """MonitoringLogEntry accepts and serialises provenance fields."""
        entry = MonitoringLogEntry(
            run_id="prov-001",
            model_name="test_model",
            timestamp="2026-06-18T10:00:00Z",
            metrics=[],
            overall_drifted=False,
            action_taken="none",
            exit_code=0,
            created_by="svc-mrm@bank.com",
            hostname="scheduler-prod-01",
            invocation="airflow",
            config_hash="abc123def456",
        )
        d = entry.to_dict()
        assert d["created_by"] == "svc-mrm@bank.com"
        assert d["hostname"] == "scheduler-prod-01"
        assert d["invocation"] == "airflow"
        assert d["config_hash"] == "abc123def456"

    def test_provenance_round_trips_through_jsonl(self, tmp_path):
        """Provenance fields survive write→read via MonitoringLog."""
        log = MonitoringLog(tmp_path / "prov_model")
        entry = MonitoringLogEntry(
            run_id="prov-002",
            model_name="prov_model",
            timestamp="2026-06-18T10:00:00Z",
            metrics=[],
            overall_drifted=False,
            action_taken="none",
            exit_code=0,
            created_by="admin@bank.com",
            hostname="node-42",
            invocation="cron",
            config_hash="deadbeef",
        )
        log.append(entry)
        entries = log.read_all()
        assert len(entries) == 1
        assert entries[0]["created_by"] == "admin@bank.com"
        assert entries[0]["hostname"] == "node-42"
        assert entries[0]["invocation"] == "cron"
        assert entries[0]["config_hash"] == "deadbeef"

    def test_runner_populates_provenance(self, tmp_path):
        """MonitorRunner._write_log fills created_by, hostname, invocation, config_hash."""
        import socket as _socket

        rng = np.random.default_rng(seed=42)
        ref = rng.normal(0, 1, size=500)
        cur = rng.normal(0, 1, size=500)

        ref_path = tmp_path / "ref.csv"
        cur_path = tmp_path / "cur.csv"
        np.savetxt(ref_path, ref, header="exposure", comments="")
        np.savetxt(cur_path, cur, header="exposure", comments="")

        cfg = {
            "model": {"name": "prov_runner_model", "version": "1.0"},
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "data_drift",
                        "source": "builtin",
                        "detector": "ks",
                        "reference_dataset": str(ref_path),
                        "current_dataset": str(cur_path),
                        "columns": ["exposure"],
                        "threshold": 0.05,
                    }
                ],
                "on_drift": {"revalidate": False, "freeze_evidence": False},
            },
        }

        env_overrides = {
            "MRM_MONITOR_USER": "svc-mrm@bank.com",
            "MRM_INVOCATION": "airflow",
        }
        with patch.dict(os.environ, env_overrides, clear=False):
            runner = MonitorRunner(log_dir=tmp_path)
            runner.run(cfg)

        log = MonitoringLog(tmp_path / "prov_runner_model")
        entries = log.read_all()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["created_by"] == "svc-mrm@bank.com"
        assert entry["hostname"] == _socket.gethostname()
        assert entry["invocation"] == "airflow"
        assert entry["config_hash"] is not None
        assert len(entry["config_hash"]) == 64  # SHA-256 hex digest

    def test_config_hash_deterministic(self):
        """Same monitoring config dict always produces the same hash."""
        from mrm.monitor.runner import _compute_config_hash

        config_a = {
            "enabled": True,
            "schedule": "daily",
            "metrics": [
                {"name": "drift", "source": "builtin", "threshold": 0.05}
            ],
        }
        # Same content, different insertion order
        config_b = {
            "schedule": "daily",
            "enabled": True,
            "metrics": [
                {"name": "drift", "threshold": 0.05, "source": "builtin"}
            ],
        }
        hash_a = _compute_config_hash(config_a)
        hash_b = _compute_config_hash(config_b)
        assert hash_a == hash_b
        assert len(hash_a) == 64  # SHA-256

    def test_config_hash_differs_for_different_config(self):
        """Different configs produce different hashes."""
        from mrm.monitor.runner import _compute_config_hash

        config_a = {"enabled": True, "schedule": "daily"}
        config_b = {"enabled": True, "schedule": "weekly"}
        assert _compute_config_hash(config_a) != _compute_config_hash(config_b)

    def test_provenance_defaults_without_env_vars(self, tmp_path):
        """Without MRM_MONITOR_USER, falls back to USER env var or 'unknown'."""
        rng = np.random.default_rng(seed=42)
        ref = rng.normal(0, 1, size=500)
        cur = rng.normal(0, 1, size=500)

        ref_path = tmp_path / "ref.csv"
        cur_path = tmp_path / "cur.csv"
        np.savetxt(ref_path, ref, header="exposure", comments="")
        np.savetxt(cur_path, cur, header="exposure", comments="")

        cfg = {
            "model": {"name": "default_prov_model", "version": "1.0"},
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "data_drift",
                        "source": "builtin",
                        "detector": "ks",
                        "reference_dataset": str(ref_path),
                        "current_dataset": str(cur_path),
                        "columns": ["exposure"],
                        "threshold": 0.05,
                    }
                ],
                "on_drift": {"revalidate": False, "freeze_evidence": False},
            },
        }

        # Remove MRM-specific env vars but keep USER
        env = {k: v for k, v in os.environ.items()
               if k not in ("MRM_MONITOR_USER", "MRM_INVOCATION")}
        with patch.dict(os.environ, env, clear=True):
            runner = MonitorRunner(log_dir=tmp_path)
            runner.run(cfg)

        log = MonitoringLog(tmp_path / "default_prov_model")
        entries = log.read_all()
        assert len(entries) == 1
        entry = entries[0]
        # Should fall back to USER or "unknown"
        assert entry["created_by"] is not None
        assert entry["invocation"] == "manual"  # default
        assert entry["hostname"] is not None


# ---------------------------------------------------------------------------
# §15  Environment variable expansion in config (${VAR} syntax)
# ---------------------------------------------------------------------------


class TestEnvVarExpansion:
    """Verify that ``${VAR}`` and ``${VAR:-default}`` are expanded
    in monitoring config values before parsing."""

    def test_expand_simple_string(self):
        """${VAR} in a string is replaced by the env var value."""
        from mrm.monitor.config import _expand_env

        with patch.dict(os.environ, {"MLFLOW_URI": "http://mlflow.bank.com:5000"}):
            result = _expand_env("${MLFLOW_URI}")
            assert result == "http://mlflow.bank.com:5000"

    def test_expand_with_default(self):
        """${VAR:-default} uses the default when VAR is unset."""
        from mrm.monitor.config import _expand_env

        with patch.dict(os.environ, {}, clear=True):
            result = _expand_env("${MISSING_VAR:-fallback_value}")
            assert result == "fallback_value"

    def test_expand_with_default_var_set(self):
        """${VAR:-default} uses the env var value when VAR IS set."""
        from mrm.monitor.config import _expand_env

        with patch.dict(os.environ, {"MY_VAR": "real_value"}):
            result = _expand_env("${MY_VAR:-fallback_value}")
            assert result == "real_value"

    def test_expand_in_dict(self):
        """${VAR} expansion works recursively in dicts."""
        from mrm.monitor.config import _expand_env

        with patch.dict(os.environ, {"DB_HOST": "prod.databricks.com"}):
            result = _expand_env({"host": "${DB_HOST}", "port": 443})
            assert result == {"host": "prod.databricks.com", "port": 443}

    def test_expand_in_list(self):
        """${VAR} expansion works recursively in lists."""
        from mrm.monitor.config import _expand_env

        with patch.dict(os.environ, {"COL1": "notional", "COL2": "pd_annual"}):
            result = _expand_env(["${COL1}", "${COL2}", "lgd"])
            assert result == ["notional", "pd_annual", "lgd"]

    def test_expand_non_string_passthrough(self):
        """Non-string values (int, float, bool) pass through unchanged."""
        from mrm.monitor.config import _expand_env

        assert _expand_env(42) == 42
        assert _expand_env(3.14) == 3.14
        assert _expand_env(True) is True
        assert _expand_env(None) is None

    def test_expand_multiple_vars_in_one_string(self):
        """Multiple ${VAR} references in one string are all expanded."""
        from mrm.monitor.config import _expand_env

        with patch.dict(os.environ, {"SCHEME": "https", "HOST": "mlflow.bank.com"}):
            result = _expand_env("${SCHEME}://${HOST}/api")
            assert result == "https://mlflow.bank.com/api"

    def test_expand_missing_var_becomes_empty(self):
        """${UNSET_VAR} with no default becomes an empty string."""
        from mrm.monitor.config import _expand_env

        with patch.dict(os.environ, {}, clear=True):
            result = _expand_env("prefix-${UNSET_VAR}-suffix")
            assert result == "prefix--suffix"

    def test_env_var_expansion_in_parsed_config(self):
        """parse_monitoring_config expands ${VAR} in metric fields."""
        raw = {
            "enabled": True,
            "metrics": [
                {
                    "name": "mlflow_drift",
                    "source": "mlflow",
                    "tracking_uri": "${MLFLOW_TRACKING_URI}",
                    "experiment": "ccr_prod",
                    "metric_key": "rmse",
                    "threshold": 0.15,
                    "comparison": "greater_than",
                }
            ],
        }
        with patch.dict(
            os.environ,
            {"MLFLOW_TRACKING_URI": "https://mlflow.internal.bank.com:5000"},
        ):
            cfg = parse_monitoring_config(raw)
            assert cfg.metrics[0].tracking_uri == "https://mlflow.internal.bank.com:5000"

    def test_env_var_expansion_in_webhook_url(self):
        """parse_monitoring_config expands ${VAR} in webhook URLs and headers."""
        raw = {
            "enabled": True,
            "metrics": [],
            "webhooks": [
                {
                    "url": "https://hooks.slack.com/services/${WEBHOOK_TOKEN}",
                    "headers": {"Authorization": "Bearer ${SLACK_TOKEN}"},
                    "events": ["drift_detected"],
                }
            ],
        }
        with patch.dict(
            os.environ,
            {"WEBHOOK_TOKEN": "T00/B00/xxxx", "SLACK_TOKEN": "xoxb-secret"},
        ):
            cfg = parse_monitoring_config(raw)
            assert cfg.webhooks[0].url == "https://hooks.slack.com/services/T00/B00/xxxx"
            assert cfg.webhooks[0].headers["Authorization"] == "Bearer xoxb-secret"

    def test_env_var_expansion_databricks_token(self):
        """Databricks token from ${VAR} in YAML is expanded before parsing."""
        raw = {
            "enabled": True,
            "metrics": [
                {
                    "name": "db_drift",
                    "source": "databricks",
                    "host": "${DATABRICKS_HOST}",
                    "token": "${DATABRICKS_TOKEN}",
                    "warehouse_id": "wh-123",
                    "table_name": "catalog.schema.tbl",
                    "metric_column": "ks_statistic",
                    "threshold": 0.05,
                    "comparison": "greater_than",
                }
            ],
        }
        with patch.dict(
            os.environ,
            {
                "DATABRICKS_HOST": "https://prod.cloud.databricks.com",
                "DATABRICKS_TOKEN": "dapi_secret_12345",
            },
        ):
            cfg = parse_monitoring_config(raw)
            assert cfg.metrics[0].host == "https://prod.cloud.databricks.com"
            assert cfg.metrics[0].token == "dapi_secret_12345"


# ---------------------------------------------------------------------------
# §16  Graceful degradation — per-metric error isolation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """Verify that one metric source failing does not abort the entire
    monitoring run. This is critical for bank environments where one
    CloudWatch namespace being down should not blind the system to
    drift detected by local metrics."""

    def test_one_metric_fails_others_succeed(self, tmp_path):
        """When one metric fails, surviving metrics are still evaluated."""
        # Create a valid file metric
        metrics_file = tmp_path / "metrics.json"
        metrics_file.write_text(json.dumps({"rmse": 0.20}))

        cfg = {
            "model": {"name": "partial_fail_model", "version": "1.0"},
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "broken_metric",
                        "source": "file",
                        "path": "/nonexistent/file.json",
                        "metric_key": "x",
                        "threshold": 0.5,
                        "comparison": "greater_than",
                    },
                    {
                        "name": "working_metric",
                        "source": "file",
                        "path": str(metrics_file),
                        "metric_key": "rmse",
                        "threshold": 0.15,
                        "comparison": "greater_than",
                    },
                ],
                "on_drift": {"revalidate": False, "freeze_evidence": False},
            },
        }
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(cfg)

        # The working metric drifted (0.20 > 0.15), so exit code 1 not 2
        assert result.exit_code == 1
        assert result.overall_drifted is True
        assert len(result.metric_results) == 1  # only the working one
        assert result.metric_results[0].name == "working_metric"
        assert result.error is None  # not an error run

    def test_all_metrics_fail_exits_2(self, tmp_path):
        """When ALL metrics fail, exit code is 2 with combined error."""
        cfg = {
            "model": {"name": "all_fail_model", "version": "1.0"},
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "broken_1",
                        "source": "file",
                        "path": "/nonexistent/a.json",
                        "metric_key": "x",
                        "threshold": 0.5,
                        "comparison": "greater_than",
                    },
                    {
                        "name": "broken_2",
                        "source": "file",
                        "path": "/nonexistent/b.json",
                        "metric_key": "y",
                        "threshold": 0.5,
                        "comparison": "greater_than",
                    },
                ],
                "on_drift": {"revalidate": False, "freeze_evidence": False},
            },
        }
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(cfg)

        assert result.exit_code == 2
        assert result.overall_drifted is False
        assert result.error is not None
        assert "broken_1" in result.error
        assert "broken_2" in result.error

    def test_one_fails_no_drift_in_survivors(self, tmp_path):
        """When one metric fails but surviving metrics show no drift → exit 0."""
        metrics_file = tmp_path / "metrics.json"
        metrics_file.write_text(json.dumps({"rmse": 0.08}))

        cfg = {
            "model": {"name": "partial_ok_model", "version": "1.0"},
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "broken_metric",
                        "source": "file",
                        "path": "/nonexistent/file.json",
                        "metric_key": "x",
                        "threshold": 0.5,
                        "comparison": "greater_than",
                    },
                    {
                        "name": "stable_metric",
                        "source": "file",
                        "path": str(metrics_file),
                        "metric_key": "rmse",
                        "threshold": 0.15,
                        "comparison": "greater_than",
                    },
                ],
                "on_drift": {"revalidate": False, "freeze_evidence": False},
            },
        }
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(cfg)

        # 0.08 < 0.15, no drift
        assert result.exit_code == 0
        assert result.overall_drifted is False
        assert len(result.metric_results) == 1
        assert result.metric_results[0].name == "stable_metric"

    def test_failed_metric_logged_as_warning(self, tmp_path):
        """Failed metric is logged but does not appear in metric_results."""
        metrics_file = tmp_path / "metrics.json"
        metrics_file.write_text(json.dumps({"acc": 0.95}))

        cfg = {
            "model": {"name": "warning_model", "version": "1.0"},
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "bad_source",
                        "source": "file",
                        "path": "/nonexistent/x.json",
                        "metric_key": "x",
                        "threshold": 0.5,
                        "comparison": "greater_than",
                    },
                    {
                        "name": "good_source",
                        "source": "file",
                        "path": str(metrics_file),
                        "metric_key": "acc",
                        "threshold": 0.90,
                        "comparison": "less_than",
                    },
                ],
                "on_drift": {"revalidate": False, "freeze_evidence": False},
            },
        }
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(cfg)

        # Only the successful metric is in results
        metric_names = [m.name for m in result.metric_results]
        assert "bad_source" not in metric_names
        assert "good_source" in metric_names

    def test_all_fail_writes_error_log(self, tmp_path):
        """When all metrics fail, the log entry records exit_code=2 and error."""
        cfg = {
            "model": {"name": "total_fail_model", "version": "1.0"},
            "monitoring": {
                "enabled": True,
                "metrics": [
                    {
                        "name": "broken",
                        "source": "file",
                        "path": "/nonexistent/z.json",
                        "metric_key": "z",
                        "threshold": 0.5,
                        "comparison": "greater_than",
                    }
                ],
                "on_drift": {"revalidate": False, "freeze_evidence": False},
            },
        }
        runner = MonitorRunner(log_dir=tmp_path)
        result = runner.run(cfg)

        log = MonitoringLog(tmp_path / "total_fail_model")
        entries = log.read_all()
        assert len(entries) == 1
        assert entries[0]["exit_code"] == 2
        assert entries[0]["action_taken"] == "error"
        assert "broken" in entries[0].get("error", "")
