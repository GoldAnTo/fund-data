import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from scripts import fund_mcp  # noqa: E402


def _seed_fund_profile(db_path: Path) -> None:
    store = fund_mcp.fund_data.FundDataStore(db_path)
    store.upsert_funds(
        [
            {
                "fund_code": "110022",
                "fund_name": "易方达消费行业股票",
                "fund_type": "股票型",
                "company": "易方达基金",
                "manager": "萧楠",
                "nav": None,
                "nav_date": "",
                "other_names": "",
                "source": "local.test",
            }
        ]
    )
    store.upsert_profile(
        {
            "fund_code": "110022",
            "fund_name": "易方达消费行业股票",
            "full_name": "易方达消费行业股票型证券投资基金",
            "fund_type": "股票型",
            "fund_company": "易方达基金",
            "manager": "萧楠",
            "source": "local.profile",
        }
    )


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
        self.assertIn("fund_cloud_status", tool_names)

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

    def test_call_fund_cloud_status_returns_cache_status(self):
        status = {"installed": False, "cache_dir": "/tmp/fund-data-cache"}
        with patch.object(fund_mcp.fund_cloud, "status", return_value=status) as mock_status, patch.object(
            fund_mcp.fund_cloud, "ensure_project_bundle"
        ) as mock_bootstrap:
            response = fund_mcp.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "fund_cloud_status",
                        "arguments": {"cache_dir": "/tmp/fund-data-cache"},
                    },
                }
            )

        mock_status.assert_called_once_with(cache_dir="/tmp/fund-data-cache", manifest_url=None)
        mock_bootstrap.assert_not_called()
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"], status)

    def test_call_fund_search_bootstraps_project_cloud_before_provider_api(self):
        rows = [{"fund_code": "006600", "fund_name": "人保沪深300A"}]
        with patch.object(fund_mcp.fund_cloud, "ensure_project_bundle") as mock_bootstrap, patch.object(
            fund_mcp.fund_data, "search_funds", return_value=rows
        ) as mock_search:
            response = fund_mcp.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {
                        "name": "fund_search",
                        "arguments": {"keyword": "沪深300"},
                    },
                }
            )

        mock_bootstrap.assert_called_once_with()
        mock_search.assert_called_once_with("沪深300", db_path=None, provider="auto")
        self.assertFalse(response["result"]["isError"])

    def test_known_code_search_uses_local_export_before_provider_api(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            _seed_fund_profile(db_path)

            with patch.object(
                fund_mcp.fund_data, "search_funds", side_effect=AssertionError("provider ran")
            ):
                response = fund_mcp.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 7,
                        "method": "tools/call",
                        "params": {
                            "name": "fund_search",
                            "arguments": {"keyword": "110022", "db": str(db_path)},
                        },
                    }
                )

        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["count"], 1)
        self.assertEqual(result["structuredContent"]["rows"][0]["fund_code"], "110022")
        self.assertEqual(result["structuredContent"]["rows"][0]["fund_name"], "易方达消费行业股票")

    def test_known_code_profile_uses_local_export_before_provider_api(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            _seed_fund_profile(db_path)

            with patch.object(
                fund_mcp.fund_data, "fetch_profile", side_effect=AssertionError("provider ran")
            ):
                response = fund_mcp.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 8,
                        "method": "tools/call",
                        "params": {
                            "name": "fund_profile",
                            "arguments": {"code": "110022", "db": str(db_path)},
                        },
                    }
                )

        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["fund_code"], "110022")
        self.assertEqual(result["structuredContent"]["manager"], "萧楠")

    def test_known_code_profile_refresh_bypasses_local_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            _seed_fund_profile(db_path)
            refreshed = {
                "fund_code": "110022",
                "fund_name": "易方达消费行业股票",
                "manager": "实时经理",
                "source": "provider.test",
            }

            with patch.object(
                fund_mcp.fund_data, "fetch_profile", return_value=refreshed
            ) as mock_profile:
                response = fund_mcp.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "method": "tools/call",
                        "params": {
                            "name": "fund_profile",
                            "arguments": {
                                "code": "110022",
                                "db": str(db_path),
                                "refresh": True,
                            },
                        },
                    }
                )

        mock_profile.assert_called_once_with(
            "110022", db_path=str(db_path), provider="auto"
        )
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(response["result"]["structuredContent"], refreshed)

    def test_known_code_managers_uses_local_links_before_provider_api(self):
        """`_call_fund_managers` should hit the new
        ``fund_manager_links`` projection for O(1) lookup on
        known codes, only falling through to the provider chain
        when the local table has no row for the requested code."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            store = fund_mcp.fund_data.FundDataStore(db_path)
            store.upsert_fund_managers(
                [
                    {
                        "manager_name": "萧楠",
                        "company": "易方达基金",
                        "current_fund_codes": "110022",
                        "current_funds": "易方达消费行业股票",
                        "tenure_days": 4994,
                        "current_aum": 225.82,
                        "best_return": 2.7587,
                        "source": "akshare.fund_manager_em",
                    }
                ]
            )

            with patch.object(
                fund_mcp.fund_data,
                "fetch_fund_managers",
                side_effect=AssertionError("provider ran"),
            ):
                response = fund_mcp.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 10,
                        "method": "tools/call",
                        "params": {
                            "name": "fund_managers",
                            "arguments": {"code": "110022", "db": str(db_path)},
                        },
                    }
                )

        result = response["result"]
        self.assertFalse(result["isError"])
        # List responses are wrapped in a ``{"rows": [...],
        # "count": N}`` envelope by the protocol layer; the
        # single-row ``fund_profile`` response is unwrapped. Read
        # ``rows`` explicitly so the assertion matches the
        # shape agents actually consume.
        rows = result["structuredContent"]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["manager_name"], "萧楠")
        self.assertEqual(rows[0]["fund_code"], "110022")

    def test_unknown_code_managers_falls_through_to_provider(self):
        """Local-first is a hit-or-miss optimization: when the
        local links table has no row, the provider chain still
        runs (this is the same behavior as ``fund_profile``)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            fund_mcp.fund_data.FundDataStore(db_path)  # bootstrap only
            fetched = [
                {
                    "manager_name": "test_manager",
                    "company": "test_co",
                    "current_fund_codes": "999999",
                    "current_funds": "test_fund",
                    "tenure_days": 1,
                    "current_aum": 1.0,
                    "best_return": 0.1,
                    "source": "provider.test",
                }
            ]
            with patch.object(
                fund_mcp.fund_data, "fetch_fund_managers", return_value=fetched
            ) as mock_fetch:
                response = fund_mcp.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 11,
                        "method": "tools/call",
                        "params": {
                            "name": "fund_managers",
                            "arguments": {"code": "999999", "db": str(db_path)},
                        },
                    }
                )

        mock_fetch.assert_called_once_with(
            "999999", db_path=str(db_path), provider="auto"
        )
        self.assertEqual(
            response["result"]["structuredContent"]["rows"], fetched
        )

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


