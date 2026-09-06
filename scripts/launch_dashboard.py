"""
Launcher script for the Adaptive AI Iron Condor Agent Streamlit dashboard.
"""

import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_PATH = ROOT_DIR / "dashboard" / "app.py"


def main():
    print(f"Launching Read-Only Streamlit Dashboard from: {DASHBOARD_PATH}...")
    cmd = [sys.executable, "-m", "streamlit", "run", str(DASHBOARD_PATH), "--server.headless", "true"]
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\nDashboard stopped by user.")


if __name__ == "__main__":
    main()
