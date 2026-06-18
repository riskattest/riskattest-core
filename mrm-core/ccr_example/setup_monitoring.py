"""Generate reference and current monitoring datasets for ccr_monte_carlo.

Extracts numeric columns from training.csv (reference) and
validation.csv (current) into simple single-column CSV files that
the builtin drift detectors can consume.

Also writes a monitoring metrics JSON with simulated model
performance metrics (e.g. RMSE from the latest model evaluation).

Run this once before ``mrm monitor run``:

    python setup_monitoring.py
"""

import csv
import json
from pathlib import Path


def main():
    data_dir = Path(__file__).parent / "data"
    monitoring_dir = data_dir / "monitoring"
    monitoring_dir.mkdir(exist_ok=True)

    # Columns to monitor for drift
    columns = ["notional", "pd_annual", "lgd"]

    # --- Extract reference from training.csv ---
    ref_path = monitoring_dir / "reference_portfolio.csv"
    _extract_columns(data_dir / "training.csv", ref_path, columns)

    # --- Extract current from validation.csv ---
    cur_path = monitoring_dir / "current_portfolio.csv"
    _extract_columns(data_dir / "validation.csv", cur_path, columns)

    print(f"  Reference dataset: {ref_path} ({_count_rows(ref_path)} rows)")
    print(f"  Current dataset:   {cur_path} ({_count_rows(cur_path)} rows)")

    # --- Write monitoring metrics JSON ---
    metrics_path = monitoring_dir / "latest_metrics.json"
    metrics = {
        "rmse": 0.042,
        "mae": 0.031,
        "pfe_breach_rate": 0.08,
        "mc_convergence_ratio": 0.98,
    }
    metrics_path.write_text(json.dumps(metrics, indent=2))
    print(f"  Monitoring metrics: {metrics_path}")

    print("\nMonitoring data ready. Run:")
    print("  mrm monitor run --models ccr_monte_carlo --dry-run")


def _extract_columns(src: Path, dst: Path, columns: list):
    with open(src) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    with open(dst, "w") as f:
        f.write(",".join(columns) + "\n")
        for row in rows:
            vals = [row[c] for c in columns]
            f.write(",".join(vals) + "\n")


def _count_rows(path: Path) -> int:
    with open(path) as f:
        return sum(1 for _ in f) - 1  # subtract header


if __name__ == "__main__":
    main()
