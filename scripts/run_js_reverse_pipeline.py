#!/usr/bin/env python3
"""JS reverse pipeline orchestrator.

Mode:
- static (default): fetch + static audit
- dynamic: static first, then cost-aware dynamic capture ladder
- auto: static first; only try dynamic when confidence is unknown (unless forced)

This script never modifies global environment.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

THIS_DIR = Path(__file__).resolve().parent
FETCH_SCRIPT = THIS_DIR / "fetch_js_from_url.py"
DEOB_SCRIPT = THIS_DIR / "deobfuscate_basic.py"
LOCATE_SCRIPT = THIS_DIR / "locate_js_candidates.py"
STATIC_SCRIPT = THIS_DIR / "static_source_sink_audit.py"
ADVANCED_SCRIPT = THIS_DIR / "advanced_reverse_analysis.py"
DYNAMIC_CAPTURE_SCRIPT = THIS_DIR / "dynamic_capture_playwright.py"

DEFAULT_CDP_URL_REGEX = [
    r".*(sign|signature|token|encrypt|crypto|aes|hmac|sha|auth|login).*\\.js.*",
]
DEFAULT_CDP_XHR_BREAKPOINTS = ["/api/", "/auth", "/login"]

BUDGET_PROFILES: Dict[str, Dict[str, Any]] = {
    "low": {
        "hook": {
            "timeout_ms": 8000,
            "post_wait_ms": 1000,
            "max_js_requests": 200,
            "max_console_events": 120,
            "max_hook_events": 300,
            "max_value_len": 256,
            "capture_all_console": False,
        },
        "cdp": {
            "timeout_ms": 9000,
            "post_wait_ms": 1000,
            "max_js_requests": 260,
            "max_console_events": 150,
            "max_hook_events": 350,
            "max_value_len": 256,
            "max_cdp_pauses": 4,
            "capture_all_console": False,
        },
        "escalate_threshold": 16,
    },
    "medium": {
        "hook": {
            "timeout_ms": 12000,
            "post_wait_ms": 1800,
            "max_js_requests": 500,
            "max_console_events": 280,
            "max_hook_events": 800,
            "max_value_len": 384,
            "capture_all_console": False,
        },
        "cdp": {
            "timeout_ms": 13000,
            "post_wait_ms": 2000,
            "max_js_requests": 650,
            "max_console_events": 320,
            "max_hook_events": 900,
            "max_value_len": 384,
            "max_cdp_pauses": 10,
            "capture_all_console": False,
        },
        "escalate_threshold": 24,
    },
    "high": {
        "hook": {
            "timeout_ms": 16000,
            "post_wait_ms": 2500,
            "max_js_requests": 900,
            "max_console_events": 500,
            "max_hook_events": 1400,
            "max_value_len": 512,
            "capture_all_console": True,
        },
        "cdp": {
            "timeout_ms": 18000,
            "post_wait_ms": 3200,
            "max_js_requests": 1200,
            "max_console_events": 700,
            "max_hook_events": 1800,
            "max_value_len": 640,
            "max_cdp_pauses": 20,
            "capture_all_console": True,
        },
        "escalate_threshold": 40,
    },
}


def run_cmd(cmd: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True)


def parse_json_output(text: str) -> Dict[str, Any]:
    text = text.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        idx = text.rfind("{\n")
        if idx >= 0:
            try:
                return json.loads(text[idx:])
            except Exception:
                pass
    return {}


def read_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}


def write_json_file(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def short_text(text: str, limit: int = 800) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "...(truncated)"


def stage_error(proc: subprocess.CompletedProcess) -> Optional[Dict[str, str]]:
    if proc.returncode == 0:
        return None
    return {
        "stderr": short_text(proc.stderr, 1200),
        "stdout": short_text(proc.stdout, 1200),
    }


def sum_hits(bucket: Any) -> int:
    if isinstance(bucket, list):
        return len(bucket)
    if isinstance(bucket, dict):
        total = 0
        for value in bucket.values():
            total += sum_hits(value)
        return total
    return 0


def summarize_fetch(fetch_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "target_url": fetch_data.get("target_url"),
        "output_dir": fetch_data.get("output_dir"),
        "js_total_discovered": int(fetch_data.get("js_total_discovered", 0) or 0),
        "js_saved": int(fetch_data.get("js_saved", 0) or 0),
        "js_failed_count": len(fetch_data.get("js_failed", []) or []),
        "dynamic_import_count": len(fetch_data.get("dynamic_import_discovered", []) or []),
        "worker_count": len(fetch_data.get("worker_scripts_discovered", []) or []),
        "service_worker_count": len(fetch_data.get("service_workers_discovered", []) or []),
        "wasm_count": len(fetch_data.get("wasm_discovered", []) or []),
        "sourcemap_count": len(fetch_data.get("sourcemaps", []) or []),
    }


def summarize_deob(deob_data: Dict[str, Any]) -> Dict[str, Any]:
    items = deob_data.get("items", []) or []
    return {
        "target": deob_data.get("target"),
        "out_dir": deob_data.get("out_dir"),
        "files": int(deob_data.get("files", 0) or 0),
        "removed_debugger_total": sum(int((x.get("report", {}) or {}).get("removed_debugger", 0) or 0) for x in items),
        "simplified_tokens_total": sum(int((x.get("report", {}) or {}).get("simplified_tokens", 0) or 0) for x in items),
        "member_dot_normalized_total": sum(int((x.get("report", {}) or {}).get("member_dot_normalized", 0) or 0) for x in items),
    }


def summarize_locate(locate_data: Dict[str, Any], top_n: int = 3) -> Dict[str, Any]:
    out_items = []
    for item in (locate_data.get("results", []) or [])[:top_n]:
        signals = item.get("signals", {}) or {}
        out_items.append(
            {
                "file": item.get("file"),
                "score": int(item.get("score", 0) or 0),
                "priority": item.get("priority"),
                "signal_categories": sorted(list(signals.keys())),
                "signal_hits": sum_hits(signals),
            }
        )

    return {
        "target": locate_data.get("target"),
        "files_scanned": int(locate_data.get("files_scanned", 0) or 0),
        "high_count": int(locate_data.get("high_count", 0) or 0),
        "medium_count": int(locate_data.get("medium_count", 0) or 0),
        "low_count": int(locate_data.get("low_count", 0) or 0),
        "top_results": out_items,
    }


def summarize_static(static_data: Dict[str, Any], top_n: int = 3) -> Dict[str, Any]:
    out_items = []
    for item in (static_data.get("results", []) or [])[:top_n]:
        out_items.append(
            {
                "file": item.get("file"),
                "score": int(item.get("score", 0) or 0),
                "confidence": item.get("confidence"),
                "sink_hits": sum_hits(item.get("sinks", {})),
                "crypto_hits": sum_hits(item.get("crypto", {})),
                "source_hits": sum_hits(item.get("sources", {})),
                "keyword_hits": sum_hits(item.get("keywords", {})),
            }
        )

    return {
        "target": static_data.get("target"),
        "files_scanned": int(static_data.get("files_scanned", 0) or 0),
        "top_confidence": static_data.get("top_confidence", "unknown"),
        "confirmed_count": int(static_data.get("confirmed_count", 0) or 0),
        "likely_count": int(static_data.get("likely_count", 0) or 0),
        "unknown_count": int(static_data.get("unknown_count", 0) or 0),
        "top_results": out_items,
    }


def summarize_advanced(adv_data: Dict[str, Any], top_n: int = 3) -> Dict[str, Any]:
    out_items = []
    for item in (adv_data.get("top_js_candidates", []) or [])[:top_n]:
        out_items.append(
            {
                "file": item.get("file"),
                "score": int(item.get("score", 0) or 0),
                "anti_debug_hits": sum_hits(item.get("anti_debug", {})),
                "crypto_hits": sum_hits(item.get("crypto_primitives", {})),
                "key_source_hits": sum_hits(item.get("key_sources", {})),
                "fingerprint_hits": sum_hits(item.get("fingerprint_signals", {})),
                "request_hits": sum_hits(item.get("request_sinks", {})),
            }
        )

    return {
        "js_target": adv_data.get("js_target"),
        "js_files_scanned": int(adv_data.get("js_files_scanned", 0) or 0),
        "wasm_files_scanned": int(adv_data.get("wasm_files_scanned", 0) or 0),
        "top_js_candidates": out_items,
    }


def get_runtime_signals(locate_data: Dict[str, Any], adv_data: Dict[str, Any]) -> Dict[str, int]:
    locate_results = (locate_data or {}).get("results") or []
    top_locate = locate_results[0] if locate_results else {}

    top_adv = (adv_data or {}).get("top_js_candidates") or []
    anti_debug_hits = 0
    crypto_hits = 0
    request_hits = 0
    for item in top_adv[:3]:
        anti_debug_hits += sum_hits((item or {}).get("anti_debug", {}))
        crypto_hits += sum_hits((item or {}).get("crypto_primitives", {}))
        request_hits += sum_hits((item or {}).get("request_sinks", {}))

    return {
        "locate_top_score": int((top_locate or {}).get("score", 0) or 0),
        "locate_high_count": int((locate_data or {}).get("high_count", 0) or 0),
        "wasm_files": int((adv_data or {}).get("wasm_files_scanned", 0) or 0),
        "anti_debug_hits": anti_debug_hits,
        "crypto_hits": crypto_hits,
        "request_hits": request_hits,
    }


def build_dynamic_plan(
    mode: str,
    allow_dynamic: bool,
    force_dynamic: bool,
    force_cdp: bool,
    budget_level: str,
    static_data: Dict[str, Any],
    locate_data: Dict[str, Any],
    adv_data: Dict[str, Any],
) -> Dict[str, Any]:
    top_conf = (static_data or {}).get("top_confidence", "unknown")
    unknown = top_conf == "unknown"
    explicit_dynamic = mode == "dynamic"

    profile = BUDGET_PROFILES[budget_level]
    signals = get_runtime_signals(locate_data, adv_data)
    heavy_target = signals["wasm_files"] > 0 or signals["anti_debug_hits"] >= 6

    if mode == "static":
        return {
            "run_dynamic": False,
            "reason": "mode=static",
            "dynamic_level": "none",
            "budget_level": budget_level,
            "signals": signals,
        }

    if not allow_dynamic:
        return {
            "run_dynamic": False,
            "reason": "dynamic requested but --allow-dynamic not set",
            "dynamic_level": "blocked",
            "budget_level": budget_level,
            "signals": signals,
        }

    need_dynamic = explicit_dynamic or unknown or force_dynamic
    if not need_dynamic:
        return {
            "run_dynamic": False,
            "reason": f"top_confidence={top_conf}, skip dynamic for cost",
            "dynamic_level": "none",
            "budget_level": budget_level,
            "signals": signals,
        }

    dynamic_level = "hook"
    if force_cdp:
        dynamic_level = "hook+cdp"
    elif budget_level == "high":
        if explicit_dynamic or unknown or heavy_target:
            dynamic_level = "hook+cdp"
    elif budget_level == "medium":
        if heavy_target and (explicit_dynamic or unknown):
            dynamic_level = "hook+cdp"
    else:
        if explicit_dynamic and heavy_target and signals["locate_top_score"] >= 100:
            dynamic_level = "hook+cdp"

    return {
        "run_dynamic": True,
        "reason": "static-first ladder with cost-aware escalation",
        "dynamic_level": dynamic_level,
        "budget_level": budget_level,
        "signals": signals,
        "escalate_threshold": int(profile["escalate_threshold"]),
        "profiles": {
            "hook": profile["hook"],
            "cdp": profile["cdp"],
        },
    }


def public_dynamic_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "run_dynamic",
        "reason",
        "dynamic_level",
        "budget_level",
        "signals",
        "escalate_threshold",
    ]
    return {k: plan[k] for k in keys if k in plan}


def summarize_dynamic_data(data: Dict[str, Any]) -> Dict[str, Any]:
    if not data:
        return {}
    if "error" in data:
        return {
            "error": data.get("error"),
            "detail": short_text(str(data.get("detail", "")), 400),
            "hint": data.get("hint"),
        }
    return {
        "url": data.get("url"),
        "js_request_count": int(data.get("js_request_count", 0) or 0),
        "js_request_overflow": int(data.get("js_request_overflow", 0) or 0),
        "console_event_count": int(data.get("console_event_count", 0) or 0),
        "console_overflow": int(data.get("console_overflow", 0) or 0),
        "hook_event_count": int(data.get("hook_event_count", 0) or 0),
        "hook_overflow": int(data.get("hook_overflow", 0) or 0),
        "hook_store_dropped": int(data.get("hook_store_dropped", 0) or 0),
        "cdp_pause_count": int(data.get("cdp_pause_count", 0) or 0),
        "cdp_pause_overflow": int(data.get("cdp_pause_overflow", 0) or 0),
        "error_count": int(data.get("error_count", 0) or 0),
    }


def run_dynamic_capture(url: str, out_dir: Path, options: Dict[str, Any], enable_cdp: bool) -> Dict[str, Any]:
    hook_file = THIS_DIR.parent / "references" / "hook-templates.js"

    cmd: List[str] = [
        sys.executable,
        str(DYNAMIC_CAPTURE_SCRIPT),
        url,
        "--out",
        str(out_dir),
        "--timeout-ms",
        str(options["timeout_ms"]),
        "--post-wait-ms",
        str(options["post_wait_ms"]),
        "--max-js-requests",
        str(options["max_js_requests"]),
        "--max-console-events",
        str(options["max_console_events"]),
        "--max-hook-events",
        str(options["max_hook_events"]),
        "--max-value-len",
        str(options["max_value_len"]),
        "--capture-console",
        "--capture-hook-buffer",
        "--summary-only",
    ]

    if hook_file.exists():
        cmd.extend(["--hook-file", str(hook_file)])

    if options.get("capture_all_console"):
        cmd.append("--capture-all-console")

    if enable_cdp:
        cmd.extend(
            [
                "--enable-cdp-breakpoints",
                "--max-cdp-pauses",
                str(options["max_cdp_pauses"]),
            ]
        )
        for regex in DEFAULT_CDP_URL_REGEX:
            cmd.extend(["--cdp-url-regex", regex])
        for snippet in DEFAULT_CDP_XHR_BREAKPOINTS:
            cmd.extend(["--cdp-xhr-breakpoint", snippet])

    proc = run_cmd(cmd)
    parsed = parse_json_output(proc.stdout)

    summary_file = out_dir / "dynamic-capture-summary.json"
    if not parsed:
        parsed = read_json_file(summary_file)

    summary = summarize_dynamic_data(parsed)
    return {
        "stage": "hook+cdp" if enable_cdp else "hook",
        "returncode": proc.returncode,
        "summary": summary,
        "artifacts": {
            "summary_json": str(summary_file),
            "full_json": str(out_dir / "dynamic-capture.json"),
        },
        "error": stage_error(proc),
    }


def dynamic_evidence_score(summary: Dict[str, Any]) -> int:
    hook_n = int(summary.get("hook_event_count", 0) or 0)
    console_n = int(summary.get("console_event_count", 0) or 0)
    pause_n = int(summary.get("cdp_pause_count", 0) or 0)
    return hook_n + min(console_n, 30) + pause_n * 6


def execute_dynamic_ladder(url: str, run_dir: Path, plan: Dict[str, Any]) -> Dict[str, Any]:
    if not plan.get("run_dynamic"):
        return {
            "attempted": False,
            "status": "skipped",
            "reason": plan.get("reason", "dynamic disabled"),
            "passes": [],
        }

    passes: List[Dict[str, Any]] = []
    hook_out_dir = run_dir / "dynamic-hook"
    hook_profile = dict(plan["profiles"]["hook"])
    hook_result = run_dynamic_capture(url, hook_out_dir, hook_profile, enable_cdp=False)
    passes.append(hook_result)

    hook_summary = hook_result.get("summary") or {}
    score = dynamic_evidence_score(hook_summary)
    should_escalate = False
    escalate_reason = ""

    if plan.get("dynamic_level") == "hook+cdp":
        threshold = int(plan.get("escalate_threshold", 20))
        if hook_result.get("returncode") != 0:
            should_escalate = True
            escalate_reason = "hook stage failed"
        elif score < threshold:
            should_escalate = True
            escalate_reason = f"hook evidence score({score}) < threshold({threshold})"
        elif int((hook_summary or {}).get("hook_store_dropped", 0) or 0) > 0:
            should_escalate = True
            escalate_reason = "hook buffer dropped events"

    if should_escalate:
        cdp_out_dir = run_dir / "dynamic-cdp"
        cdp_profile = dict(plan["profiles"]["cdp"])
        cdp_result = run_dynamic_capture(url, cdp_out_dir, cdp_profile, enable_cdp=True)
        passes.append(cdp_result)

    status = "ok"
    if any(p.get("returncode") != 0 for p in passes):
        status = "failed"

    return {
        "attempted": True,
        "status": status,
        "passes": passes,
        "hook_evidence_score": score,
        "escalated_to_cdp": should_escalate,
        "escalation_reason": escalate_reason,
    }


def stage_payload(
    returncode: int,
    summary: Dict[str, Any],
    artifacts: Dict[str, Any],
    error: Optional[Dict[str, str]],
    raw_data: Optional[Dict[str, Any]],
    verbose_output: bool,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "returncode": returncode,
        "summary": summary,
        "artifacts": artifacts,
        "error": error,
    }
    if verbose_output:
        out["raw_data"] = raw_data or {}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Run js-url-auto-reverse pipeline")
    parser.add_argument("url", help="Target URL")
    parser.add_argument("--mode", choices=["static", "dynamic", "auto"], default="static")
    parser.add_argument("--workdir", help="Working directory. Default: ./runs/<timestamp>")
    parser.add_argument("--cleanup", action="store_true", help="Remove working directory after run")
    parser.add_argument("--allow-dynamic", action="store_true", help="Allow dynamic phase when mode requires")
    parser.add_argument("--budget-level", choices=["low", "medium", "high"], default="low", help="Dynamic budget profile")
    parser.add_argument("--force-dynamic", action="store_true", help="Run dynamic ladder even when static confidence is not unknown")
    parser.add_argument("--force-cdp", action="store_true", help="Force cdp stage after hook stage")
    parser.add_argument("--verbose-output", action="store_true", help="Include full raw stage data in final JSON")
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.workdir).resolve() if args.workdir else (Path("./runs") / f"pipeline-{stamp}").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    fetch_cmd = [sys.executable, str(FETCH_SCRIPT), args.url, "--out", str(run_dir / "fetch")]
    fetch_proc = run_cmd(fetch_cmd)
    fetch_data = parse_json_output(fetch_proc.stdout)
    fetch_json = run_dir / "fetch-result.json"
    write_json_file(fetch_json, fetch_data)

    output: Dict[str, Any] = {
        "mode": args.mode,
        "budget_level": args.budget_level,
        "run_dir": str(run_dir),
        "fetch": stage_payload(
            returncode=fetch_proc.returncode,
            summary=summarize_fetch(fetch_data),
            artifacts={"json": str(fetch_json), "fetch_dir": str(run_dir / "fetch")},
            error=stage_error(fetch_proc),
            raw_data=fetch_data,
            verbose_output=args.verbose_output,
        ),
        "deobfuscation": None,
        "locate": None,
        "static_audit": None,
        "advanced_analysis": None,
        "dynamic_plan": None,
        "dynamic": None,
        "manual_fallback": None,
    }

    if fetch_proc.returncode != 0 or not fetch_data.get("js_saved"):
        output["manual_fallback"] = {
            "required": True,
            "reason": "auto fetch failed or no js saved",
            "instruction": "请手动保存核心JS文件到本地目录后，重新执行静态审计。",
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if args.cleanup:
            shutil.rmtree(run_dir, ignore_errors=True)
        return 2

    js_dir = Path(fetch_data["output_dir"]) / "js"
    deob_dir = run_dir / "deob-js"
    deob_report_path = run_dir / "deobfuscation.json"

    deob_cmd = [
        sys.executable,
        str(DEOB_SCRIPT),
        str(js_dir),
        "--out-dir",
        str(deob_dir),
        "--report",
        str(deob_report_path),
    ]
    deob_proc = run_cmd(deob_cmd)
    deob_data = parse_json_output(deob_proc.stdout)

    output["deobfuscation"] = stage_payload(
        returncode=deob_proc.returncode,
        summary=summarize_deob(deob_data),
        artifacts={"json": str(deob_report_path), "deob_dir": str(deob_dir)},
        error=stage_error(deob_proc),
        raw_data=deob_data,
        verbose_output=args.verbose_output,
    )

    analysis_target = deob_dir if deob_proc.returncode == 0 else js_dir

    locate_json = run_dir / "locate.json"
    locate_cmd = [sys.executable, str(LOCATE_SCRIPT), str(analysis_target), "--out", str(locate_json)]
    locate_proc = run_cmd(locate_cmd)
    locate_data = parse_json_output(locate_proc.stdout)

    output["locate"] = stage_payload(
        returncode=locate_proc.returncode,
        summary=summarize_locate(locate_data),
        artifacts={"json": str(locate_json), "analysis_target": str(analysis_target)},
        error=stage_error(locate_proc),
        raw_data=locate_data,
        verbose_output=args.verbose_output,
    )

    static_json = run_dir / "static-audit.json"
    static_cmd = [sys.executable, str(STATIC_SCRIPT), str(analysis_target), "--out", str(static_json)]
    static_proc = run_cmd(static_cmd)
    static_data = parse_json_output(static_proc.stdout)

    output["static_audit"] = stage_payload(
        returncode=static_proc.returncode,
        summary=summarize_static(static_data),
        artifacts={"json": str(static_json), "analysis_target": str(analysis_target)},
        error=stage_error(static_proc),
        raw_data=static_data,
        verbose_output=args.verbose_output,
    )

    wasm_dir = Path(fetch_data["output_dir"]) / "wasm"
    adv_json = run_dir / "advanced-analysis.json"
    adv_cmd = [
        sys.executable,
        str(ADVANCED_SCRIPT),
        str(analysis_target),
        "--out",
        str(adv_json),
    ]
    if wasm_dir.exists():
        adv_cmd.extend(["--wasm-dir", str(wasm_dir)])
    adv_proc = run_cmd(adv_cmd)
    adv_data = parse_json_output(adv_proc.stdout)

    output["advanced_analysis"] = stage_payload(
        returncode=adv_proc.returncode,
        summary=summarize_advanced(adv_data),
        artifacts={"json": str(adv_json), "analysis_target": str(analysis_target)},
        error=stage_error(adv_proc),
        raw_data=adv_data,
        verbose_output=args.verbose_output,
    )

    dynamic_plan = build_dynamic_plan(
        mode=args.mode,
        allow_dynamic=args.allow_dynamic,
        force_dynamic=args.force_dynamic,
        force_cdp=args.force_cdp,
        budget_level=args.budget_level,
        static_data=static_data,
        locate_data=locate_data,
        adv_data=adv_data,
    )
    output["dynamic_plan"] = public_dynamic_plan(dynamic_plan)

    output["dynamic"] = execute_dynamic_ladder(args.url, run_dir, dynamic_plan)

    top_conf = (static_data or {}).get("top_confidence", "unknown")
    if top_conf == "unknown":
        output["manual_fallback"] = {
            "required": True,
            "reason": "static evidence is insufficient",
            "instruction": "请手动保存主bundle与相关chunk后，继续静态Source-Sink审计定位加密方法或密钥来源。",
        }

    print(json.dumps(output, ensure_ascii=False, indent=2))

    if args.cleanup:
        shutil.rmtree(run_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
