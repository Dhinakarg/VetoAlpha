"""
Local storage manager for trade logs, state checkpoints, and metrics.
"""

import json
from pathlib import Path
from typing import Dict, Any


class StorageManager:
    """Handles JSON storage for trade state checkpoints."""

    def __init__(self, data_dir: str = "logs"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def save_checkpoint(self, filename: str, data: Dict[str, Any]):
        """Save data dictionary to JSON file."""
        filepath = self.data_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load_checkpoint(self, filename: str) -> Dict[str, Any]:
        """Load data dictionary from JSON file."""
        filepath = self.data_dir / filename
        if not filepath.exists():
            return {}
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