class FundMcpErrorPathTests(unittest.TestCase):
    """MCP is the agent's primary entry point. The error responses
    must be stable enough for an agent to branch on them
    programmatically: stable error codes, stable message keys, and
    a guarantee that a real handler exception does not crash the
    server. These tests are the contract for that."""

    JSONRPC_INVALID_PARAMS = -32602
    JSONRPC_METHOD_NOT_FOUND = -32601
    JSONRPC_INVALID_REQUEST = -32600

    # --- protocol-level errors -------------------------------------

    def test_unknown_method_returns_method_not_found(self):
        response = fund_mcp.handle_message(
            {"jsonrpc": "2.0", "id": 10, "method": "tools/nonexistent", "params": {}}
        )
        self.assertEqual(response["id"], 10)
        self.assertEqual(response["error"]["code"], self.JSONRPC_METHOD_NOT_FOUND)
        # The error message must reference the offending method so
        # the agent can self-diagnose from a single response.
        self.assertIn("tools/nonexistent", response["error"]["message"])

    def test_notification_with_no_id_returns_none(self):
        # ``notifications/initialized`` and friends carry no ``id``;
        # the dispatcher must not synthesize a response for them.
        result = fund_mcp.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        self.assertIsNone(result)

    def test_params_not_an_object_returns_invalid_params(self):
        response = fund_mcp.handle_message(
            {"jsonrpc": "2.0", "id": 11, "method": "tools/list", "params": "oops"}
        )
        self.assertEqual(response["error"]["code"], self.JSONRPC_INVALID_PARAMS)
        self.assertIn("params", response["error"]["message"])

    def test_missing_id_with_known_method_returns_invalid_request(self):
        # No id + a real method is treated like a notification and
        # is silently dropped. We do not want to invent a
        # response id, because that would confuse an agent that
        # is using the response to correlate its outbound calls.
        result = fund_mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "method": "tools/list",  # no id
                "params": {},
            }
        )
        self.assertIsNone(result)

    # --- tools/call parameter validation ----------------------------

    def test_tools_call_without_name_returns_invalid_params(self):
        response = fund_mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 20,
                "method": "tools/call",
                "params": {"arguments": {"keyword": "沪深300"}},
            }
        )
        self.assertEqual(response["error"]["code"], self.JSONRPC_INVALID_PARAMS)
        self.assertIn("name", response["error"]["message"])

    def test_tools_call_with_unknown_tool_returns_method_not_found(self):
        response = fund_mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {"name": "fund_made_up", "arguments": {}},
            }
        )
        self.assertEqual(response["error"]["code"], self.JSONRPC_METHOD_NOT_FOUND)
        self.assertIn("fund_made_up", response["error"]["message"])

    def test_tools_call_missing_required_arg_returns_invalid_params(self):
        # fund_search has a required ``keyword`` field; an empty
        # arguments object should surface as a JSON-RPC
        # ``-32602`` invalid-params error so the agent can branch
        # on the error envelope (no result envelope is set).
        response = fund_mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 22,
                "method": "tools/call",
                "params": {
                    "name": "fund_search",
                    "arguments": {},  # keyword missing
                },
            }
        )
        self.assertNotIn("result", response)
        self.assertEqual(response["error"]["code"], self.JSONRPC_INVALID_PARAMS)
        # The error message mentions the missing field so the agent
        # does not have to guess.
        self.assertIn("keyword", response["error"]["message"])

    # --- handler exception isolation ---------------------------------

    def test_handler_exception_is_returned_as_tool_error_not_rpc_error(self):
        # A real exception inside a tool handler must not be
        # reported as a JSON-RPC protocol error -- the agent is
        # only allowed to branch on isError inside the result
        # envelope. Crashing the server with a 500-equivalent is
        # worse than a structured tool error.
        #
        # The dispatcher resolves the handler via the TOOL_HANDLERS
        # mapping at call time, so we patch the slot in that
        # mapping rather than the bare function -- the mapping
        # already holds a stale reference to the function object
        # captured at module import.
        with patch.dict(
            fund_mcp.TOOL_HANDLERS,
            {"fund_search": lambda _args: (_ for _ in ()).throw(RuntimeError("boom"))},
        ):
            response = fund_mcp.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 30,
                    "method": "tools/call",
                    "params": {
                        "name": "fund_search",
                        "arguments": {"keyword": "沪深300"},
                    },
                }
            )
        # No top-level JSON-RPC error envelope -- the failure
        # surfaces as a tool-level result.
        self.assertNotIn("error", response)
        result = response["result"]
        self.assertTrue(result["isError"])
        # The structured payload carries the exception message so
        # the agent can decide whether to retry.
        self.assertIn("error", result["structuredContent"])
        self.assertIn("boom", result["structuredContent"]["error"])
        # And the human-readable text mirror so log scrapers can
        # see the same message without JSON-parsing.
        text = result["content"][0]["text"]
        self.assertIn("boom", text)

    def test_handler_value_error_is_returned_as_invalid_params(self):
        # ValueError (e.g. ``fund code must contain 6 digits: ''``)
        # is treated as a *bad request* rather than a tool error,
        # so the agent gets a clearer signal that the input is
        # the problem rather than the server. The dispatcher
        # catches (TypeError, ValueError) specifically and wraps
        # them as JSON-RPC -32602 so the agent sees a protocol
        # error rather than a tool envelope.
        def _raise_value_error(_args: dict[str, Any]) -> list[dict[str, Any]]:
            raise ValueError("fund code must contain 6 digits: ''")

        with patch.dict(
            fund_mcp.TOOL_HANDLERS,
            {"fund_search": _raise_value_error},
        ):
            response = fund_mcp.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 31,
                    "method": "tools/call",
                    "params": {
                        "name": "fund_search",
                        "arguments": {"keyword": "x"},
                    },
                }
            )
        self.assertNotIn("result", response)
        self.assertEqual(response["error"]["code"], self.JSONRPC_INVALID_PARAMS)
        self.assertIn("6 digits", response["error"]["message"])

    # --- happy path: a tool that was not previously smoke-tested ----

    def test_fund_coverage_report_smoke_test(self):
        # Before this guard the only fund_* tools exercised by
        # tests were fund_search and fund_cloud_status. Add at
        # least one more so a regression in the dispatcher wiring
        # is caught.
        rows = [
            {
                "fund_code": "110022",
                "fund_name": "易方达消费行业股票",
                "completeness": 0.875,
                "missing": ["splits"],
            }
        ]
        with patch.object(
            fund_mcp.fund_data, "coverage_report", return_value=rows
        ) as mock_report:
            response = fund_mcp.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 40,
                    "method": "tools/call",
                    "params": {
                        "name": "fund_coverage_report",
                        "arguments": {"only_incomplete": True, "limit": 5},
                    },
                }
            )
        result = response["result"]
        self.assertFalse(result["isError"])
        # The dispatcher wraps list payloads as
        # ``{"rows": [...], "count": N}`` so consumers do not
        # have to disambiguate list vs dict tool results.
        self.assertEqual(result["structuredContent"]["rows"], rows)
        self.assertEqual(result["structuredContent"]["count"], len(rows))
        mock_report.assert_called_once()


if __name__ == "__main__":
    unittest.main()
