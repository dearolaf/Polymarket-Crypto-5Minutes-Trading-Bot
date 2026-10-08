"""Auto-restart wrapper for bot.py with runner logging and status tracking."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

BOT_SCRIPT = "bot.py"
STATUS_FILE = project_root / ".bot_status.json"
RUNNER_LOG = project_root / "logs" / "runner.log"


def _append_runner_log(message: str) -> None:
    RUNNER_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with RUNNER_LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"[{stamp}] {message}\n")


def _update_runner_status(*, exit_code: int | None = None, restart_count: int | None = None) -> None:
    if not STATUS_FILE.exists():
        return
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if exit_code is not None:
        data["last_exit_code"] = exit_code
        data["last_exit_at"] = datetime.now(timezone.utc).isoformat()
    if restart_count is not None:
        data["restart_count"] = restart_count
    try:
        STATUS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def run_bot() -> None:
    python_cmd = sys.executable
    bot_args = sys.argv[1:] if len(sys.argv) > 1 else []

    print("=" * 80)
    print("CRYPTO 5-MIN TRADING BOT - AUTO-RESTART WRAPPER (v5)")
    print("=" * 80)
    print(f"Platform: {sys.platform}")
    print(f"Python: {python_cmd}")
    print(f"Bot script: {BOT_SCRIPT}")
    print(f"Bot arguments: {bot_args}")
    print(f"Runner log: {RUNNER_LOG}")
    print("=" * 80)
    print()

    if not os.path.exists(BOT_SCRIPT):
        print(f"ERROR: Bot script '{BOT_SCRIPT}' not found in {os.getcwd()}")
        sys.exit(1)

    restart_count = 0
    consecutive_errors = 0

    while True:
        restart_count += 1
        started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cmd = [python_cmd, BOT_SCRIPT, *bot_args]
        cmd_str = " ".join(cmd)

        print("=" * 80)
        print(f"[{started}] Starting bot (restart #{restart_count})...")
        print(f"Command: {cmd_str}")
        print("=" * 80)
        print()
        _append_runner_log(f"START restart #{restart_count}: {cmd_str}")
        _update_runner_status(restart_count=restart_count)

        try:
            result = subprocess.run(cmd, check=False)
            exit_code = result.returncode
            stopped = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            print()
            print("=" * 80)
            print(f"Bot stopped at {stopped}")
            print(f"Exit code: {exit_code}")
            print("=" * 80)
            _append_runner_log(f"STOP restart #{restart_count} exit_code={exit_code}")
            _update_runner_status(exit_code=exit_code)

            if exit_code in (0, 143, 15, -15):
                print("Normal auto-restart — reloading...")
                wait_time = 2
                consecutive_errors = 0
            else:
                consecutive_errors += 1
                print(f"Error detected (code {exit_code}) — waiting before retry...")
                wait_time = min(60, 10 * consecutive_errors)

            print(f"Restarting in {wait_time} seconds...")
            print()
            time.sleep(wait_time)

        except KeyboardInterrupt:
            print()
            print("=" * 80)
            print("Keyboard interrupt — stopping wrapper")
            print("=" * 80)
            _append_runner_log("STOP wrapper (keyboard interrupt)")
            break
        except Exception as exc:
            consecutive_errors += 1
            print()
            print("=" * 80)
            print(f"ERROR running bot: {exc}")
            print("=" * 80)
            _append_runner_log(f"ERROR: {exc}")
            wait_time = min(60, 10 * consecutive_errors)
            print(f"Waiting {wait_time} seconds before retry...")
            print()
            time.sleep(wait_time)


if __name__ == "__main__":
    try:
        run_bot()
    except KeyboardInterrupt:
        print("\nStopped by user")
        sys.exit(0)
