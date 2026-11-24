"""
Single Demonstration Script for System Presentation (Legacy Alias).
Delegates to scripts.demo_system.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from scripts.demo_system import main

if __name__ == "__main__":
    main()

