#!/usr/bin/env python3
"""Optional dynamic capture using Playwright.

Purpose:
- capture runtime-loaded script URLs
- capture hook logs and selected console events
- optionally pause with CDP breakpoints and collect pause snapshots

This script is opt-in and never runs by default.
"""

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List


def compact_text(value: Any, max_len: int) -> str:
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[:max_len] + "...(truncated)"


def trim_value(value: Any, max_len: int, depth: int = 0) -> Any:
    if depth > 3:
        return compact_text(value, max_len)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return compact_text(value, max_len)
    if isinstance(value, list):
        return [trim_value(v, max_len, depth + 1) for v in value[:30]]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in list(value.items())[:30]:
            out[compact_text(k, 80)] = trim_value(v, max_len, depth + 1)
        return out
    return compact_text(value, max_len)


def build_capture_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "url": result.get("url"),
        "capture_plan": result.get("capture_plan", {}),
        "js_request_count": int(result.get("js_request_count", 0) or 0),
        "js_request_overflow": int(result.get("js_request_overflow", 0) or 0),
        "console_event_count": int(result.get("console_event_count", 0) or 0),
        "console_overflow": int(result.get("console_overflow", 0) or 0),
        "hook_event_count": int(result.get("hook_event_count", 0) or 0),
        "hook_overflow": int(result.get("hook_overflow", 0) or 0),
        "hook_store_dropped": int(result.get("hook_store_dropped", 0) or 0),
        "cdp_pause_count": int(result.get("cdp_pause_count", 0) or 0),
        "cdp_pause_overflow": int(result.get("cdp_pause_overflow", 0) or 0),
        "error_count": len(result.get("errors", []) or []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Dynamic runtime capture with Playwright (optional)")
    parser.add_argument("url", help="Target page URL")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--hook-file", help="Path to JS hook template to inject")
    parser.add_argument("--timeout-ms", type=int, default=15000)
    parser.add_argument("--post-wait-ms", type=int, default=1500, help="Extra wait after load")
    parser.add_argument("--max-js-requests", type=int, default=800)

    parser.add_argument("--capture-console", action="store_true", help="Capture selected console logs")
    parser.add_argument("--capture-all-console", action="store_true", help="Capture all console logs")
    parser.add_argument("--max-console-events", type=int, default=300)

    parser.add_argument("--capture-hook-buffer", action="store_true", help="Read window.__jsReverseLogs")
    parser.add_argument("--max-hook-events", type=int, default=800)
    parser.add_argument("--max-value-len", type=int, default=512)

    parser.add_argument("--enable-cdp-breakpoints", action="store_true", help="Enable CDP breakpoint pause capture")
    parser.add_argument("--cdp-url-regex", action="append", default=[], help="CDP setBreakpointByUrl regex")
    parser.add_argument("--cdp-xhr-breakpoint", action="append", default=[], help="CDP DOMDebugger XHR breakpoint substring")
    parser.add_argument("--max-cdp-pauses", type=int, default=10)
    parser.add_argument("--summary-only", action="store_true", help="Print compact summary instead of full events")

    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(
            json.dumps(
                {
                    "error": "playwright is not available",
                    "detail": str(e),
                    "hint": "Use static mode or install playwright in workspace-local venv only.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    js_requests: set[str] = set()
    js_request_overflow = 0

    console_events: List[Dict[str, Any]] = []
    console_overflow = 0

    cdp_pauses: List[Dict[str, Any]] = []
    cdp_pause_overflow = 0
    cdp_errors: List[str] = []

    def add_console_event(item: Dict[str, Any]) -> None:
        nonlocal console_overflow
        if len(console_events) < max(args.max_console_events, 1):
            console_events.append(item)
        else:
            console_overflow += 1

    def add_cdp_pause(item: Dict[str, Any]) -> None:
        nonlocal cdp_pause_overflow
        if len(cdp_pauses) < max(args.max_cdp_pauses, 1):
            cdp_pauses.append(item)
        else:
            cdp_pause_overflow += 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        def on_response(resp):
            nonlocal js_request_overflow
            try:
                url = resp.url
                ctype = (resp.headers or {}).get("content-type", "")
                is_js = url.lower().endswith(".js") or "javascript" in ctype
                if not is_js:
                    return
                if url in js_requests:
                    return
                if len(js_requests) < max(args.max_js_requests, 1):
                    js_requests.add(url)
                else:
                    js_request_overflow += 1
            except Exception as ex:
                cdp_errors.append(f"response handler error: {ex}")

        page.on("response", on_response)

        if args.capture_console:

            def on_console(msg):
                try:
                    text = msg.text or ""
                    if not args.capture_all_console and "[HOOK]" not in text:
                        return
                    add_console_event(
                        {
                            "ts": round(time.time(), 3),
                            "type": msg.type,
                            "text": compact_text(text, max(args.max_value_len, 64)),
                            "location": trim_value(msg.location or {}, max(args.max_value_len, 64)),
                        }
                    )
                except Exception as ex:
                    cdp_errors.append(f"console handler error: {ex}")

            page.on("console", on_console)

        cdp_session = None
        if args.enable_cdp_breakpoints:
            try:
                cdp_session = context.new_cdp_session(page)
                cdp_session.send("Runtime.enable")
                cdp_session.send("Debugger.enable")

                def on_paused(params):
                    try:
                        frames = params.get("callFrames", []) if isinstance(params, dict) else []
                        top = frames[0] if frames else {}
                        add_cdp_pause(
                            {
                                "ts": round(time.time(), 3),
                                "reason": params.get("reason") if isinstance(params, dict) else "unknown",
                                "hit_breakpoints": trim_value(params.get("hitBreakpoints", []), 160)
                                if isinstance(params, dict)
                                else [],
                                "top_frame": {
                                    "function": top.get("functionName", ""),
                                    "url": top.get("url", ""),
                                    "line": top.get("location", {}).get("lineNumber", -1),
                                    "column": top.get("location", {}).get("columnNumber", -1),
                                },
                            }
                        )
                    except Exception as ex:
                        cdp_errors.append(f"Debugger.paused parse error: {ex}")
                    finally:
                        try:
                            cdp_session.send("Debugger.resume")
                        except Exception as ex:
                            cdp_errors.append(f"Debugger.resume error: {ex}")

                cdp_session.on("Debugger.paused", on_paused)

                for pattern in args.cdp_url_regex:
                    try:
                        cdp_session.send(
                            "Debugger.setBreakpointByUrl",
                            {
                                "lineNumber": 0,
                                "urlRegex": pattern,
                            },
                        )
                    except Exception as ex:
                        cdp_errors.append(f"setBreakpointByUrl({pattern}) failed: {ex}")

                for snippet in args.cdp_xhr_breakpoint:
                    try:
                        cdp_session.send("DOMDebugger.setXHRBreakpoint", {"url": snippet})
                    except Exception as ex:
                        cdp_errors.append(f"setXHRBreakpoint({snippet}) failed: {ex}")

            except Exception as ex:
                cdp_errors.append(f"cdp init failed: {ex}")

        if args.hook_file:
            hook_path = Path(args.hook_file).expanduser().resolve()
            if hook_path.exists():
                if args.capture_hook_buffer:
                    hook_cfg = {
                        "maxEvents": max(args.max_hook_events, 1),
                        "maxValueLen": max(args.max_value_len, 64),
                    }
                    page.add_init_script(f"window.__JS_REVERSE_HOOK_CONFIG = {json.dumps(hook_cfg, ensure_ascii=False)};")

                hook_code = hook_path.read_text(encoding="utf-8", errors="ignore")
                page.add_init_script(hook_code)

        page.goto(args.url, wait_until="networkidle", timeout=args.timeout_ms)

        if args.post_wait_ms > 0:
            page.wait_for_timeout(args.post_wait_ms)

        hook_events: List[Dict[str, Any]] = []
        hook_overflow = 0
        hook_store_dropped = 0
        if args.capture_hook_buffer:
            try:
                hook_dump = page.evaluate(
                    """() => {
                        const store = window.__jsReverseLogs;
                        if (!store || !Array.isArray(store.events)) {
                            return {events: [], dropped: 0};
                        }
                        return {events: store.events, dropped: store.dropped || 0};
                    }"""
                )
                if isinstance(hook_dump, dict):
                    raw_events = hook_dump.get("events", [])
                    hook_store_dropped = int(hook_dump.get("dropped", 0) or 0)
                else:
                    raw_events = []
                cap = max(args.max_hook_events, 1)
                for item in raw_events[:cap]:
                    hook_events.append(trim_value(item, max(args.max_value_len, 64)))
                if len(raw_events) > cap:
                    hook_overflow = len(raw_events) - cap
            except Exception as ex:
                cdp_errors.append(f"hook buffer read failed: {ex}")

        result = {
            "url": args.url,
            "capture_plan": {
                "timeout_ms": args.timeout_ms,
                "post_wait_ms": args.post_wait_ms,
                "capture_console": args.capture_console,
                "capture_all_console": args.capture_all_console,
                "capture_hook_buffer": args.capture_hook_buffer,
                "enable_cdp_breakpoints": args.enable_cdp_breakpoints,
                "max_js_requests": args.max_js_requests,
                "max_console_events": args.max_console_events,
                "max_hook_events": args.max_hook_events,
                "max_cdp_pauses": args.max_cdp_pauses,
            },
            "js_requests": sorted(js_requests),
            "js_request_count": len(js_requests),
            "js_request_overflow": js_request_overflow,
            "console_events": console_events,
            "console_event_count": len(console_events),
            "console_overflow": console_overflow,
            "hook_events": hook_events,
            "hook_event_count": len(hook_events),
            "hook_overflow": hook_overflow,
            "hook_store_dropped": hook_store_dropped,
            "cdp_pauses": cdp_pauses,
            "cdp_pause_count": len(cdp_pauses),
            "cdp_pause_overflow": cdp_pause_overflow,
            "errors": cdp_errors,
        }

        (out_dir / "dynamic-capture.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary = build_capture_summary(result)
        (out_dir / "dynamic-capture-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        browser.close()

    if args.summary_only:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
