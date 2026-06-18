"""Continuous model monitoring with drift-triggered re-validation.

Public surface:

  * ``MonitorRunner``     -- orchestrates one monitoring cycle.
  * ``MonitorRunResult``  -- structured output from a cycle.
  * ``MonitoringConfig``  -- parsed monitoring YAML block.
  * ``MonitoringLog``     -- append-only JSONL log.

See ``docs/spec/continuous-monitoring-v1.md`` for the full specification.
"""

from mrm.monitor.config import MonitoringConfig, parse_monitoring_config
from mrm.monitor.log import MonitoringLog
from mrm.monitor.runner import MonitorRunner, MonitorRunResult

__all__ = [
    "MonitoringConfig",
    "MonitoringLog",
    "MonitorRunner",
    "MonitorRunResult",
    "parse_monitoring_config",
]
