#!/usr/bin/env python3
"""Pure static JavaScript source-sink audit for reverse tasks.

Outputs JSON with ranked candidates and confidence levels:
- confirmed
- likely
- unknown
"""

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List

SINK_PATTERNS = {
    "network.fetch": r"\bfetch\s*\(",
    "network.xhr": r"\bXMLHttpRequest\b|\.open\s*\(",
    "network.axios": r"\baxios\s*\.\s*(get|post|put|delete|request)\s*\(",
}

CRYPTO_PATTERNS = {
    "crypto.cryptojs": r"\bCryptoJS\b",
    "crypto.subtle": r"crypto\.subtle\.(encrypt|decrypt|sign|digest|importKey)\s*\(",
    "crypto.hash": r"\b(md5|sha1|sha256|sha512|hmac|aes|rsa)\b",
    "codec.base64": r"\b(atob|btoa)\s*\(",
}

SOURCE_PATTERNS = {
    "source.time": r"\bDate\.now\s*\(|new\s+Date\s*\(",
    "source.random": r"\bMath\.random\s*\(|crypto\.getRandomValues\s*\(",
    "source.storage": r"\b(localStorage|sessionStorage)\b",
    "source.cookie": r"\bdocument\.cookie\b",
    "source.location": r"\blocation\.(href|search|hash|pathname)\b|window\.location\b",
}

KEYWORD_PATTERNS = {
    "kw.sign": r"\b(sign|signature|sig)\b",
    "kw.token": r"\b(token|nonce|timestamp|ts)\b",
    "kw.key": r"\b(secret|key|iv|salt)\b",
}


def line_no(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def find_hits(text: str, rules: Dict[str, str]) -> Dict[str, List[int]]:
    out: Dict[str, List[int]] = {}
    for name, pattern in rules.items():
        hits = [line_no(text, m.start()) for m in re.finditer(pattern, text, flags=re.IGNORECASE)]
        if hits:
            out[name] = hits[:30]
    return out


def calc_confidence(sinks: Dict[str, List[int]], cryptos: Dict[str, List[int]], sources: Dict[str, List[int]], kws: Dict[str, List[int]]) -> str:
    sink_n = sum(len(v) for v in sinks.values())
    crypto_n = sum(len(v) for v in cryptos.values())
    source_n = sum(len(v) for v in sources.values())
    kw_n = sum(len(v) for v in kws.values())

    if sink_n > 0 and crypto_n > 0 and kw_n > 0:
        return "confirmed"
    if (sink_n > 0 and crypto_n > 0) or (crypto_n > 0 and source_n > 0 and kw_n > 0):
        return "likely"
    return "unknown"


def score(sinks: Dict[str, List[int]], cryptos: Dict[str, List[int]], sources: Dict[str, List[int]], kws: Dict[str, List[int]]) -> int:
    sink_n = sum(len(v) for v in sinks.values())
    crypto_n = sum(len(v) for v in cryptos.values())
    source_n = sum(len(v) for v in sources.values())
    kw_n = sum(len(v) for v in kws.values())
    return sink_n * 4 + crypto_n * 4 + source_n * 2 + kw_n * 2


def audit_file(path: Path) -> Dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="ignore")

    sinks = find_hits(text, SINK_PATTERNS)
    cryptos = find_hits(text, CRYPTO_PATTERNS)
    sources = find_hits(text, SOURCE_PATTERNS)
    keywords = find_hits(text, KEYWORD_PATTERNS)

    conf = calc_confidence(sinks, cryptos, sources, keywords)
    sc = score(sinks, cryptos, sources, keywords)

    return {
        "file": str(path),
        "score": sc,
        "confidence": conf,
        "sinks": sinks,
        "crypto": cryptos,
        "sources": sources,
        "keywords": keywords,
    }


def collect_js_files(target: Path, recursive: bool) -> List[Path]:
    if target.is_file():
        return [target]
    pattern = "**/*.js" if recursive else "*.js"
    return sorted(p for p in target.glob(pattern) if p.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description="Static source-sink audit for JS reverse")
    parser.add_argument("target", help="Path to JS file or directory")
    parser.add_argument("--no-recursive", action="store_true", help="Only scan top-level directory")
    parser.add_argument("--out", help="Output JSON file path")
    args = parser.parse_args()

    target = Path(args.target).expanduser().resolve()
    if not target.exists():
        print(json.dumps({"error": f"target not found: {target}"}, ensure_ascii=False))
        return 1

    files = collect_js_files(target, recursive=not args.no_recursive)
    results = [audit_file(p) for p in files]
    results.sort(key=lambda x: x["score"], reverse=True)

    summary = {
        "target": str(target),
        "files_scanned": len(files),
        "top_confidence": results[0]["confidence"] if results else "unknown",
        "confirmed_count": sum(1 for r in results if r["confidence"] == "confirmed"),
        "likely_count": sum(1 for r in results if r["confidence"] == "likely"),
        "unknown_count": sum(1 for r in results if r["confidence"] == "unknown"),
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
