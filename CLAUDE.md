# bouch-registry

Bouch capability metaregistry served over MCP (FastMCP 3, streamable HTTP) and
as a static `/registry.json`. See README.md for the record format and surface.

## Invariants

- `registry/` is the only dataset. Never add a second copy (generated
  `registry.json` files are exports, not sources — do not commit one).
- Records are pointers. Do not copy native manifest content (versions,
  keywords, tool lists, packages) into records; the model forbids unknown
  fields — do not loosen `extra="forbid"` to make data fit.
- No machine-local paths in records: paths are relative to the source
  repository root. Checkout locations are passed to `verify-sources` on the
  command line only.
- Point only at committed files. Run `verify-sources` against real checkouts
  after changing pointers; an uncommitted file in a source repo is not a valid
  entrypoint yet.
- Do not encode live state (git status, branches, tool counts, running
  services, installed plugins).
- Keep the type set closed at the eight types unless a real record cannot be
  represented. Do not add types, domains or tools speculatively; every declared
  domain must have at least one record.
- Keep the MCP surface small and read-only. No recommendation, install or
  orchestration tools.

## Commands

```bash
uv sync --group dev
uv run pytest
uv run bouch-registry validate
uv run bouch-registry serve --port 8080
scripts/inspector-smoke.sh http://127.0.0.1:8080/mcp
```

Not deployed. Do not deploy, configure bouch.dev DNS or publish to the MCP
Registry without explicit approval.
