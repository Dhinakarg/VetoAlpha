"""
Launch Detached Background Trading Daemon.
Starts the autonomous trading agent in a detached, hidden background process.
Continues running continuously even if VSCode, IDE, or terminal windows are closed.
"""

import sys
import subprocess
import logging
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def launch_detached_daemon():
    print("\n" + "=" * 70)
    print("⚡ LAUNCHING PERSISTENT DETACHED BACKGROUND DAEMON")
    print("=" * 70)
    print("Mode:                    ALPACA_PAPER_SUBMISSION")
    print("Execution Persistence:  DETACHED (Survives IDE / Terminal Exit)")
    print("=" * 70 + "\n")

    cmd = [
        sys.executable,
        str(ROOT_DIR / "scripts" / "run_live_crypto_paper.py"),
        "--symbols", "BTC/USD", "ETH/USD", "SOL/USD",
        "--interval", "30",
        "--cycles", "100000",
        "--submit-alpaca-crypto",
    ]

    # Use CREATE_NO_WINDOW and DETACHED_PROCESS on Windows
    DETACHED_PROCESS = 0x00000008
    CREATE_NO_WINDOW = 0x08000000

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT_DIR),
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
            close_fds=True,
        )
        print(f"✅ Background Daemon Started Successfully! (Process ID: {proc.pid})")
        print("It is now running independently in the background and will NOT stop when you close VSCode or terminal.")
        print("\nCommands to monitor or stop:")
        print("  - Real-time performance summary: py -3 scripts/summarize_crypto.py")
        print("  - Streamlit Web Dashboard:       http://localhost:8501")
        print(f"  - Stop daemon via Task Manager or PowerShell: Stop-Process -Id {proc.pid} -Force")
        print("=" * 70 + "\n")
    except Exception as e:
        print(f"[ERROR] Failed to start detached daemon: {e}")


if __name__ == "__main__":
    launch_detached_daemon()
