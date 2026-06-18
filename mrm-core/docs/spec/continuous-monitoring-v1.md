# Continuous Model Monitoring — Specification v1

**Status:** Public Review (PRD-2)
**Author:** RiskAttest Core
**Date:** 2026-06-18

---

## §1 Problem

RiskAttest validates models at a point in time. Regulators are moving
toward continuous validation:

- **SR 26-2 (Fed, 2026):** Ongoing model performance monitoring with
  documented escalation procedures.
- **CPS 230 (APRA, 2025):** Continuous monitoring of critical
  operations; CPG 235 expects ongoing model performance tracking.
- **EU AI Act Art. 72:** High-risk systems require post-market
  monitoring "continuously and systematically."

Databricks (TruEra), AWS SageMaker Monitor, Azure ML Monitor all
provide monitoring infrastructure — but none produce regulatory
evidence. The gap: turning drift signals into immutable, compliance-
mapped EvidencePackets with an audit trail.

## §2 Design Principles

1. **No daemon.** Banks run Airflow, Databricks Workflows, AWS Step
   Functions, or cron. `mrm monitor run` is a single CLI invocation
   that does one monitoring cycle. The bank's scheduler calls it.
2. **No new infrastructure.** Drift metrics come from sources the bank
   already has: MLflow metrics, SageMaker Monitor output, CloudWatch
   metrics, local CSV/JSON, or RiskAttest's own drift detectors.
3. **Evidence-first.** Every monitoring cycle that detects drift
   produces an EvidencePacket (or at minimum a monitoring log entry)
   with compliance paragraph mapping. Silent monitoring is useless for
   regulatory examination.
4. **Webhook-out, not UI.** Alert via HTTP POST (Slack, PagerDuty,
   ServiceNow, Splunk). Banks already have alert routing; don't
   reinvent it.

## §3 Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Bank Scheduler (Airflow / cron / Databricks Workflows)     │
│                                                              │
│  Schedule: daily / weekly / model-specific                   │
│  Command:  mrm monitor run --models ccr_monte_carlo          │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  mrm monitor run                                             │
│                                                              │
│  1. Load model config + monitoring section from YAML         │
│  2. Collect metrics from configured sources (graceful         │
│     degradation: per-metric error isolation):                │
│     ├─ builtin:    run RiskAttest drift detectors in-process │
│     ├─ mlflow:     query MLflow tracking server metrics      │
│     ├─ cloudwatch: query CloudWatch metric statistics        │
│     ├─ databricks: query Lakehouse Monitoring drift tables   │
│     └─ file:       read metrics from JSON/CSV                │
│  3. Evaluate thresholds → DriftResult per metric             │
│  4. If ANY threshold breached:                               │
│     a. Run full validation suite (mrm test)                  │
│     b. Freeze EvidencePacket with drift + test results       │
│     c. Fire triggers (TriggerEngine)                         │
│     d. POST webhook notification                             │
│  5. Write MonitoringLog entry (append-only JSON)             │
│  6. Exit 0 (no drift) or 1 (drift detected) or 2 (error)    │
└──────────────────────────────────────────────────────────────┘
```

## §4 YAML Configuration

### §4.1 Model-level monitoring config

```yaml
# models/ccr/ccr_monte_carlo.yml
monitoring:
  enabled: true
  schedule: "daily"          # Informational; actual schedule is the caller's job

  # Metric sources — evaluated in order, first match wins per metric
  metrics:
    - name: data_drift
      source: builtin        # Use RiskAttest's own KS detector
      detector: ks
      reference_dataset: data/reference_portfolio.csv
      current_dataset: data/current_portfolio.csv
      columns: [exposure, notional, lgd]
      threshold: 0.05         # KS p-value

    - name: model_drift
      source: builtin
      detector: page_hinkley
      reference_dataset: data/reference_residuals.csv
      current_dataset: data/current_residuals.csv
      columns: [residual]
      threshold: 50.0          # Page-Hinkley lambda

    - name: prediction_quality
      source: mlflow
      tracking_uri: ${MLFLOW_TRACKING_URI}
      experiment: ccr_monte_carlo
      metric_key: rmse
      threshold: 0.15          # Absolute value
      comparison: greater_than  # Drift if metric > threshold

    - name: feature_drift_psi
      source: cloudwatch
      namespace: SageMaker/Endpoints
      metric_name: FeatureDriftPSI
      dimensions:
        EndpointName: ccr-monte-carlo-prod
      threshold: 0.25
      comparison: greater_than
      period: 86400            # 1 day in seconds

    - name: custom_metric
      source: file
      path: /data/monitoring/latest_metrics.json
      metric_key: gini_coefficient
      threshold: 0.60
      comparison: less_than    # Drift if metric < threshold

  # What to do when drift is detected
  on_drift:
    revalidate: true           # Run full test suite
    freeze_evidence: true      # Create EvidencePacket
    resolve_triggers: true     # Mark old triggers as resolved after re-val

  # Webhook notifications
  webhooks:
    - url: ${MONITORING_WEBHOOK_URL}
      events: [drift_detected, revalidation_complete, revalidation_failed]
      headers:
        Authorization: "Bearer ${WEBHOOK_TOKEN}"
    - url: https://hooks.slack.com/services/T.../B.../xxx
      events: [drift_detected]
