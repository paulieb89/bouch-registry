"""Bouch Registry MCP server.

Three read-only tools and four resource shapes over one loaded `Registry`.
The same object backs `/registry.json`, so the MCP and static views cannot
diverge. The `bouch://source/...` template reads a record's declared
entrypoints from its canonical remote source at read time (see `remote`).
"""

from __future__ import annotations

import json
import os
from typing import Annotated

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.resources import ResourceContent, ResourceResult
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from .model import Capability, CapabilityType
from .remote import SourceError, read_declared
from .store import Registry, capability_uri, domain_uri

READ_ONLY = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}

INSTRUCTIONS = """\
Bouch capability registry. Consult it before treating a domain or environment \
as capability-empty: it lists what Bouch already knows and has built (Agent \
Plugins, Agent Skills, MCP servers, workbenches, evidence repositories, \
observability tools, routing pointers).

Records are pointers, not the knowledge itself. Each record names its source \
repository, a native manifest where an open spec applies (plugin.json, \
SKILL.md, server.json, A2A agent card) and entrypoint paths relative to that \
repository. Read those authoritative sources before acting. The registry holds \
no live state: versions, tool lists, git state and runtime health must be \
derived from the systems themselves.

Typical flow: list_domains → search_capabilities (query and/or domain) → \
get_capability for the records worth following → read the entrypoints you \
need. For a record with a published source, read a declared entrypoint (or \
repo-relative native manifest) as the resource \
bouch://source/<id>/<path>, e.g. bouch://source/dev.bouch/<name>/README.md. \
It is fetched from the source repository at the record's source.ref (else the \
remote HEAD); _meta carries the commit and git blob id served. Only declared \
paths are readable, one at a time: read what the task needs, not the whole \
package. The registry does not recommend or install anything; you decide \
what to use."""


class CapabilityHit(BaseModel):
    id: str
    type: CapabilityType
    title: str
    summary: str
    domains: list[str]
    lifecycle: str
    score: float = Field(description="Deterministic relevance score; 0 when browsing without a query.")
    matched_terms: list[str]
    resource_uri: str


class SearchResult(BaseModel):
    query: str
    domain: str | None
    type: CapabilityType | None
    hits: list[CapabilityHit]


class DomainSummary(BaseModel):
    id: str
    title: str
    summary: str
    capability_ids: list[str]
    resource_uri: str


class DomainList(BaseModel):
    domains: list[DomainSummary]


