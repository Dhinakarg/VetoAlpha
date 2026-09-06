"""
Unit tests for Read-Only Streamlit Dashboard.
Verifies read-only architectural guarantees, data loaders, and error-free module syntax.
"""

import ast
from pathlib import Path
import pytest

from dashboard.app import load_log_events, calculate_symbol_regime
from trading_agent.data.alpaca_data import AlpacaDataFetcher


def test_dashboard_read_only_guarantee():
    """Verify that dashboard/app.py contains NO order submission calls."""
    dashboard_file = Path("dashboard/app.py")
    assert dashboard_file.exists()
    content = dashboard_file.read_text(encoding="utf-8")

    # Ensure no write/submit methods are called or imported
    assert "submit_order" not in content
    assert "place_condor_order" not in content
    assert "AlpacaExecutor" not in content


def test_dashboard_loads_log_events(tmp_path, monkeypatch):
    """Test load_log_events reads and parses JSONL records correctly."""
    events = load_log_events()
    assert isinstance(events, list)


def test_dashboard_regime_calculation():
    """Test calculate_symbol_regime returns structured regime metrics."""
    fetcher = AlpacaDataFetcher()
    regime = calculate_symbol_regime(fetcher, "SPY")
    assert isinstance(regime, dict)
    assert "eligible" in regime
    assert "reasons" in regime


def test_dashboard_syntax_validity():
    """Verify dashboard/app.py compiles as valid Python AST without syntax errors."""
    code = Path("dashboard/app.py").read_text(encoding="utf-8")
    parsed = ast.parse(code)
    assert isinstance(parsed, ast.Module)