```

### §4.2 Project-level defaults

```yaml
# mrm_project.yml
monitoring:
  defaults:
    schedule: "daily"
    on_drift:
      revalidate: true
      freeze_evidence: true
    webhooks: []
```

## §5 MonitoringLog

Each `mrm monitor run` invocation appends one entry to an append-only
log at `evidence/monitoring/{model_name}/log.jsonl`:

```json
{
  "run_id": "uuid",
  "model_name": "ccr_monte_carlo",
  "timestamp": "2026-06-18T00:05:00Z",
  "metrics": [
    {
      "name": "data_drift",
      "source": "builtin",
      "detector": "ks",
      "drifted": false,
      "score": 0.032,
      "threshold": 0.05
    },
    {
      "name": "prediction_quality",
      "source": "mlflow",
      "drifted": true,
      "value": 0.18,
      "threshold": 0.15
    }
  ],
  "overall_drifted": true,
  "action_taken": "revalidated",
  "evidence_packet_id": "pkt-uuid-if-frozen",
  "triggers_fired": ["DRIFT-ccr_monte_carlo-20260618"],
  "webhooks_sent": ["https://hooks.slack.com/..."],
  "exit_code": 1,
  "created_by": "svc-mrm@bank.com",
  "hostname": "scheduler-prod-01",
  "invocation": "airflow",
  "config_hash": "b7af008bf2d1e897fe981d67f2fed9a3c7728226fc13a769f13176f7d427ba7d"
}
```

## §6 Metric Source Adapters

### §6.1 `builtin` — RiskAttest drift detectors

Uses `mrm.drift.get_detector()`. Reads reference and current datasets
from configured paths. Zero external dependencies beyond scipy/numpy.

### §6.2 `mlflow` — MLflow Tracking Server

Queries `mlflow.tracking.MlflowClient().get_metric_history()` for the
latest value of `metric_key` in the specified experiment. Requires
`mlflow` package (already a dependency).

### §6.3 `cloudwatch` — AWS CloudWatch Metrics

Queries `boto3.client("cloudwatch").get_metric_statistics()`. Requires
`boto3` (optional dependency). This is how SageMaker Model Monitor
results are consumed — the bank configures SageMaker Monitor
independently; RiskAttest reads the CloudWatch metrics it produces.

### §6.4 `file` — Local JSON/CSV

Reads a JSON or CSV file at `path` and extracts `metric_key`. For
banks that export monitoring metrics to shared filesystems or S3-
mounted paths.

### §6.5 `databricks` — Lakehouse Monitoring

Queries the `{table_name}_drift_metrics` Delta table that Databricks
Lakehouse Monitoring creates in Unity Catalog.  Uses the SQL Statement
Execution API (`POST /api/2.0/sql/statements`) — stdlib-only, no
`databricks-sdk` dependency.

```yaml
- name: lakehouse_drift
  source: databricks
  host: ${DATABRICKS_HOST}
  token: ${DATABRICKS_TOKEN}
  warehouse_id: ${SQL_WAREHOUSE_ID}
  table_name: risk_models.ccr.portfolio_data
  metric_column: ks_statistic        # Column from drift_metrics
  filter_column: notional            # Optional: check only this feature
  threshold: 0.05
  comparison: greater_than
```

Auth: `host` and `token` in YAML, or fallback to `DATABRICKS_HOST` /
`DATABRICKS_TOKEN` env vars. SSL-inspecting proxies are supported via
`SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` (see §6.7).

When `filter_column` is set, queries that specific column in the drift
table. When omitted, takes `MAX(metric_column)` across all columns in
the latest monitoring window — the worst-case drift signal.

### §6.6 Graceful degradation

Per-metric error isolation.  If one metric source fails (e.g.
CloudWatch is unreachable), the remaining metrics are still collected
and evaluated. The run only exits `2` if **all** metrics fail.  This
is critical for bank environments where one namespace being down
should not blind the monitoring system to drift detected by local or
other cloud sources.

### §6.7 Bank IT environment hardening

String values in the `monitoring:` YAML block support `${VAR_NAME}`
and `${VAR_NAME:-default}` expansion (matching standard shell
convention). This supplements the `{{ env_var('VAR') }}` syntax that
the project config loader already handles, and ensures secrets (API
tokens, webhook URLs) are never hardcoded in YAML.

**Proxy and TLS support:**

| Env var | Effect |
|---|---|
| `HTTPS_PROXY` / `HTTP_PROXY` | Webhook and Databricks API calls route through the proxy |
| `SSL_CERT_FILE` | Custom CA bundle (e.g. Zscaler root CA) used for HTTPS verification |
| `REQUESTS_CA_BUNDLE` | Same as `SSL_CERT_FILE` (Python requests convention, also honoured) |

SSL verification is **never disabled**. Banks deploying behind
SSL-inspecting proxies (Zscaler, Blue Coat, Palo Alto) inject their
root CA via these env vars — the same pattern used by `pip`, `git`,
`curl`, and the Python `requests` library.

### §6.8 Audit provenance

Every `MonitoringLogEntry` records:

| Field | Source | Purpose |
|---|---|---|
| `created_by` | `MRM_MONITOR_USER` env var, or `USER` | Who ran this cycle |
| `hostname` | `socket.gethostname()` | Which machine |
| `invocation` | `MRM_INVOCATION` env var (default `"manual"`) | How: `airflow` / `cron` / `databricks` / `ci` / `manual` |
| `config_hash` | SHA-256 of canonical JSON of monitoring config | Reproducibility — two runs with identical config produce the same hash |

This satisfies bank examiner requirements (SR 26-2 §II.AI.D, CPS 230
Para 35) for tracing exactly who ran monitoring, from where, using
which configuration, and whether the configuration changed between
runs.

## §7 CLI Commands

```bash
# One monitoring cycle for specified models
mrm monitor run --models ccr_monte_carlo