def create_server(registry: Registry, transport: httpx.AsyncBaseTransport | None = None) -> FastMCP:
    """`transport` replaces the network for remote source reads (tests only)."""
    mcp = FastMCP("bouch-registry", instructions=INSTRUCTIONS)

    def _require_domain(domain: str) -> None:
        if registry.domain(domain) is None:
            known = ", ".join(d.id for d in registry.domains)
            raise ToolError(f"Unknown domain {domain!r}. Known domains: {known}.")

    @mcp.tool(annotations=READ_ONLY)
    def search_capabilities(
        query: Annotated[str, Field(description="Free text, e.g. 'sound design synthesis'. Empty to browse.")] = "",
        domain: Annotated[str | None, Field(description="Restrict to a domain id from list_domains.")] = None,
        type: Annotated[CapabilityType | None, Field(description="Restrict to one capability type.")] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> SearchResult:
        """Find Bouch capabilities by deterministic keyword match over titles, tags, domains and routing summaries.

        Returns compact hits ranked by score. Follow up with get_capability for
        the native manifest, source repository and entrypoints of a hit.
        """
        if domain is not None:
            _require_domain(domain)
        hits = registry.search(query, domain=domain, type=type, limit=limit)
        return SearchResult(
            query=query,
            domain=domain,
            type=type,
            hits=[
                CapabilityHit(
                    id=h.capability.id,
                    type=h.capability.type,
                    title=h.capability.title,
                    summary=h.capability.summary,
                    domains=h.capability.domains,
                    lifecycle=h.capability.lifecycle,
                    score=h.score,
                    matched_terms=list(h.matched_terms),
                    resource_uri=capability_uri(h.capability.id),
                )
                for h in hits
            ],
        )

    @mcp.tool(annotations=READ_ONLY)
    def get_capability(
        id: Annotated[str, Field(description="Registry id, e.g. 'dev.bouch/reaper-mcp'.")],
    ) -> Capability:
        """Get one full capability record: source repository, native spec and manifest pointer, entrypoints, related ids.

        Entrypoints of a record with a published source.url can be read as
        resources: bouch://source/<id>/<entrypoint path>.
        """
        cap = registry.get(id)
        if cap is None:
            raise ToolError(f"No capability with id {id!r}. Ids look like 'dev.bouch/<name>'; use search_capabilities to find one.")
        return cap

    @mcp.tool(annotations=READ_ONLY)
    def list_domains() -> DomainList:
        """List registry domains with a short routing summary and the capability ids in each."""
        return DomainList(
            domains=[
                DomainSummary(
                    id=d.id,
                    title=d.title,
                    summary=d.summary,
                    capability_ids=[c.id for c in registry.in_domain(d.id)],
                    resource_uri=domain_uri(d.id),
                )
                for d in registry.domains
            ]
        )

    @mcp.resource(
        "bouch://registry",
        name="Bouch registry",
        description="The complete registry: native spec table, domains and every capability record. Identical to /registry.json.",
        mime_type="application/json",
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    def registry_resource() -> str:
        return _dumps(registry.to_json())

    @mcp.resource(
        "bouch://domains/{domain}",
        name="Domain view",
        description="A domain's routing summary with the full records of its capabilities.",
        mime_type="application/json",
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    def domain_resource(domain: str) -> str:
        d = registry.domain(domain)
        if d is None:
            raise ResourceError(f"Unknown domain {domain!r}")
        return _dumps(
            {
                **d.model_dump(mode="json"),
                "capabilities": [c.model_dump(mode="json", exclude_none=True) for c in registry.in_domain(domain)],
            }
        )

    @mcp.resource(
        "bouch://capabilities/dev.bouch/{name}",
        name="Capability record",
        description="One capability record, addressed by its registry id (bouch://capabilities/dev.bouch/<name>).",
        mime_type="application/json",
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    def capability_resource(name: str) -> str:
        cap = registry.get(f"dev.bouch/{name}")
        if cap is None:
            raise ResourceError(f"Unknown capability dev.bouch/{name}")
        return _dumps(cap.model_dump(mode="json", exclude_none=True))

    @mcp.resource(
        "bouch://source/dev.bouch/{name}/{path*}",
        name="Declared entrypoint",
        description=(
            "One declared entrypoint (or repo-relative native manifest) of a capability, read from its canonical "
            "remote source at the record's source.ref, else the remote HEAD. Only declared paths resolve; missing "
            "or stale pointers and unpublished sources are errors, never a fallback. _meta reports repository, "
            "ref, commit and git blob id. Relative links inside a document resolve against its directory and are "
            "readable when the target is itself declared."
        ),
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    async def source_resource(name: str, path: str) -> ResourceResult:
        cap = registry.get(f"dev.bouch/{name}")
        if cap is None:
            raise ResourceError(f"Unknown capability dev.bouch/{name}")
        try:
            async with httpx.AsyncClient(transport=transport) as client:
                doc = await read_declared(cap, path, client)
        except SourceError as exc:
            raise ResourceError(str(exc)) from None
        return ResourceResult([ResourceContent(doc.text, mime_type=doc.mime_type, meta=doc.provenance)])

    @mcp.custom_route("/registry.json", methods=["GET"])
    async def registry_json(request):
        return JSONResponse(registry.to_json())

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request):
        # Counts are read off the loaded registry, so a deployment that served a
        # truncated or stale dataset would show it here rather than report "ok".
        return JSONResponse(
            {
                "status": "ok",
                "server": "bouch-registry",
                "capabilities": len(registry.capabilities),
                "domains": len(registry.domains),
            }
        )

    return mcp


def _dumps(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# HTTP entrypoint
# ---------------------------------------------------------------------------
# _HttpGuard and _AcceptNormalizer are the fleet's proven claude.ai
# compatibility shims, copied from govuk-mcp (reference: uk-legal-mcp gateway).


class _HttpGuard:
    """Return a held-open SSE stream for GET /mcp; 405 for DELETE /mcp.

    claude.ai opens GET /mcp as an SSE stream before POSTing protocol messages.
    A stateless FastMCP app only routes POST, so GET would 405 and claude.ai
    treats the connector as broken. DELETE is rejected: stateless servers have
    no sessions to end.
    """

    def __init__(self, app, mcp_path: bytes = b"/mcp"):
        self.app = app
        self._mcp_path = mcp_path.rstrip(b"/")

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "").rstrip("/").encode()
            method = scope.get("method", "").upper().encode()
            if path == self._mcp_path:
                if method == b"GET":
                    await send({"type": "http.response.start", "status": 200, "headers": [
                        (b"content-type", b"text/event-stream"),
                        (b"cache-control", b"no-cache"),
                        (b"connection", b"keep-alive"),
                    ]})
                    await send({"type": "http.response.body", "body": b"", "more_body": True})
                    while True:
                        event = await receive()
                        if event["type"] == "http.disconnect":
                            break
                    return
                if method == b"DELETE":
                    from starlette.responses import Response
                    await Response("Method Not Allowed", status_code=405, headers={"Allow": "POST"})(scope, receive, send)
                    return
        await self.app(scope, receive, send)


class _AcceptNormalizer:
    """Stamp the MCP-spec Accept value on /mcp only, so json_response=True never 406s."""

    def __init__(self, app, mcp_path: bytes = b"/mcp"):
        self.app = app
        self._mcp_path = mcp_path.rstrip(b"/")

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").rstrip("/").encode() == self._mcp_path:
            headers = [
                (b"accept", b"application/json, text/event-stream") if name.lower() == b"accept" else (name, value)
                for name, value in scope.get("headers", [])
            ]
            scope = {**scope, "headers": headers}
        await self.app(scope, receive, send)


def create_http_app(registry: Registry):
    from fastmcp.server.http import create_streamable_http_app

    app = create_streamable_http_app(
        create_server(registry),
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )
    return _HttpGuard(_AcceptNormalizer(app))


def serve(registry: Registry, host: str = "127.0.0.1", port: int | None = None) -> None:
    import uvicorn

    uvicorn.run(
        create_http_app(registry),
        host=host,
        port=port or int(os.environ.get("PORT", "8080")),
        forwarded_allow_ips="*",
        proxy_headers=True,
        lifespan="on",
        log_level="info",
    )
