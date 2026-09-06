"""
API Key Validation Script for Alpaca Trading and Featherless AI.
Performs read-only connection checks and verifies authentication validity.
"""

import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure workspace root in path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
load_dotenv(ROOT_DIR / ".env", override=True)


def check_alpaca():
    print("\n" + "=" * 55)
    print("1. CHECKING ALPACA TRADING API CREDENTIALS")
    print("=" * 55)

    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    paper_str = os.getenv("ALPACA_PAPER", "true").lower()
    is_paper = paper_str in ("true", "1", "yes")

    if not api_key or not secret_key:
        print("[FAIL] Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env")
        return False

    masked_key = f"{api_key[:4]}****{api_key[-4:]}" if len(api_key) > 8 else "****"
    print(f"API Key:     {masked_key}")
    print(f"Paper Mode:  {is_paper}")

    try:
        from alpaca.trading.client import TradingClient

        client = TradingClient(api_key=api_key, secret_key=secret_key, paper=is_paper)
        account = client.get_account()
        clock = client.get_clock()

        print("[OK] Alpaca Authentication SUCCESSFUL!")
        print(f"  • Account ID:       {account.id}")
        print(f"  • Status:           {account.status}")
        print(f"  • Currency:         {account.currency}")
        print(f"  • Equity:           ${float(account.equity):,.2f}")
        print(f"  • Cash:             ${float(account.cash):,.2f}")
        print(f"  • Buying Power:     ${float(account.buying_power):,.2f}")
        print(f"  • Pattern Day T.:   {account.pattern_day_trader}")
        print(f"  • Market Open:      {clock.is_open}")
        print(f"  • Next Open (UTC):  {clock.next_open}")
        print(f"  • Next Close (UTC): {clock.next_close}")
        return True

    except Exception as e:
        print(f"[FAIL] Alpaca Authentication FAILED: {e}")
        return False


def check_featherless():
    print("\n" + "=" * 55)
    print("2. CHECKING FEATHERLESS AI CREDENTIALS")
    print("=" * 55)

    api_key = os.getenv("FEATHERLESS_API_KEY")
    base_url = os.getenv("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1")
    model = os.getenv("FEATHERLESS_MODEL", "deepseek-ai/DeepSeek-V4-Flash-0731")

    if not api_key:
        print("[FAIL] Missing FEATHERLESS_API_KEY in .env")
        return False

    masked_key = f"{api_key[:6]}****{api_key[-4:]}" if len(api_key) > 10 else "****"
    print(f"API Key:   {masked_key}")
    print(f"Base URL:  {base_url}")
    print(f"Model:     {model}")

    try:
        from openai import OpenAI

        client = OpenAI(base_url=base_url, api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a test agent. Respond in valid JSON."},
                {"role": "user", "content": 'Respond with: {"status": "ok", "message": "ping"}'},
            ],
            temperature=0.0,
            max_tokens=50,
        )

        reply = response.choices[0].message.content
        print("[OK] Featherless AI Authentication SUCCESSFUL!")
        print(f"  • Model:          {model}")
        print(f"  • Response Text:  {reply.strip()[:100]}")
        return True

    except Exception as e:
        print(f"[FAIL] Featherless AI Authentication FAILED: {e}")
        return False


def main():
    alpaca_ok = check_alpaca()
    featherless_ok = check_featherless()

    print("\n" + "=" * 55)
    print("SUMMARY")
    print("=" * 55)
    print(f"Alpaca API:      {'[OK] VALID' if alpaca_ok else '[FAIL] INVALID'}")
    print(f"Featherless AI:  {'[OK] VALID' if featherless_ok else '[FAIL] INVALID'}")
    print("=" * 55 + "\n")

    if not (alpaca_ok and featherless_ok):
        sys.exit(1)


if __name__ == "__main__":
    main()
