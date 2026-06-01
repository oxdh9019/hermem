"""pytest configuration: ensure V5.5 tests can find their dependencies."""

import sys
from pathlib import Path

_PHASE3 = Path(__file__).parent.parent.parent
_IMPL_V55 = Path(__file__).parent.parent / "impl"

if str(_PHASE3) not in sys.path:
    sys.path.insert(0, str(_PHASE3))
if str(_IMPL_V55) not in sys.path:
    sys.path.insert(0, str(_IMPL_V55))