# All models with monitoring.enabled: true
mrm monitor run --all

# Dry run — check metrics, report drift, don't revalidate
mrm monitor run --models ccr_monte_carlo --dry-run

# Show monitoring history
mrm monitor history --model ccr_monte_carlo --last 10

# Show current monitoring status across all models
mrm monitor status
```

## §8 Exit Codes

| Code | Meaning |
|------|---------|
| 0    | No drift detected |
| 1    | Drift detected, re-validation triggered |
| 2    | Error (config, network, metric source unavailable) |

Exit codes are designed for scheduler integration: Airflow can branch
on exit code 1 to send alerts or trigger downstream DAGs.

## §9 Webhook Payload

```json
{
  "event": "drift_detected",
  "model_name": "ccr_monte_carlo",
  "timestamp": "2026-06-18T00:05:00Z",
  "run_id": "uuid",
  "metrics": [
    {"name": "prediction_quality", "drifted": true, "value": 0.18, "threshold": 0.15}
  ],
  "evidence_packet_id": "pkt-uuid",
  "riskattest_version": "0.1.0",
  "compliance_references": ["CPS 230 Para 35", "SR 26-2 §II.AI.D"]
}
```

## §10 Integration Patterns

### §10.1 Databricks Workflows

```python
# Databricks notebook cell
!mrm monitor run --models ccr_monte_carlo --profile prod
```

Or as a Databricks Workflow task of type "Shell" with the command above.
MLflow metrics are read via the workspace's built-in tracking server.

### §10.2 AWS Step Functions + SageMaker Monitor

1. SageMaker Monitor runs on schedule → writes to CloudWatch.
2. Step Function invokes `mrm monitor run` as an ECS/Lambda task.
3. RiskAttest reads CloudWatch metrics, evaluates thresholds.
4. If drift: re-validates, freezes evidence to S3 Object Lock.

### §10.3 Azure ML + Azure DevOps

1. Azure ML Monitor publishes metrics to Azure Monitor.
2. Azure DevOps pipeline runs `mrm monitor run` as a pipeline step.
3. RiskAttest reads from file export or Azure Monitor API.

### §10.4 Airflow

```python
monitor_task = BashOperator(
    task_id="mrm_monitor",
    bash_command="mrm monitor run --models ccr_monte_carlo --profile prod",
    env={
        "MRM_MONITOR_USER": "svc-mrm@bank.com",
        "MRM_INVOCATION": "airflow",
    },
    dag=dag,
)

# Branch on exit code: 0 = no drift, 1 = drift → alert
from airflow.operators.python import BranchPythonOperator

def _check_drift(**ctx):
    return "alert_task" if ctx["ti"].xcom_pull(task_ids="mrm_monitor")["return_code"] == 1 else "no_op"
```

### §10.5 Databricks Lakehouse Monitoring

For banks already using Databricks Lakehouse Monitoring on Unity
Catalog tables, `mrm monitor run` can read the `_drift_metrics` table
directly — no additional monitoring infrastructure required.

```yaml
# Model YAML
monitoring:
  enabled: true
  metrics:
    - name: lakehouse_drift
      source: databricks
      host: ${DATABRICKS_HOST}
      token: ${DATABRICKS_TOKEN}
      warehouse_id: ${SQL_WAREHOUSE_ID}
      table_name: risk_models.ccr.portfolio_data
      metric_column: ks_statistic
      filter_column: notional
      threshold: 0.05
      comparison: greater_than
  on_drift:
    freeze_evidence: true
```

```python
# Databricks notebook cell
import os
os.environ["MRM_INVOCATION"] = "databricks"
os.environ["MRM_MONITOR_USER"] = dbutils.notebook.entry_point.getDbutils() \
    .notebook().getContext().userName().get()

!mrm monitor run --models ccr_monte_carlo --profile prod
```

The Databricks adapter queries the SQL Statement Execution API
(`POST /api/2.0/sql/statements`), requires no `databricks-sdk`
dependency, and respects SSL-inspecting proxies via `SSL_CERT_FILE` /
`REQUESTS_CA_BUNDLE`.
