#!/usr/bin/env python3
"""Fetch page HTML and JavaScript assets from a URL into local workspace.

Features:
- Fetch HTML and external scripts.
- Save inline scripts as local JS files.
- Discover additional JS via static dynamic-import patterns.
- Fetch source maps referenced by sourceMappingURL comments.

No third-party dependencies.
"""

import argparse
import hashlib
import json
import os
import re
import time
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from typing import Deque, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

DYNAMIC_IMPORT_RE = re.compile(r"\bimport\s*\(\s*['\"]([^'\"]+\.js[^'\"]*)['\"]\s*\)")
SOURCE_MAP_RE = re.compile(r"[#@]\s*sourceMappingURL\s*=\s*([^\s*]+)")
WORKER_RE = re.compile(r"\bnew\s+Worker\s*\(\s*['\"]([^'\"]+\.(?:js|mjs)[^'\"]*)['\"]")
SW_REGISTER_RE = re.compile(r"\bserviceWorker\.register\s*\(\s*['\"]([^'\"]+\.(?:js|mjs)[^'\"]*)['\"]")
WASM_RE = re.compile(r"['\"]([^'\"]+\.wasm(?:\?[^'\"]*)?)['\"]")


class ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.script_srcs: List[str] = []
        self.inline_scripts: List[str] = []
        self._collect_inline = False
        self._inline_buffer: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "script":
            return
        attr_dict = {k.lower(): v for k, v in attrs}
        src = attr_dict.get("src")
        if src:
            self.script_srcs.append(src)
            self._collect_inline = False
            self._inline_buffer = []
            return
        self._collect_inline = True
        self._inline_buffer = []

    def handle_data(self, data: str) -> None:
        if self._collect_inline:
            self._inline_buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script":
            return
        if self._collect_inline:
            content = "".join(self._inline_buffer).strip()
            if content:
                self.inline_scripts.append(content)
        self._collect_inline = False
        self._inline_buffer = []


def fetch_bytes(url: str, timeout: int = 20) -> bytes:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def decode_response(raw: bytes, fallback: str = "utf-8") -> str:
    try:
        return raw.decode(fallback)
    except Exception:
        return raw.decode("utf-8", errors="ignore")


def derive_default_out(base_out: Path, url: str) -> Path:
    parsed = urlparse(url)
    host = parsed.netloc or "unknown-host"
    host = re.sub(r"[^A-Za-z0-9._-]", "_", host)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return base_out / f"{host}-{stamp}"


def safe_name(url: str, index: int, ext_hint: str = ".js") -> str:
    parsed = urlparse(url)
    base = os.path.basename(parsed.path) or f"item_{index}{ext_hint}"
    if "." not in base:
        base = f"{base}{ext_hint}"
    clean = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{index:03d}_{digest}_{clean}"


def should_skip_url(url: str) -> Optional[str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "file"}:
        return f"unsupported scheme: {parsed.scheme}"
    if url.startswith("data:"):
        return "data URL is not fetched"
    return None


