"""Run the tests against the engine wheel the page ships, not whichever engine is installed."""
import sys
from pathlib import Path

sys.path.insert(0, str(next(Path(__file__).parent.glob("vendor/pseudonimizzatore_legale-*.whl"))))
