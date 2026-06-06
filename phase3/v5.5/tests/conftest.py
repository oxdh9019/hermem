"""pytest configuration: ensure V5.5 tests can find their dependencies.

V5.5 modules live in `phase3/v5.5/impl/` (a separate package from
`phase3/impl/`). The V5.5 unit tests do `from impl.l4_reflection import ...`
— matching the convention of V4/V5 modules that live directly in
`phase3/impl/`. To bridge this without touching production code, we
inject the V5.5 modules into the `impl` package namespace at test time
via `sys.modules`. Production code uses the explicit
`v5_5_llm_helper` / `v5_5_l4_reflection` importlib bypass (see
`__init__.py:_v55_import`), so this shim is test-only and has no runtime
effect.
"""

import sys
import importlib
from pathlib import Path

_PHASE3 = Path(__file__).parent.parent.parent
_IMPL_V55 = Path(__file__).parent.parent / "impl"

if str(_PHASE3) not in sys.path:
    sys.path.insert(0, str(_PHASE3))
if str(_IMPL_V55) not in sys.path:
    sys.path.insert(0, str(_IMPL_V55))

# Inject V5.5 modules into the `impl` package namespace so tests can do
# `from impl.l4_reflection import ...` just like V4/V5 modules.
import impl  # noqa: E402

for _mod_name in (
    "l4_reflection",
    "conflict_resolver",
    "active_forgetting",
    "llm_helper",
):
    _full_name = f"impl.{_mod_name}"
    if _full_name not in sys.modules:
        _mod = importlib.import_module(_mod_name)
        sys.modules[_full_name] = _mod
        setattr(impl, _mod_name, _mod)
