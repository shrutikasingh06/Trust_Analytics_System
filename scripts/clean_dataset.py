"""
Run both cleaning tracks. Original files in dataset/csv_files/ are never modified.

Usage:
  python scripts/clean_dataset.py
  python scripts/clean_dataset.py --track 1
  python scripts/clean_dataset.py --track 2
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def run(script: str) -> None:
    cmd = [sys.executable, str(SCRIPTS / script)]
    print("RUN", " ".join(cmd), flush=True)
    subprocess.check_call(cmd)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--track", choices=["1", "2", "both"], default="both")
    args = p.parse_args()
    if args.track in {"1", "both"}:
        run("clean_behavior_eda.py")
    if args.track in {"2", "both"}:
        run("clean_candidate_label.py")


if __name__ == "__main__":
    main()