def uniq_keep_order(items: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def discover_dynamic_imports(text: str, base_url: str) -> List[str]:
    found = [urljoin(base_url, m.group(1)) for m in DYNAMIC_IMPORT_RE.finditer(text)]
    return uniq_keep_order(found)


def discover_worker_scripts(text: str, base_url: str) -> Tuple[List[str], List[str]]:
    workers = [urljoin(base_url, m.group(1)) for m in WORKER_RE.finditer(text)]
    sw = [urljoin(base_url, m.group(1)) for m in SW_REGISTER_RE.finditer(text)]
    return uniq_keep_order(workers), uniq_keep_order(sw)


def discover_wasm_urls(text: str, base_url: str) -> List[str]:
    urls = [urljoin(base_url, m.group(1)) for m in WASM_RE.finditer(text)]
    return uniq_keep_order(urls)


def discover_sourcemap(text: str, base_url: str) -> Optional[str]:
    m = SOURCE_MAP_RE.search(text)
    if not m:
        return None
    ref = m.group(1).strip().strip("'\"")
    if not ref:
        return None
    return urljoin(base_url, ref)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch page and linked JS files from URL")
    parser.add_argument("url", help="Target page URL")
    parser.add_argument("--out", help="Output directory. Default: ./runs/<domain-timestamp>")
    parser.add_argument("--max-js", type=int, default=300, help="Maximum JS files to fetch")
    args = parser.parse_args()

    target_url = args.url.strip()
    base_out = Path("./runs").resolve()
    out_dir = Path(args.out).resolve() if args.out else derive_default_out(base_out, target_url)
    html_dir = out_dir / "html"
    js_dir = out_dir / "js"
    map_dir = out_dir / "maps"
    wasm_dir = out_dir / "wasm"

    html_dir.mkdir(parents=True, exist_ok=True)
    js_dir.mkdir(parents=True, exist_ok=True)
    map_dir.mkdir(parents=True, exist_ok=True)
    wasm_dir.mkdir(parents=True, exist_ok=True)

    manifest: Dict[str, object] = {
        "target_url": target_url,
        "output_dir": str(out_dir),
        "html_file": "",
        "js_total_discovered": 0,
        "js_saved_external": 0,
        "js_saved_inline": 0,
        "js_saved": 0,
        "js_failed": [],
        "js_files": [],
        "inline_scripts": [],
        "dynamic_import_discovered": [],
        "worker_scripts_discovered": [],
        "service_workers_discovered": [],
        "sourcemaps": [],
        "wasm_discovered": [],
        "wasm_files": [],
        "wasm_failed": [],
    }

    try:
        html_raw = fetch_bytes(target_url)
    except Exception as e:
        manifest["error"] = f"failed to fetch target page: {e}"
        (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 1

    html = decode_response(html_raw)
    html_path = html_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")
    manifest["html_file"] = str(html_path)

    p = ScriptParser()
    p.feed(html)

    # Save inline scripts so static audit can inspect them too.
    for i, script_text in enumerate(p.inline_scripts, start=1):
        file_path = js_dir / f"inline_{i:03d}.js"
        file_path.write_text(script_text, encoding="utf-8")
        manifest["inline_scripts"].append(str(file_path))

    queue: Deque[str] = deque()
    wasm_queue: Deque[str] = deque()
    visited: Set[str] = set()
    wasm_visited: Set[str] = set()
    saved: List[Dict[str, str]] = []
    failed: List[Dict[str, str]] = []
    dynamic_seen: Set[str] = set()
    worker_seen: Set[str] = set()
    sw_seen: Set[str] = set()
    sourcemap_seen: Set[str] = set()
    wasm_seen: Set[str] = set()

    initial = [urljoin(target_url, s) for s in p.script_srcs]
    initial.extend(discover_dynamic_imports(html, target_url))
    html_workers, html_sw = discover_worker_scripts(html, target_url)
    initial.extend(html_workers)
    initial.extend(html_sw)
    for w in html_workers:
        worker_seen.add(w)
    for sw in html_sw:
        sw_seen.add(sw)
    for wu in discover_wasm_urls(html, target_url):
        wasm_seen.add(wu)
        wasm_queue.append(wu)
    if target_url.lower().endswith(".js"):
        initial.insert(0, target_url)

    for u in uniq_keep_order(initial):
        queue.append(u)

    idx = 0
    while queue and idx < args.max_js:
        js_url = queue.popleft()
        if js_url in visited:
            continue
        visited.add(js_url)

        reason = should_skip_url(js_url)
        if reason:
            failed.append({"url": js_url, "error": reason})
            continue

        idx += 1
        file_name = safe_name(js_url, idx, ext_hint=".js")
        file_path = js_dir / file_name

        try:
            content = fetch_bytes(js_url)
            file_path.write_bytes(content)
            saved.append({"url": js_url, "path": str(file_path)})

            text = decode_response(content)
            for dyn in discover_dynamic_imports(text, js_url):
                if dyn not in visited:
                    dynamic_seen.add(dyn)
                    queue.append(dyn)

            worker_urls, sw_urls = discover_worker_scripts(text, js_url)
            for w in worker_urls:
                if w not in visited:
                    worker_seen.add(w)
                    queue.append(w)
            for sw in sw_urls:
                if sw not in visited:
                    sw_seen.add(sw)
                    queue.append(sw)

            for wu in discover_wasm_urls(text, js_url):
                if wu not in wasm_visited:
                    wasm_seen.add(wu)
                    wasm_queue.append(wu)

            sm = discover_sourcemap(text, js_url)
            if sm and sm not in sourcemap_seen:
                sourcemap_seen.add(sm)
                try:
                    sm_bytes = fetch_bytes(sm)
                    sm_name = safe_name(sm, len(sourcemap_seen), ext_hint=".map")
                    sm_path = map_dir / sm_name
                    sm_path.write_bytes(sm_bytes)
                    manifest["sourcemaps"].append({"url": sm, "path": str(sm_path)})
                except Exception as e:
                    manifest["sourcemaps"].append({"url": sm, "error": str(e)})

        except Exception as e:
            failed.append({"url": js_url, "error": str(e)})

    wasm_idx = 0
    while wasm_queue:
        wasm_url = wasm_queue.popleft()
        if wasm_url in wasm_visited:
            continue
        wasm_visited.add(wasm_url)

        reason = should_skip_url(wasm_url)
        if reason:
            manifest["wasm_failed"].append({"url": wasm_url, "error": reason})
            continue

        wasm_idx += 1
        wasm_name = safe_name(wasm_url, wasm_idx, ext_hint=".wasm")
        wasm_path = wasm_dir / wasm_name
        try:
            wasm_bin = fetch_bytes(wasm_url)
            wasm_path.write_bytes(wasm_bin)
            manifest["wasm_files"].append({"url": wasm_url, "path": str(wasm_path)})
        except Exception as e:
            manifest["wasm_failed"].append({"url": wasm_url, "error": str(e)})

    manifest["js_total_discovered"] = len(visited)
    manifest["js_saved_external"] = len(saved)
    manifest["js_saved_inline"] = len(manifest["inline_scripts"])
    manifest["js_saved"] = manifest["js_saved_external"] + manifest["js_saved_inline"]
    manifest["js_failed"] = failed
    manifest["js_files"] = saved
    manifest["dynamic_import_discovered"] = sorted(dynamic_seen)
    manifest["worker_scripts_discovered"] = sorted(worker_seen)
    manifest["service_workers_discovered"] = sorted(sw_seen)
    manifest["wasm_discovered"] = sorted(wasm_seen)

    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
