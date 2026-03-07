#!/usr/bin/env python3
"""Advanced static reverse analysis for JS/WASM targets.

Covers:
- anti-debug signals
- crypto primitive recognition
- fingerprint signal tracing
- wasm binary clue extraction
"""

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List

ANTI_DEBUG_RULES = {
    "debugger": r"\bdebugger\b",
    "function_constructor": r"new\s+Function\s*\(",
    "eval": r"\beval\s*\(",
    "devtools_detect": r"(outerWidth\s*-\s*innerWidth|devtools|console\.clear)",
    "timing_trap": r"(Date\.now\s*\(|performance\.now\s*\()",
    "integrity_check": r"toString\s*\(\)\s*\[\s*['\"]constructor['\"]\s*\]",
}

CRYPTO_RULES = {
    "cryptojs": r"\bCryptoJS\b",
    "subtle_encrypt": r"crypto\.subtle\.encrypt\s*\(",
    "subtle_decrypt": r"crypto\.subtle\.decrypt\s*\(",
    "subtle_sign": r"crypto\.subtle\.sign\s*\(",
    "aes": r"\bAES\b|\baes\b",
    "rsa": r"\bRSA\b|\brsa\b",
    "hmac": r"\bHmac\w*\b|\bhmac\b",
    "sha": r"\bsha(1|224|256|384|512)?\b",
    "md5": r"\bmd5\b",
    "base64": r"\b(atob|btoa)\s*\(",
}

KEY_SOURCE_RULES = {
    "literal_key": r"\b(secret|key|iv|salt)\b\s*[:=]\s*['\"][^'\"]{4,}['\"]",
    "storage_key": r"(localStorage|sessionStorage)\s*\.\s*getItem\s*\(",
    "cookie_key": r"document\.cookie",
    "meta_key": r"document\.querySelector\s*\(\s*['\"]meta",
}

FINGERPRINT_RULES = {
    "navigator": r"navigator\.(userAgent|platform|language|languages|hardwareConcurrency|deviceMemory)",
    "screen": r"screen\.(width|height|availWidth|availHeight|colorDepth)",
    "timezone": r"Intl\.DateTimeFormat\s*\(\s*\)\.resolvedOptions\s*\(\s*\)\.timeZone",
    "canvas": r"(toDataURL\s*\(|getContext\s*\(\s*['\"]2d['\"]\s*\))",
    "webgl": r"(WebGLRenderingContext|getParameter\s*\(|WEBGL_debug_renderer_info)",
    "audio": r"(AudioContext|OfflineAudioContext|createOscillator\s*\()",
    "touch": r"(maxTouchPoints|ontouchstart)",
}

REQUEST_CHAIN_RULES = {
    "fetch": r"\bfetch\s*\(",
    "xhr": r"\bXMLHttpRequest\b|\.open\s*\(",
    "axios": r"\baxios\s*\.\s*(get|post|put|delete|request)\s*\(",
}

ASCII_STR_RE = re.compile(rb"[ -~]{4,}")


def line_no(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def find_hits(text: str, rules: Dict[str, str]) -> Dict[str, List[int]]:
    out: Dict[str, List[int]] = {}
    for name, pattern in rules.items():
        hits = [line_no(text, m.start()) for m in re.finditer(pattern, text, flags=re.IGNORECASE)]
        if hits:
            out[name] = hits[:50]
    return out


def collect_js_files(target: Path) -> List[Path]:
    if target.is_file():
        return [target] if target.suffix.lower() == ".js" else []
    return sorted(p for p in target.glob("**/*.js") if p.is_file())


def collect_wasm_files(target: Path) -> List[Path]:
    if not target.exists():
        return []
    if target.is_file():
        return [target] if target.suffix.lower() == ".wasm" else []
    return sorted(p for p in target.glob("**/*.wasm") if p.is_file())


def extract_wasm_clues(path: Path) -> Dict[str, object]:
    raw = path.read_bytes()
    is_wasm = raw.startswith(b"\x00asm")
    strings = [m.group(0).decode("utf-8", errors="ignore") for m in ASCII_STR_RE.finditer(raw)]

    crypto_terms = []
    term_re = re.compile(r"(aes|rsa|sha|md5|hmac|encrypt|decrypt|sign|verify)", flags=re.IGNORECASE)
    for s in strings:
        if term_re.search(s):
            crypto_terms.append(s)

    return {
        "file": str(path),
        "is_wasm_magic": is_wasm,
        "size": len(raw),
        "ascii_strings": strings[:100],
        "crypto_related_strings": crypto_terms[:50],
    }


def score_file(anti: Dict[str, List[int]], crypto: Dict[str, List[int]], keysrc: Dict[str, List[int]], fp: Dict[str, List[int]], req: Dict[str, List[int]]) -> int:
    return (
        sum(len(v) for v in anti.values()) * 2
        + sum(len(v) for v in crypto.values()) * 4
        + sum(len(v) for v in keysrc.values()) * 4
        + sum(len(v) for v in fp.values()) * 2
        + sum(len(v) for v in req.values()) * 3
    )


def analyze_js_file(path: Path) -> Dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    anti = find_hits(text, ANTI_DEBUG_RULES)
    crypto = find_hits(text, CRYPTO_RULES)
    keysrc = find_hits(text, KEY_SOURCE_RULES)
    fp = find_hits(text, FINGERPRINT_RULES)
    req = find_hits(text, REQUEST_CHAIN_RULES)

    sc = score_file(anti, crypto, keysrc, fp, req)

    return {
        "file": str(path),
        "score": sc,
        "anti_debug": anti,
        "crypto_primitives": crypto,
        "key_sources": keysrc,
        "fingerprint_signals": fp,
        "request_sinks": req,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Advanced reverse analysis for JS/WASM")
    parser.add_argument("js_target", help="Path to JS file or directory")
    parser.add_argument("--wasm-dir", help="Optional wasm directory")
    parser.add_argument("--out", help="Output JSON path")
    args = parser.parse_args()

    js_target = Path(args.js_target).expanduser().resolve()
    if not js_target.exists():
        print(json.dumps({"error": f"js target not found: {js_target}"}, ensure_ascii=False))
        return 1

    js_files = collect_js_files(js_target)
    js_results = [analyze_js_file(p) for p in js_files]
    js_results.sort(key=lambda x: x["score"], reverse=True)

    wasm_results: List[Dict[str, object]] = []
    if args.wasm_dir:
        wasm_dir = Path(args.wasm_dir).expanduser().resolve()
        for wf in collect_wasm_files(wasm_dir):
            wasm_results.append(extract_wasm_clues(wf))

    summary = {
        "js_target": str(js_target),
        "js_files_scanned": len(js_files),
        "top_js_candidates": js_results[:20],
        "wasm_files_scanned": len(wasm_results),
        "wasm_clues": wasm_results,
    }

    if args.out:
        out = Path(args.out).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
