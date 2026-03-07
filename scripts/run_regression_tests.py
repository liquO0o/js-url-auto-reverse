#!/usr/bin/env python3
"""Regression tests for js-url-auto-reverse skill scripts.

Runs 3 local file:// cases:
1. static-basic
2. dynamic-import
3. mini-obfuscated
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FETCH = ROOT / "scripts" / "fetch_js_from_url.py"
DEOB = ROOT / "scripts" / "deobfuscate_basic.py"
LOCATE = ROOT / "scripts" / "locate_js_candidates.py"
AUDIT = ROOT / "scripts" / "static_source_sink_audit.py"
ADVANCED = ROOT / "scripts" / "advanced_reverse_analysis.py"
PIPELINE = ROOT / "scripts" / "run_js_reverse_pipeline.py"
CASES = ROOT / "tests" / "cases"
TMP = ROOT / "tests" / "_tmp_runs"


def run(cmd):
    return subprocess.run(cmd, text=True, capture_output=True)


def parse_json(s: str):
    s = s.strip()
    if not s:
        return {}
    return json.loads(s)


def assert_true(cond: bool, msg: str):
    if not cond:
        raise AssertionError(msg)


def run_case(case_name: str) -> None:
    case_dir = CASES / case_name
    url = f"file://{case_dir / 'index.html'}"
    out_dir = TMP / case_name

    if out_dir.exists():
        shutil.rmtree(out_dir)

    fetch_proc = run([sys.executable, str(FETCH), url, "--out", str(out_dir)])
    assert_true(fetch_proc.returncode == 0, f"fetch failed for {case_name}: {fetch_proc.stderr}")

    fetch_data = parse_json(fetch_proc.stdout)
    assert_true(fetch_data.get("js_saved", 0) >= 1, f"no js saved for {case_name}")

    js_dir = Path(fetch_data["output_dir"]) / "js"
    deob_dir = out_dir / "deob"
    deob_proc = run([sys.executable, str(DEOB), str(js_dir), "--out-dir", str(deob_dir)])
    assert_true(deob_proc.returncode == 0, f"deob failed for {case_name}: {deob_proc.stderr}")

    locate_proc = run([sys.executable, str(LOCATE), str(js_dir), "--top", "5"])
    assert_true(locate_proc.returncode == 0, f"locate failed for {case_name}: {locate_proc.stderr}")
    locate_data = parse_json(locate_proc.stdout)
    assert_true(locate_data.get("files_scanned", 0) >= 1, f"locate scanned empty for {case_name}")
    assert_true(len(locate_data.get("results", [])) >= 1, f"locate results empty for {case_name}")

    audit_proc = run([sys.executable, str(AUDIT), str(js_dir)])
    assert_true(audit_proc.returncode == 0, f"audit failed for {case_name}: {audit_proc.stderr}")

    audit_data = parse_json(audit_proc.stdout)
    assert_true(audit_data.get("files_scanned", 0) >= 1, f"no files scanned for {case_name}")

    wasm_dir = Path(fetch_data["output_dir"]) / "wasm"
    adv_proc = run([sys.executable, str(ADVANCED), str(js_dir), "--wasm-dir", str(wasm_dir)])
    assert_true(adv_proc.returncode == 0, f"advanced analysis failed for {case_name}: {adv_proc.stderr}")
    adv_data = parse_json(adv_proc.stdout)
    assert_true(adv_data.get("js_files_scanned", 0) >= 1, f"advanced js scan empty for {case_name}")

    pipe_out = out_dir / "pipeline"
    pipe_proc = run([
        sys.executable,
        str(PIPELINE),
        url,
        "--mode",
        "auto",
        "--workdir",
        str(pipe_out),
    ])
    assert_true(pipe_proc.returncode == 0, f"pipeline failed for {case_name}: {pipe_proc.stderr}")
    pipe_data = parse_json(pipe_proc.stdout)
    assert_true("locate" in pipe_data and pipe_data["locate"] is not None, f"pipeline locate missing for {case_name}")
    assert_true("advanced_analysis" in pipe_data and pipe_data["advanced_analysis"] is not None, f"pipeline advanced missing for {case_name}")

    # Case-specific checks
    if case_name == "dynamic-import":
        dyn = fetch_data.get("dynamic_import_discovered", [])
        assert_true(any("chunk-sign.js" in x for x in dyn), "dynamic import not discovered")
        workers = fetch_data.get("worker_scripts_discovered", [])
        assert_true(any("worker-sign.js" in x for x in workers), "worker script not discovered")
        sws = fetch_data.get("service_workers_discovered", [])
        assert_true(any("sw.js" in x for x in sws), "service worker script not discovered")
        wasms = fetch_data.get("wasm_discovered", [])
        assert_true(any("sign.wasm" in x for x in wasms), "wasm url not discovered")
        wasm_files = fetch_data.get("wasm_files", [])
        assert_true(any("sign.wasm" in str(x) for x in wasm_files), "wasm file not fetched")
        maps = fetch_data.get("sourcemaps", [])
        assert_true(any("chunk-sign.js.map" in str(x) for x in maps), "sourcemap not fetched")
        assert_true(adv_data.get("wasm_files_scanned", 0) >= 1, "advanced wasm scan empty")


def main() -> int:
    TMP.mkdir(parents=True, exist_ok=True)

    for case in ["static-basic", "dynamic-import", "mini-obfuscated"]:
        run_case(case)

    shutil.rmtree(TMP, ignore_errors=True)
    print(json.dumps({"status": "ok", "cases": 3}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
