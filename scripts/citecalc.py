#!/usr/bin/env python3
"""Run from the source checkout or installed plugin without installing a console command."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from citations_calculations.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
