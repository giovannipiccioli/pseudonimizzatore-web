"""Test the same Python wheels the page ships."""
import sys
from pathlib import Path

vendor = Path(__file__).parent / "vendor"
for package in ("pseudonimizzatore_legale", "pypdf"):
    wheel, = vendor.glob(f"{package}-*.whl")
    sys.path.insert(0, str(wheel))
