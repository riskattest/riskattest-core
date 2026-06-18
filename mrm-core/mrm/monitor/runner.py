"""Monitor runner — orchestrates one monitoring cycle.

``MonitorRunner.run()`` is the core of ``mrm monitor run``. It:

  1. Parses the monitoring config from model YAML.
  2. Collects metrics from configured sources.
  3. Evaluates thresholds.
  4. If drift detected and configured: triggers revalidation,
     freezes evidence, fires triggers, sends webhooks.
  5. Writes a MonitoringLog entry.
  6. Returns a structured result with exit code.

Exit codes:
  0 = no drift detected
  1 = drift detected, re-validation triggered
  2 = error (config, network, metric source unavailable)

See §3 of the continuous monitoring spec.
"""

from __future__ import annotations

import hashlib
import json as _json
import logging
import os
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from mrm.core.triggers import TriggerEvent, TriggerType
from mrm.evidence.packet import EvidencePacket
from mrm.monitor.config import MonitoringConfig, parse_monitoring_config
from mrm.monitor.log import MonitoringLog, MonitoringLogEntry
from mrm.monitor.metrics import MetricResult, get_metric_source
from mrm.monitor.webhook import build_webhook_payload, send_webhook

logger = logging.getLogger(__name__)


def _compute_config_hash(monitoring_raw: Dict[str, Any]) -> str:
    """Return the SHA-256 hex digest of the canonical JSON of the monitoring config.

    Canonical = sorted keys, no whitespace, deterministic across runs.
    This lets auditors verify that two monitoring runs used identical
    configuration.
    """
    canonical = _json.dumps(monitoring_raw, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class MonitorRunResult:
    """Result of one monitoring cycle."""

    run_id: str
    model_name: str
    overall_drifted: bool
    metric_results: List[MetricResult]
    exit_code: int  # 0 | 1 | 2
    error: Optional[str] = None
    skipped: bool = False
    evidence_packet_id: Optional[str] = None
    triggers_fired: List[str] = field(default_factory=list)
    webhooks_sent: List[str] = field(default_factory=list)


class MonitorRunner:
    """Orchestrates one monitoring cycle for a model.

    Parameters
    ----------
    log_dir:
        Base directory for monitoring logs. Each model gets a
        subdirectory. Defaults to ``evidence/monitoring/``.
    """

    def __init__(self, log_dir: Optional[Any] = None) -> None:
        from pathlib import Path

        self.log_dir = Path(log_dir) if log_dir else Path("evidence/monitoring")

    def run(self, model_config: Dict[str, Any]) -> MonitorRunResult:
        """Run one monitoring cycle for a model.

        Parameters
        ----------
        model_config:
            Full model YAML config dict (must include ``model:``
            and optionally ``monitoring:`` sections).

        Returns
        -------
        MonitorRunResult
            Structured result with exit code.
        """
        run_id = str(uuid.uuid4())
        model_section = model_config.get("model", {})
        model_name = model_section.get("name", "unknown")

        # --- Check monitoring config ---
        monitoring_raw = model_config.get("monitoring")
        if not monitoring_raw:
            return MonitorRunResult(
                run_id=run_id,
                model_name=model_name,
                overall_drifted=False,
                metric_results=[],
                exit_code=0,
                skipped=True,
            )

        try:
            config = parse_monitoring_config(monitoring_raw)
        except Exception as exc:
            logger.error("Failed to parse monitoring config: %s", exc)
            return MonitorRunResult(
                run_id=run_id,
                model_name=model_name,
                overall_drifted=False,
                metric_results=[],
                exit_code=2,
                error=str(exc),
            )

        if not config.enabled:
            return MonitorRunResult(
                run_id=run_id,
                model_name=model_name,
                overall_drifted=False,
                metric_results=[],
                exit_code=0,
                skipped=True,
            )

        # --- Collect metrics (graceful degradation) ---
        #
        # Per-metric errors are logged but do not abort the run.  If
        # *all* metrics fail the run exits 2; if at least one succeeds
        # the surviving results are evaluated normally.  This matches
        # bank ops reality: one CloudWatch namespace being down should
        # not blind the monitoring system to drift in locally-computed
        # metrics.
        metric_results: List[MetricResult] = []
        metric_errors: List[str] = []
        for metric_cfg in config.metrics:
            try:
                source = get_metric_source(metric_cfg.source)
                result = source.collect(metric_cfg)
                metric_results.append(result)
            except Exception as exc:
                err_msg = f"{metric_cfg.name} ({metric_cfg.source}): {exc}"
                logger.warning(
                    "Metric collection failed for %s: %s",
                    metric_cfg.name, exc,
                )
                metric_errors.append(err_msg)

        if not metric_results and metric_errors:
            # Every single metric failed — this is exit 2.
            combined = "; ".join(metric_errors)
            logger.error(
                "All metrics failed for %s: %s", model_name, combined,
            )
            self._write_log(
                run_id=run_id,
                model_name=model_name,
                metrics=metric_results,
                overall_drifted=False,
                action_taken="error",
                exit_code=2,
                error=combined,
                monitoring_raw=monitoring_raw,
            )
            return MonitorRunResult(
                run_id=run_id,
                model_name=model_name,
                overall_drifted=False,
                metric_results=metric_results,
                exit_code=2,
                error=combined,
            )

        # --- Evaluate drift ---
        overall_drifted = any(m.drifted for m in metric_results)

        action_taken = "none"
        evidence_packet_id: Optional[str] = None
        triggers_fired: List[str] = []
        webhooks_sent: List[str] = []

        if overall_drifted:
            logger.info("Drift detected for model '%s'", model_name)
            action_taken = "drift_detected"

            # --- Freeze evidence ---
            if config.on_drift.freeze_evidence:
                model_version = model_section.get("version", "unknown")
                packet = EvidencePacket(
                    packet_id=str(uuid.uuid4()),
                    model_name=model_name,
                    model_version=model_version,
                    model_artifact_hash="monitoring-cycle",
                    test_results={
                        "drift_metrics": [m.to_dict() for m in metric_results],
                    },
                    compliance_mappings={
                        "monitoring": [
                            "SR 26-2 §II.AI.D",
                            "CPS 230 Para 35",
                        ],
                    },
                    timestamp=datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    created_by="mrm-monitor",
                    metadata={
                        "trigger": "drift_detected",
                        "run_id": run_id,
                    },
                )
                evidence_dir = self.log_dir / model_name / "evidence"
                evidence_dir.mkdir(parents=True, exist_ok=True)
                evidence_path = evidence_dir / f"{packet.packet_id}.json"
                evidence_path.write_text(packet.to_json())
                evidence_packet_id = packet.packet_id
                logger.info(
                    "Evidence packet %s written to %s",
                    packet.packet_id,
                    evidence_path,
                )

            # --- Fire drift trigger ---
            now_utc = datetime.now(timezone.utc)
            trigger_event = TriggerEvent(
                trigger_id=(
                    f"DRIFT-{model_name}-{now_utc.strftime('%Y%m%d%H%M')}"
                ),
                trigger_type=TriggerType.DRIFT,
                model_name=model_name,
                fired_at=now_utc.isoformat(),
                reason=(
                    "Monitoring drift detected: "
                    + ", ".join(
                        m.name for m in metric_results if m.drifted
                    )
                ),
                evidence={
                    "drifted_metrics": [
                        m.name for m in metric_results if m.drifted
                    ],
                    "run_id": run_id,
                },
                compliance_reference=(
                    "CPS 230 Para 35: Material change detection"
                ),
            )
            triggers_fired.append(trigger_event.trigger_id)
            logger.info(
                "Trigger %s fired for model '%s'",
                trigger_event.trigger_id,
                model_name,
            )

            # Send webhooks
            for wh_cfg in config.webhooks:
                payload = build_webhook_payload(
                    event="drift_detected",
                    model_name=model_name,
                    run_id=run_id,
                    metric_results=[m for m in metric_results if m.drifted],
                )
                sent = send_webhook(wh_cfg, payload)
                if sent:
                    webhooks_sent.append(wh_cfg.url)

        # --- Write log ---
        exit_code = 1 if overall_drifted else 0
        self._write_log(
            run_id=run_id,
            model_name=model_name,
            metrics=metric_results,
            overall_drifted=overall_drifted,
            action_taken=action_taken,
            exit_code=exit_code,
            evidence_packet_id=evidence_packet_id,
            triggers_fired=triggers_fired,
            webhooks_sent=webhooks_sent,
            monitoring_raw=monitoring_raw,
        )

        return MonitorRunResult(
            run_id=run_id,
            model_name=model_name,
            overall_drifted=overall_drifted,
            metric_results=metric_results,
            exit_code=exit_code,
            evidence_packet_id=evidence_packet_id,
            triggers_fired=triggers_fired,
            webhooks_sent=webhooks_sent,
        )

    def _write_log(
        self,
        run_id: str,
        model_name: str,
        metrics: List[MetricResult],
        overall_drifted: bool,
        action_taken: str,
        exit_code: int,
        evidence_packet_id: Optional[str] = None,
        triggers_fired: Optional[List[str]] = None,
        webhooks_sent: Optional[List[str]] = None,
        error: Optional[str] = None,
        monitoring_raw: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write an entry to the monitoring log with audit provenance."""
        log = MonitoringLog(self.log_dir / model_name)
        entry = MonitoringLogEntry(
            run_id=run_id,
            model_name=model_name,
            timestamp=datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            metrics=[m.to_dict() for m in metrics],
            overall_drifted=overall_drifted,
            action_taken=action_taken,
            exit_code=exit_code,
            evidence_packet_id=evidence_packet_id,
            triggers_fired=triggers_fired or [],
            webhooks_sent=webhooks_sent or [],
            error=error,
            # --- Audit provenance ---
            created_by=os.environ.get(
                "MRM_MONITOR_USER", os.environ.get("USER", "unknown")
            ),
            hostname=socket.gethostname(),
            invocation=os.environ.get("MRM_INVOCATION", "manual"),
            config_hash=(
                _compute_config_hash(monitoring_raw)
                if monitoring_raw
                else None
            ),
        )
        log.append(entry)
