from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_ENDPOINT = "https://mcp.wisburg.com/mcp"
PROTOCOL_VERSION = "2025-03-26"


class McpHttpClient:
    def __init__(self, endpoint: str, api_key: str, timeout: int = 30) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout
        self.session_id: str | None = None
        self._next_id = 1

    def initialize(self) -> dict:
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "equity-research-radar", "version": "0.1.0"},
            },
        )
        self.notify("notifications/initialized", {})
        return result

    def request(self, method: str, params: dict | None = None) -> dict:
        request_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        response = self._post(payload)
        if response.get("error"):
            raise RuntimeError(f"MCP error for {method}: {response['error']}")
        return response.get("result", {})

    def notify(self, method: str, params: dict | None = None) -> None:
        self._post(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
            },
            allow_empty=True,
        )

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def _post(self, payload: dict, allow_empty: bool = False) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "EquityResearchRadar-MCP-Diagnostic/0.1",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                self.session_id = response.headers.get("Mcp-Session-Id") or self.session_id
                body = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} from MCP server: {body[:500]}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Could not reach MCP server: {exc}") from exc

        if not body.strip() and allow_empty:
            return {}
        return _decode_mcp_body(body)


def _decode_mcp_body(body: str) -> dict:
    stripped = body.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)

    messages: list[dict] = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        messages.append(json.loads(data))
    if not messages:
        raise RuntimeError(f"Unrecognized MCP response: {body[:500]}")
    return messages[-1]


def _tool_summary(tools: list[dict]) -> list[dict]:
    summaries = []
    for tool in tools:
        schema = tool.get("inputSchema") or {}
        summaries.append(
            {
                "name": tool.get("name"),
                "description": tool.get("description"),
                "required_inputs": schema.get("required", []),
                "input_fields": list((schema.get("properties") or {}).keys()),
            }
        )
    return summaries


def _content_preview(result: dict, max_chars: int = 4000) -> dict:
    previews: list[dict] = []
    for item in result.get("content", []):
        if item.get("type") != "text":
            previews.append({"type": item.get("type")})
            continue
        text = item.get("text", "")
        previews.append({"type": "text", "text": text[:max_chars]})
    return {
        "is_error": result.get("isError", False),
        "content": previews,
        "structured_content": result.get("structuredContent"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a Wisburg MCP server safely.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--query", help="Run sample research searches after listing capabilities.")
    parser.add_argument("--first", type=int, default=3)
    args = parser.parse_args()

    api_key = os.getenv("WISBURG_API_KEY", "").strip()
    if not api_key:
        print("WISBURG_API_KEY is not set.", file=sys.stderr)
        return 2

    client = McpHttpClient(args.endpoint, api_key, timeout=args.timeout)
    initialize = client.initialize()
    tools = client.request("tools/list").get("tools", [])

    try:
        resources = client.request("resources/list").get("resources", [])
    except RuntimeError as exc:
        resources = [{"note": str(exc)}]

    try:
        prompts = client.request("prompts/list").get("prompts", [])
    except RuntimeError as exc:
        prompts = [{"note": str(exc)}]

    output = {
        "server": initialize.get("serverInfo", {}),
        "protocol_version": initialize.get("protocolVersion"),
        "capabilities": initialize.get("capabilities", {}),
        "tools": _tool_summary(tools),
        "resources": resources,
        "prompts": prompts,
    }
    if args.query:
        output["sample_searches"] = {}
        for tool_name in (
            "list-company-reports",
            "list-earning-calls",
            "list-institutional-reports",
            "list-feed",
        ):
            result = client.call_tool(
                tool_name,
                {"query": args.query, "first": args.first},
            )
            output["sample_searches"][tool_name] = _content_preview(result)
    print(json.dumps(output, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
