import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from scripts import fund_mcp  # noqa: E402


class FundMcpProtocolTests(unittest.TestCase):
    def test_initialize_returns_tools_capability(self):
        response = fund_mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "unit-test", "version": "1.0.0"},
                },
            }
        )

        self.assertEqual(response["id"], 1)
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(response["result"]["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(response["result"]["serverInfo"]["name"], "fund-data")

    def test_tools_list_exposes_core_fund_tools(self):
        response = fund_mcp.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )

        tool_names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("fund_search", tool_names)
        self.assertIn("fund_nav_history", tool_names)
        self.assertIn("fund_sync", tool_names)
        self.assertIn("fund_coverage_report", tool_names)

    def test_call_fund_search_returns_text_and_structured_content(self):
        rows = [{"fund_code": "006600", "fund_name": "人保沪深300A"}]
        with patch.object(fund_mcp.fund_data, "search_funds", return_value=rows) as mock_search:
            response = fund_mcp.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "fund_search",
                        "arguments": {
                            "keyword": "沪深300",
                            "provider": "eastmoney",
                            "limit": 5,
                            "db": "/tmp/fund-mcp-test.sqlite",
                        },
                    },
                }
            )

        mock_search.assert_called_once_with(
            "沪深300",
            db_path="/tmp/fund-mcp-test.sqlite",
            provider="eastmoney",
        )
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["rows"], rows)
        self.assertEqual(json.loads(result["content"][0]["text"]), rows)

    def test_stdio_entrypoint_reads_newline_delimited_json_rpc(self):
        message = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "stdio-test", "version": "1.0.0"},
            },
        }
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "fund_mcp.py")],
            input=json.dumps(message) + "\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response["id"], 4)
        self.assertEqual(response["result"]["serverInfo"]["name"], "fund-data")


if __name__ == "__main__":
    unittest.main()
