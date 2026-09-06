"""
Featherless AI Quota & Usage Summary Tool.
Parses telemetry logs (logs/competition_run.jsonl) and state checkpoints (logs/competition_state.json)
to track LLM calls, skipped calls, cache hits, and estimated usage without fabricating provider credits.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def summarize_ai_usage(
    telemetry_file: Path = Path("logs/competition_run.jsonl"),
    state_file: Path = Path("logs/competition_state.json"),
) -> str:
    """Generate Featherless AI quota and call summary report."""
    records: List[Dict[str, Any]] = []
    if telemetry_file.exists():
        try:
            with open(telemetry_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        records.append(json.loads(line.strip()))
        except Exception:
            pass

    state = {}
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            pass

    stats = state.get("stats", {})

    total_calls = sum(1 for r in records if not r.get("ai_call_skipped"))
    successful_calls = total_calls - stats.get("ai_failures", 0)
    failed_calls = stats.get("ai_failures", 0)
    skipped_calls = sum(1 for r in records if r.get("ai_call_skipped"))
    cache_hits = stats.get("ai_cache_hits", 0)

    # Estimated token usage (~300 tokens per prompt/response completion)
    estimated_tokens = total_calls * 300
    estimated_cost_usd = (estimated_tokens / 1000.0) * 0.0002  # $0.0002 per 1k tokens estimate

    lines = [
        "## FEATHERLESS AI USAGE",
        "",
        f"Total calls: {total_calls}",
        f"Successful: {max(0, successful_calls)}",
        f"Failed: {failed_calls}",
        f"Skipped: {skipped_calls}",
        f"Cache hits: {cache_hits}",
        "",
        f"Estimated token usage: {estimated_tokens:,} tokens",
        f"Estimated cost: ~${estimated_cost_usd:.4f} USD",
        "Remaining budget: UNKNOWN unless provider data is actually available",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Featherless AI Quota & Usage Summary")
    parser.add_argument("--telemetry", type=str, default="logs/competition_run.jsonl")
    parser.add_argument("--state", type=str, default="logs/competition_state.json")

    args = parser.parse_args()
    summary = summarize_ai_usage(
        telemetry_file=Path(args.telemetry),
        state_file=Path(args.state),
    )
    print(summary)


if __name__ == "__main__":
    main()
