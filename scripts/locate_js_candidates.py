#!/usr/bin/env python3
"""Locate high-value JS candidates for reverse analysis.

Keyword and structure based ranking for fast prioritization.
"""

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List

RULES = {
    "network": [
        ("fetch", r"\bfetch\s*\("),
        ("xhr", r"\bXMLHttpRequest\b|\.open\s*\("),
        ("axios", r"\baxios\s*\.\s*(get|post|put|delete|request)\s*\("),
    ],
    "crypto": [
        ("cryptojs", r"\bCryptoJS\b"),
        ("subtle", r"crypto\.subtle\.(encrypt|decrypt|sign|digest|importKey)\s*\("),
        ("hash", r"\b(md5|sha1|sha256|sha512|hmac|aes|rsa)\b"),
        ("codec", r"\b(atob|btoa|encodeURIComponent|decodeURIComponent)\s*\("),
    ],
    "reverse_keywords": [
        ("sign", r"\b(sign|signature|sig)\b"),
        ("token", r"\b(token|nonce|timestamp|ts)\b"),
        ("secret", r"\b(secret|key|iv|salt)\b"),
    ],
    "obfuscation": [
        ("hex_var", r"\b_0x[a-fA-F0-9]{3,}\b"),
        ("eval_like", r"\beval\s*\(|new\s+Function\s*\("),
        ("debugger", r"\bdebugger\b"),
    ],
}

WEIGHTS = {
    "network": 5,
    "crypto": 5,
    "reverse_keywords": 3,
    "obfuscation": 2,
}


def line_no(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def collect_js_files(target: Path, recursive: bool) -> List[Path]:
    if target.is_file():
        return [target]
    pattern = "**/*.js" if recursive else "*.js"
    return sorted(p for p in target.glob(pattern) if p.is_file())


def scan_file(path: Path) -> Dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="ignore")

    detail: Dict[str, Dict[str, List[int]]] = {}
    score = 0

    for category, rules in RULES.items():
        cat_detail: Dict[str, List[int]] = {}
        for name, pattern in rules:
            lines = [line_no(text, m.start()) for m in re.finditer(pattern, text, flags=re.IGNORECASE)]
            if lines:
                cat_detail[name] = lines[:30]
                score += len(lines) * WEIGHTS[category]
        if cat_detail:
            detail[category] = cat_detail

    has_network = "network" in detail
    has_crypto = "crypto" in detail
    has_keywords = "reverse_keywords" in detail

    if has_network and has_crypto:
        score += 25
    if has_network and has_keywords:
        score += 10

    level = "low"
    if score >= 50:
        level = "high"
    elif score >= 20:
        level = "medium"

    return {
        "file": str(path),
        "score": score,
        "priority": level,
        "signals": detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Locate high-value JS candidates")
    parser.add_argument("target", help="JS file or directory")
    parser.add_argument("--no-recursive", action="store_true", help="Only scan top-level directory")
    parser.add_argument("--top", type=int, default=20, help="Number of top candidates to return")
    parser.add_argument("--out", help="Output JSON path")
    args = parser.parse_args()

    target = Path(args.target).expanduser().resolve()
    if not target.exists():
        print(json.dumps({"error": f"target not found: {target}"}, ensure_ascii=False))
        return 1

    files = collect_js_files(target, recursive=not args.no_recursive)
    scanned = [scan_file(p) for p in files]
    scanned.sort(key=lambda x: x["score"], reverse=True)

    top_items = scanned[: max(args.top, 1)]
    summary = {
        "target": str(target),
        "files_scanned": len(files),
        "top": len(top_items),
        "high_count": sum(1 for x in scanned if x["priority"] == "high"),
        "medium_count": sum(1 for x in scanned if x["priority"] == "medium"),
        "low_count": sum(1 for x in scanned if x["priority"] == "low"),
        "results": top_items,
    }

    if args.out:
        out = Path(args.out).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
