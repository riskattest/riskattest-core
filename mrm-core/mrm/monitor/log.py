"""Append-only monitoring log.

Each ``mrm monitor run`` invocation appends one JSONL entry to
``evidence/monitoring/{model_name}/log.jsonl``.

The log is append-only by design — entries are never modified or
deleted. This satisfies the regulatory requirement for immutable
audit trails (SR 26-2 §II.AI.D, CPS 230 Para 35).

See §5 of the continuous monitoring spec.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MonitoringLogEntry:
    """One monitoring cycle entry."""

    run_id: str
    model_name: str
    timestamp: str
    metrics: List[Dict[str, Any]]
    overall_drifted: bool
    action_taken: str  # "none" | "revalidated" | "error"
    exit_code: int
    evidence_packet_id: Optional[str] = None
    triggers_fired: List[str] = field(default_factory=list)
    webhooks_sent: List[str] = field(default_factory=list)
    error: Optional[str] = None

    # --- Audit provenance (bank examiners need to know who/where/how) ---
    created_by: Optional[str] = None       # email or service principal
    hostname: Optional[str] = None         # machine hostname
    invocation: Optional[str] = None       # "airflow" | "cron" | "databricks" | "manual" | "ci"
    config_hash: Optional[str] = None      # SHA-256 of monitoring config for reproducibility

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Remove None values for cleaner JSON
        return {k: v for k, v in d.items() if v is not None}


class MonitoringLog:
    """Append-only JSONL monitoring log for a model.

    Parameters
    ----------
    log_dir:
        Directory for this model's log file (e.g.
        ``evidence/monitoring/ccr_monte_carlo``). The file
        ``log.jsonl`` is created inside this directory.
    """

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = Path(log_dir)
        self.log_file = self.log_dir / "log.jsonl"

    def append(self, entry: MonitoringLogEntry) -> None:
        """Append one entry to the log. Creates the file if needed."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
        logger.debug("Appended log entry %s to %s", entry.run_id, self.log_file)

    def read_all(self) -> List[Dict[str, Any]]:
        """Read all log entries (chronological order)."""
        if not self.log_file.exists():
            return []
        entries = []
        with open(self.log_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def read_last(self, n: int) -> List[Dict[str, Any]]:
        """Read the last ``n`` entries."""
        all_entries = self.read_all()
        return all_entries[-n:] if len(all_entries) >= n else all_entries
