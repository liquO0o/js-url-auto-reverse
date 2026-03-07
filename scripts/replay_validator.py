#!/usr/bin/env python3
"""Replay validator for recovered sign/encrypt algorithm.

Adapter contract:
- Python file path given by --adapter
- must expose function: generate(payload: dict) -> str

Vectors file format (JSON array):
[
  {"payload": {...}, "expected": "..."},
  ...
]
"""

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List


def load_adapter(adapter_path: Path):
    spec = importlib.util.spec_from_file_location("reverse_adapter", str(adapter_path))
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load adapter module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "generate"):
        raise RuntimeError("adapter must export generate(payload) function")
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate replayed sign/encrypt algorithm")
    parser.add_argument("--adapter", required=True, help="Path to adapter python file")
    parser.add_argument("--vectors", required=True, help="Path to vectors JSON")
    parser.add_argument("--out", help="Output JSON path")
    args = parser.parse_args()

    adapter_path = Path(args.adapter).expanduser().resolve()
    vectors_path = Path(args.vectors).expanduser().resolve()
    if not adapter_path.exists() or not vectors_path.exists():
        print(json.dumps({"error": "adapter or vectors not found"}, ensure_ascii=False))
        return 1

    try:
        module = load_adapter(adapter_path)
    except Exception as e:
        print(json.dumps({"error": f"load adapter failed: {e}"}, ensure_ascii=False))
        return 1

    try:
        vectors: List[Dict[str, Any]] = json.loads(vectors_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"error": f"load vectors failed: {e}"}, ensure_ascii=False))
        return 1

    results = []
    passed = 0

    for i, item in enumerate(vectors, start=1):
        payload = item.get("payload", {})
        expected = item.get("expected")
        try:
            got = module.generate(payload)
            ok = got == expected
            if ok:
                passed += 1
            results.append({
                "index": i,
                "ok": ok,
                "expected": expected,
                "got": got,
            })
        except Exception as e:
            results.append({
                "index": i,
                "ok": False,
                "error": str(e),
            })

    summary = {
        "adapter": str(adapter_path),
        "vectors": str(vectors_path),
        "total": len(vectors),
        "passed": passed,
        "pass_rate": (passed / len(vectors)) if vectors else 0.0,
        "results": results,
    }

    if args.out:
        out = Path(args.out).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
