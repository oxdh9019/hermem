#!/usr/bin/env python3
"""
bridge_smoke.py — Hermem bridge health check.

Static (AST) inspection of the bridge at
    ~/.hermes/hermes-agent/plugins/memory/hermem/__init__.py

Runs in <100 ms and does NOT:
  - import the bridge (avoids triggering DB / Ollama / LLM init)
  - touch ~/.hermes/  (safe to run before/after upgrades)
  - need hermes-agent on sys.path

What it verifies:
  1. The bridge file exists and parses cleanly.
  2. `HermemMemoryProvider` class is defined.
  3. All 5 expected tool schemas are defined and well-formed.
  4. Path constants (_IMPL_PATH, _V55_IMPL_PATH, _L0L3_DB, _HERMEM_HOME) exist.
  5. Critical methods are defined: get_tool_schemas, handle_tool_call,
     system_prompt_block, _v55_import, _v55_resolve_conflict, _ensure_impl.
  6. AGENTS.md companion doc is present.

Exit code 0 = healthy. Non-zero = upgrade likely broke the bridge.

Usage:
    python3 phase3/scripts/bridge_smoke.py
    python3 phase3/scripts/bridge_smoke.py --bridge /path/to/other/bridge
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

DEFAULT_BRIDGE = Path.home() / ".hermes" / "hermes-agent" / "plugins" / "memory" / "hermem"

EXPECTED_SCHEMAS = [
    "HERMEM_SEARCH_SCHEMA",
    "HERMEM_ADD_SCHEMA",
    "HERMEM_FORGET_SCHEMA",
    "HERMEM_STATS_SCHEMA",
    "HERMEM_RESOLVE_CONFLICT_SCHEMA",
]
EXPECTED_PATH_CONSTANTS = ["_IMPL_PATH", "_V55_IMPL_PATH", "_L0L3_DB", "_HERMEM_HOME"]
EXPECTED_METHODS = [
    ("HermemMemoryProvider", "name"),
    ("HermemMemoryProvider", "is_available"),
    ("HermemMemoryProvider", "initialize"),
    ("HermemMemoryProvider", "get_tool_schemas"),
    ("HermemMemoryProvider", "system_prompt_block"),
    ("HermemMemoryProvider", "prefetch"),
    ("HermemMemoryProvider", "queue_prefetch"),
    ("HermemMemoryProvider", "sync_turn"),
    ("HermemMemoryProvider", "handle_tool_call"),
    ("HermemMemoryProvider", "shutdown"),
    ("HermemMemoryProvider", "_v55_import"),
    ("HermemMemoryProvider", "_v55_async_detect_conflict"),
    ("HermemMemoryProvider", "_v55_resolve_conflict"),
    ("HermemMemoryProvider", "_v55_get_l4_system_prompt"),
    ("HermemMemoryProvider", "_ensure_impl"),
]


def _collect_module_names(tree: ast.AST) -> set[str]:
    """Top-level function/class/constant names defined at module level."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name):
                names.add(tgt.id)
    return names


def _collect_module_functions(tree: ast.AST) -> set[str]:
    """Names of module-level (non-class) functions."""
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(node.name)
    return out


def _collect_class_methods(tree: ast.AST) -> dict[str, set[str]]:
    """Mapping of class name -> set of method names defined on it."""
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            out[node.name] = methods
    return out


