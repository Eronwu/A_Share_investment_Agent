#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "nightly_run.v1.json"
OPENCLAW_CONFIG = Path("/Users/kael/.openclaw/openclaw.json")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(value: str | None, base: Path = ROOT) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def run_command(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def split_text(text: str, limit: int = 3500) -> list[str]:
    content = (text or "").strip()
    if not content:
        return []
    chunks: list[str] = []
    while len(content) > limit:
        split_at = content.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(content[:split_at].strip())
        content = content[split_at:].lstrip()
    if content:
        chunks.append(content)
    return chunks


class TelegramClient:
    def __init__(self, bot_token: str, proxy: str | None = None):
        self.bot_token = bot_token
        self.proxy = proxy

    def _opener(self):
        if self.proxy:
            return request.build_opener(request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
        return request.build_opener()

    def send_text(self, chat_id: str, text: str) -> list[dict[str, Any]]:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        responses: list[dict[str, Any]] = []
        for chunk in split_text(text, 3500):
            body = json.dumps({"chat_id": chat_id, "text": chunk}, ensure_ascii=False).encode("utf-8")
            req = request.Request(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
            try:
                with self._opener().open(req, timeout=25) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    responses.append(payload)
                    if not payload.get("ok"):
                        break
            except error.URLError as exc:
                responses.append({"ok": False, "error": str(exc)})
                break
        return responses


def load_openclaw_channels() -> dict[str, Any]:
    if not OPENCLAW_CONFIG.exists():
        return {}
    try:
        cfg = load_json(OPENCLAW_CONFIG)
    except Exception:
        return {}
    channels = cfg.get("channels")
    return channels if isinstance(channels, dict) else {}


def build_pool(config: dict[str, Any]) -> dict[str, Any]:
    pool_cfg = config.get("build_pool", {}) if isinstance(config.get("build_pool"), dict) else {}
    enabled = bool(pool_cfg.get("enabled", True))
    if not enabled:
        return {"enabled": False, "skipped": True}

    rules = resolve_path(pool_cfg.get("rules") or "config/sector_pool_rules.v1.json")
    out = resolve_path(pool_cfg.get("out") or "config/sector_pool.generated.json")
    cmd = ["poetry", "run", "python", "tools/build_sector_pool.py", "--rules", str(rules), "--out", str(out)]
    proc = run_command(cmd, cwd=ROOT)
    reused_existing = False
    fallback_reason = ""
    success = proc.returncode == 0
    if not success and out and out.exists():
        reused_existing = True
        success = True
        fallback_reason = "build_pool failed; reused existing generated pool"
    return {
        "enabled": True,
        "command": cmd,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "rules": str(rules),
        "out": str(out),
        "success": success,
        "reused_existing": reused_existing,
        "fallback_reason": fallback_reason,
    }


def build_scan_command(config: dict[str, Any], report_dir: Path | None = None) -> list[str]:
    scan = config.get("scan", {}) if isinstance(config.get("scan"), dict) else {}
    cmd = ["poetry", "run", "python", "tools/nightly_scan.py"]

    arg_map = {
        "pool": "--pool",
        "top_n": "--top-n",
        "num_of_news": "--num-of-news",
        "reasoning_num_of_news": "--reasoning-num-of-news",
        "timeout": "--timeout",
        "limit": "--limit",
        "report_root": "--report-root",
        "model": "--model",
        "retries": "--retries",
    }
    for key, flag in arg_map.items():
        value = scan.get(key)
        if value is None:
            continue
        cmd.extend([flag, str(value)])

    if scan.get("hq"):
        cmd.append("--hq")
    if scan.get("skip_reasoning"):
        cmd.append("--skip-reasoning")
    if scan.get("skip_existing", True):
        cmd.append("--skip-existing")
    if report_dir is not None:
        cmd.extend(["--report-dir", str(report_dir)])
    return cmd


def run_scan(config: dict[str, Any], report_dir: Path | None = None) -> dict[str, Any]:
    cmd = build_scan_command(config, report_dir=report_dir)
    proc = run_command(cmd, cwd=ROOT)
    parsed: dict[str, Any] | None = None
    stdout = proc.stdout.strip()
    if stdout:
        last_line = stdout.splitlines()[-1]
        try:
            candidate = json.loads(last_line)
            if isinstance(candidate, dict):
                parsed = candidate
        except json.JSONDecodeError:
            parsed = None
    return {
        "command": cmd,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "parsed": parsed,
        "success": proc.returncode == 0,
    }


def load_report_artifacts(report_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    summary_cfg = config.get("summary", {}) if isinstance(config.get("summary"), dict) else {}
    txt_name = summary_cfg.get("txt_name") or "push_summary.txt"
    json_name = summary_cfg.get("json_name") or "push_summary.json"

    payload: dict[str, Any] = {
        "report_dir": str(report_dir),
        "exists": report_dir.exists(),
        "daily_report": str(report_dir / "daily_report.md"),
        "report_json": str(report_dir / "report.json"),
        "final_candidates_json": str(report_dir / "final_candidates.json"),
        "push_summary_txt": str(report_dir / txt_name),
        "push_summary_json": str(report_dir / json_name),
    }

    for key, file_name in (("report_json_payload", "report.json"), ("final_candidates_payload", "final_candidates.json"), ("push_summary_payload", json_name)):
        path = report_dir / file_name
        if path.exists():
            try:
                payload[key] = load_json(path)
            except json.JSONDecodeError:
                payload[key] = None
        else:
            payload[key] = None

    txt_path = report_dir / txt_name
    payload["push_summary_text"] = txt_path.read_text(encoding="utf-8") if txt_path.exists() else None
    return payload


def run_shell_notification(command_template: str, config: dict[str, Any], artifacts: dict[str, Any], success: bool) -> dict[str, Any]:
    summary_text = artifacts.get("push_summary_text") or ""
    report_dir = artifacts.get("report_dir") or ""
    rendered = str(command_template).format(
        status="success" if success else "failure",
        report_dir=report_dir,
        summary_file=artifacts.get("push_summary_txt") or "",
        summary_json=artifacts.get("push_summary_json") or "",
    )
    env = os.environ.copy()
    env["NIGHTLY_STATUS"] = "success" if success else "failure"
    env["NIGHTLY_REPORT_DIR"] = str(report_dir)
    env["NIGHTLY_SUMMARY_FILE"] = str(artifacts.get("push_summary_txt") or "")
    env["NIGHTLY_SUMMARY_JSON"] = str(artifacts.get("push_summary_json") or "")
    env["NIGHTLY_SUMMARY_TEXT"] = summary_text
    shell = str((config.get("notify") or {}).get("shell") or "/bin/zsh")
    proc = subprocess.run([shell, "-lc", rendered], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return {
        "enabled": True,
        "mode": "shell",
        "command": rendered,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "success": proc.returncode == 0,
    }


def run_notification(config: dict[str, Any], artifacts: dict[str, Any], success: bool) -> dict[str, Any]:
    notify_cfg = config.get("notify", {}) if isinstance(config.get("notify"), dict) else {}
    enabled = bool(notify_cfg.get("enabled", False))
    if not enabled:
        return {"enabled": False, "skipped": True}

    if success and not bool(notify_cfg.get("on_success", True)):
        return {"enabled": True, "skipped": True, "reason": "success notifications disabled"}
    if (not success) and not bool(notify_cfg.get("on_failure", True)):
        return {"enabled": True, "skipped": True, "reason": "failure notifications disabled"}

    command_template = notify_cfg.get("command")
    if command_template:
        return run_shell_notification(str(command_template), config, artifacts, success)

    channel = str(notify_cfg.get("channel") or "telegram").strip().lower()
    if channel != "telegram":
        return {"enabled": True, "skipped": True, "reason": f"unsupported notify.channel: {channel}"}

    channels_cfg = load_openclaw_channels()
    telegram_cfg = channels_cfg.get("telegram") if isinstance(channels_cfg, dict) else {}
    telegram_cfg = telegram_cfg if isinstance(telegram_cfg, dict) else {}
    bot_token = str(telegram_cfg.get("botToken") or "").strip()
    proxy = telegram_cfg.get("proxy") or None
    telegram_to = str(notify_cfg.get("telegram_to") or "").strip()
    if not bot_token or not telegram_to:
        return {
            "enabled": True,
            "mode": "telegram",
            "success": False,
            "error": "missing telegram bot token or target",
        }

    summary_text = artifacts.get("push_summary_text") or ""
    report_dir = str(artifacts.get("report_dir") or "")
    prefix = "[A股夜跑成功]" if success else "[A股夜跑告警]"
    text = f"{prefix}\n{summary_text}\n报告目录: {report_dir}".strip()
    client = TelegramClient(bot_token, proxy=proxy)
    responses = client.send_text(telegram_to, text)
    ok = bool(responses) and all(item.get("ok") for item in responses)
    return {
        "enabled": True,
        "mode": "telegram",
        "to": telegram_to,
        "response_count": len(responses),
        "responses": responses,
        "success": ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified nightly runner for A-share investment agent")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="nightly config json path")
    parser.add_argument("--report-dir", default=None, help="reuse an existing report dir")
    parser.add_argument("--skip-build-pool", action="store_true", help="skip pool generation even if enabled in config")
    parser.add_argument("--skip-notify", action="store_true", help="skip notify hook even if enabled in config")
    args = parser.parse_args()

    config_path = resolve_path(args.config)
    assert config_path is not None
    config = load_json(config_path)

    report_dir = resolve_path(args.report_dir) if args.report_dir else None
    started_at = datetime.now().isoformat(timespec="seconds")

    pool_result = {"enabled": False, "skipped": True}
    if not args.skip_build_pool:
        pool_result = build_pool(config)
        if pool_result.get("enabled") and not pool_result.get("success", False):
            payload = {
                "status": "failed",
                "stage": "build_pool",
                "started_at": started_at,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "config": str(config_path),
                "build_pool": pool_result,
            }
            print(json.dumps(payload, ensure_ascii=False))
            return 1

    scan_result = run_scan(config, report_dir=report_dir)
    if not scan_result.get("success", False):
        payload = {
            "status": "failed",
            "stage": "scan",
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "config": str(config_path),
            "build_pool": pool_result,
            "scan": scan_result,
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 1

    parsed = scan_result.get("parsed") or {}
    report_dir_value = parsed.get("report_dir")
    if not report_dir_value:
        payload = {
            "status": "failed",
            "stage": "scan_parse",
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "config": str(config_path),
            "build_pool": pool_result,
            "scan": scan_result,
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 1

    artifacts = load_report_artifacts(Path(report_dir_value), config)
    notify_result = {"enabled": False, "skipped": True}
    push_payload = artifacts.get("push_summary_payload") or {}
    had_failures = bool(push_payload.get("failed")) if isinstance(push_payload, dict) else False
    success = not had_failures
    if not args.skip_notify:
        notify_result = run_notification(config, artifacts, success=success)

    payload = {
        "status": "ok" if success else "completed_with_failures",
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "config": str(config_path),
        "build_pool": pool_result,
        "scan": scan_result,
        "artifacts": artifacts,
        "notify": notify_result,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
