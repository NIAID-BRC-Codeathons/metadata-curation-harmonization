#!/usr/bin/env python3
"""
Launcher for the pipeline orchestrator.

The orchestrator package lives under src/, which is not on the default path, so
this puts it there before handing over. Equivalent to:

    PYTHONPATH=src python -m orchestrator [options]

Usage:
    python scripts/run_pipeline.py --dry-run
    python scripts/run_pipeline.py --only retrieve

Author: Andrew LaPointe
Date: 2026-09-17
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from orchestrator.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