def _validate_schemas(tree: ast.AST) -> tuple[bool, list[str]]:
    """Check each EXPECTED_SCHEMAS constant is a JSON-serializable dict with name+parameters."""
    errors: list[str] = []
    found: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name) and tgt.id in EXPECTED_SCHEMAS:
                found[tgt.id] = node.value

    for schema_name in EXPECTED_SCHEMAS:
        if schema_name not in found:
            errors.append(f"  - missing {schema_name}")
            continue
        try:
            value = ast.literal_eval(found[schema_name])
        except Exception as e:
            errors.append(f"  - {schema_name} is not a literal: {e}")
            continue
        if not isinstance(value, dict):
            errors.append(f"  - {schema_name} is not a dict")
            continue
        if "name" not in value or "parameters" not in value:
            errors.append(f"  - {schema_name} missing 'name' or 'parameters' key")
            continue
        json.dumps(value)  # ensure JSON-serializable

    return (len(errors) == 0, errors)


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermem bridge health check.")
    parser.add_argument(
        "--bridge",
        type=Path,
        default=DEFAULT_BRIDGE,
        help=f"Path to hermem bridge dir (default: {DEFAULT_BRIDGE})",
    )
    args = parser.parse_args()

    bridge_dir: Path = args.bridge
    init_path = bridge_dir / "__init__.py"
    agents_path = bridge_dir / "AGENTS.md"

    print(f"Hermem bridge smoke test")
    print(f"  bridge dir: {bridge_dir}")
    print()

    failures: list[str] = []

    # ── 1. Files present ─────────────────────────────────────────────────────
    if not init_path.is_file():
        print(f"  [FAIL] {init_path} not found")
        return 2
    print(f"  [OK]   __init__.py present ({init_path.stat().st_size:,} bytes)")

    if not agents_path.is_file():
        print(f"  [WARN] AGENTS.md not found at {agents_path} (non-fatal)")
    else:
        print(f"  [OK]   AGENTS.md present")

    # ── 2. Parse cleanly ────────────────────────────────────────────────────
    try:
        source = init_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        print(f"  [OK]   __init__.py parses cleanly ({len(source.splitlines())} lines)")
    except SyntaxError as e:
        print(f"  [FAIL] __init__.py has SyntaxError: {e}")
        return 3

    # ── 3. Tool schemas ─────────────────────────────────────────────────────
    ok, errors = _validate_schemas(tree)
    if ok:
        print(f"  [OK]   all {len(EXPECTED_SCHEMAS)} tool schemas present and well-formed")
    else:
        failures.extend(errors)
        print(f"  [FAIL] tool schema issues:")
        for err in errors:
            print(err)

    # ── 4. Path constants ───────────────────────────────────────────────────
    module_names = _collect_module_names(tree)
    missing_consts = [c for c in EXPECTED_PATH_CONSTANTS if c not in module_names]
    if missing_consts:
        failures.append(f"missing path constants: {missing_consts}")
        print(f"  [FAIL] missing path constants: {missing_consts}")
    else:
        print(f"  [OK]   all {len(EXPECTED_PATH_CONSTANTS)} path constants defined")

    # ── 5. Class + methods (mixed: class methods + module-level functions) ──
    classes = _collect_class_methods(tree)
    module_fns = _collect_module_functions(tree)
    if "HermemMemoryProvider" not in classes:
        failures.append("HermemMemoryProvider class not found")
        print("  [FAIL] HermemMemoryProvider class not found")
    else:
        class_methods = classes["HermemMemoryProvider"]
        missing_methods: list[str] = []
        for cls, meth in EXPECTED_METHODS:
            if cls == "HermemMemoryProvider":
                if meth not in class_methods and meth not in module_fns:
                    missing_methods.append(f"{cls}.{meth}")
        if missing_methods:
            failures.extend(f"missing method: {m}" for m in missing_methods)
            print(f"  [FAIL] missing {len(missing_methods)} methods:")
            for m in missing_methods:
                print(f"    - {m}")
        else:
            print(f"  [OK]   all {len(EXPECTED_METHODS)} expected methods present (class + module-level)")

    # ── 6. AGENTS.md sanity (if present) ────────────────────────────────────
    if agents_path.is_file():
        agents_text = agents_path.read_text(encoding="utf-8")
        if "Hermem bridge" in agents_text and "MemoryProvider" in agents_text:
            print(f"  [OK]   AGENTS.md contains expected key concepts")
        else:
            print(f"  [WARN] AGENTS.md missing expected key concepts (non-fatal)")

    # ── verdict ─────────────────────────────────────────────────────────────
    print()
    if failures:
        print(f"FAILED ({len(failures)} issue(s))")
        print("The bridge appears to have been broken — likely by a hermes-agent upgrade.")
        print("To roll back: bash phase3/scripts/backup_bridge.sh --list")
        return 1

    print("ALL GREEN — bridge looks healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
