#!/usr/bin/env python3
"""Basic JS deobfuscation pass (safe regex-based).

This is intentionally conservative and dependency-free.
"""

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List

TOKEN_MAP = {
    "!![]": "true",
    "![]": "false",
    "!0": "true",
    "!1": "false",
}


def collect_js_files(target: Path) -> List[Path]:
    if target.is_file():
        return [target] if target.suffix.lower() == ".js" else []
    return sorted(p for p in target.glob("**/*.js") if p.is_file())


def deobfuscate_text(text: str) -> Dict[str, object]:
    report = {
        "removed_debugger": 0,
        "simplified_tokens": 0,
        "member_dot_normalized": 0,
    }

    out = text

    out = re.sub(r"\bdebugger\s*;?", lambda _: _inc(report, "removed_debugger", ""), out)

    for old, new in TOKEN_MAP.items():
        esc = re.escape(old)
        out = re.sub(esc, lambda _: _inc(report, "simplified_tokens", new), out)

    # obj['name'] -> obj.name (identifier only)
    out = re.sub(
        r"\b([A-Za-z_$][\w$]*)\s*\[\s*['\"]([A-Za-z_$][\w$]*)['\"]\s*\]",
        lambda m: _inc(report, "member_dot_normalized", f"{m.group(1)}.{m.group(2)}"),
        out,
    )

    return {"code": out, "report": report}


def _inc(rep: Dict[str, int], key: str, replacement: str) -> str:
    rep[key] += 1
    return replacement


def main() -> int:
    parser = argparse.ArgumentParser(description="Basic deobfuscation for JS files")
    parser.add_argument("target", help="JS file or directory")
    parser.add_argument("--out-dir", required=True, help="Output directory for transformed JS")
    parser.add_argument("--report", help="Output JSON report path")
    args = parser.parse_args()

    target = Path(args.target).expanduser().resolve()
    if not target.exists():
        print(json.dumps({"error": f"target not found: {target}"}, ensure_ascii=False))
        return 1

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    files = collect_js_files(target)
    items = []

    for src in files:
        rel = src.name if target.is_file() else src.relative_to(target)
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        text = src.read_text(encoding="utf-8", errors="ignore")
        data = deobfuscate_text(text)
        dst.write_text(data["code"], encoding="utf-8")

        items.append({
            "src": str(src),
            "dst": str(dst),
            "report": data["report"],
        })

    summary = {
        "target": str(target),
        "out_dir": str(out_dir),
        "files": len(items),
        "items": items,
    }

    if args.report:
        rp = Path(args.report).expanduser().resolve()
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
