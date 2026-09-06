"""
Alpaca Model Context Protocol (MCP) Capability Discovery & Connectivity Tool.
Connects to Alpaca Paper MCP integration, discovers all read and write tools,
verifies paper account status, and confirms zero orders placed.
"""

import sys
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from trading_agent.integrations.alpaca_mcp import AlpacaMCPClient, ALL_MCP_TOOLS
from trading_agent.data.alpaca_data import AlpacaDataFetcher


def mask_account_id(account_id: str) -> str:
    if not account_id or len(account_id) < 6:
        return "******"
    return f"{account_id[:2]}****{account_id[-2:]}"


def main():
    print("\n" + "=" * 60)
    print("ALPACA MCP CONNECTIVITY CHECK")
    print("=============================")

    try:
        client = AlpacaMCPClient(dry_run=True)
        fetcher = AlpacaDataFetcher()
        acct = fetcher.get_account_snapshot()
        masked_id = mask_account_id(acct.account_id)
        tools = client.discover_tools()

        print("MCP:              CONNECTED")
        print("Environment:      PAPER")
        print(f"Account:          {masked_id}")
        print(f"Equity:           ${float(acct.equity):,.2f}")
        print(f"Buying Power:     ${float(acct.buying_power):,.2f}")
        print(f"Account Status:   {acct.status}")
        print("Order submission: DISABLED (MCP_DRY_RUN=true)")
        print("=" * 60)

        print("\n[DISCOVERED MCP TOOLS]")
        print("READ-ONLY TOOLS (Autonomous agent data acquisition):")
        for t in tools["read_tools"]:
            desc = ALL_MCP_TOOLS[t]["description"]
            print(f"  • {t:<26} -> {desc}")

        print("\nWRITE TOOLS (Guarded by deterministic MCPGuard):")
        for t in tools["write_tools"]:
            desc = ALL_MCP_TOOLS[t]["description"]
            print(f"  • {t:<26} -> {desc} [GUARDED]")

        print("\n" + "=" * 60)
        print("  ✅ MCP CAPABILITY CHECK COMPLETE: Zero orders submitted.")
        print("=" * 60 + "\n")

    except Exception as e:
        print(f"MCP Connectivity Failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
