"""pytest configuration: ensure impl module is importable from any cwd."""

import sys
from pathlib import Path

# phase3/tests/ -> phase3/ -> projects/hermem
sys.path.insert(0, str(Path(__file__).parent.parent))
