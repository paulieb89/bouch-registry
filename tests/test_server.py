"""The MCP contract, exercised through real FastMCP clients (in-memory and streamable HTTP)."""

import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.exceptions import ToolError

from bouch_registry.server import create_http_app, create_server

EXPECTED_TOOLS = {"search_capabilities", "get_capability", "list_domains"}


@pytest.fixture
def mcp(registry):
    return create_server(registry)


async def test_tool_surface_is_small_and_read_only(mcp):
    async with Client(mcp) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.annotations.readOnlyHint is True
        assert tool.outputSchema is not None, f"{tool.name} should return structured output"


async def test_resource_surface(mcp):
    async with Client(mcp) as client:
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
    assert [str(r.uri) for r in resources] == ["bouch://registry"]
    assert {t.uriTemplate for t in templates} == {"bouch://domains/{domain}", "bouch://capabilities/dev.bouch/{name}"}


async def test_search_tool_returns_structured_hits(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_capabilities", {"query": "electronic sound design synthesis", "domain": "audio"}
        )
    hits = result.structured_content["hits"]
    assert hits[0]["id"] in {"dev.bouch/audio-agent-workbench-v2", "dev.bouch/production-technique-reference", "dev.bouch/reaper-agent-lab"}
    assert hits[0]["resource_uri"].startswith("bouch://capabilities/dev.bouch/")


async def test_get_capability_tool(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("get_capability", {"id": "dev.bouch/bouch-agent-core"})
        with pytest.raises(ToolError, match="search_capabilities"):
            await client.call_tool("get_capability", {"id": "dev.bouch/missing"})
    record = result.structured_content
    assert record["native"] == {"spec": "agent-plugins", "manifest": "plugin.json", "note": None}
    assert record["source"]["repository"] == "bouch-agent-core"


async def test_unknown_domain_is_a_tool_error(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="Known domains"):
            await client.call_tool("search_capabilities", {"domain": "cooking"})


async def test_list_domains_tool(mcp, registry):
    async with Client(mcp) as client:
        result = await client.call_tool("list_domains", {})
    domains = {d["id"]: d for d in result.structured_content["domains"]}
    assert set(domains) == {d.id for d in registry.domains}
    assert "dev.bouch/reaper-mcp" in domains["audio"]["capability_ids"]


async def test_resources_read_the_same_records(mcp, registry):
    async with Client(mcp) as client:
        full = json.loads((await client.read_resource("bouch://registry"))[0].text)
        audio = json.loads((await client.read_resource("bouch://domains/audio"))[0].text)
        one = json.loads((await client.read_resource("bouch://capabilities/dev.bouch/reaper-mcp"))[0].text)
    assert full == registry.to_json()
    assert {c["id"] for c in audio["capabilities"]} == {c.id for c in registry.in_domain("audio")}
    assert one in full["capabilities"]


# --- streamable HTTP, the intended deployment transport ----------------------


@pytest.fixture(scope="module")
def http_url():
    from bouch_registry.store import load_registry

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_http_app(load_registry()), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


async def test_http_mcp_and_static_json_are_one_dataset(http_url, registry):
    async with Client(f"{http_url}/mcp") as client:
        tools = await client.list_tools()
        via_mcp = json.loads((await client.read_resource("bouch://registry"))[0].text)
        search = await client.call_tool("search_capabilities", {"query": "reaper"})
    static = httpx.get(f"{http_url}/registry.json").json()
    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert static == via_mcp == registry.to_json()
    assert search.structured_content["hits"]


def test_http_guard_holds_get_open_and_rejects_delete(http_url):
    with httpx.stream("GET", f"{http_url}/mcp", timeout=5) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
    assert httpx.delete(f"{http_url}/mcp").status_code == 405


def test_health_reports_the_dataset_it_loaded(http_url, registry):
    health = httpx.get(f"{http_url}/health").json()
    assert health["status"] == "ok"
    assert health["capabilities"] == len(registry.capabilities)
    assert health["domains"] == len(registry.domains)
