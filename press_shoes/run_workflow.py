#!/usr/bin/env python3
"""Manual test harness for PressShoesWorkflow.

This script instantiates the workflow with the fake robot arms created in utils.py
and starts the workflow loop inside a background thread. Press Ctrl+C to stop.
"""

from __future__ import annotations

import sys
import time
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from workflows.press_shoes.press_shoes_workflow import PressShoesWorkflow  # noqa: E402


def main() -> None:
    workflow = PressShoesWorkflow()
    worker = threading.Thread(target=workflow.run, name="PressShoesWorkflow", daemon=True)
    worker.start()

    print("PressShoesWorkflow is running. Press Ctrl+C to stop the test.")
    try:
        while worker.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping test (workflow threads will exit on next safe point).")


if __name__ == "__main__":
    main()
