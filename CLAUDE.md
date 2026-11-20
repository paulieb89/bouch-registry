# bouch-registry

The Bouch capability catalogue: pointer records in `registry/`, a pydantic schema and a local CLI. See README.md for the record format. The hosted MCP service has been retired; don't reintroduce a server without a demonstrated need.

## Invariants

- `registry/` is the only dataset. Don't commit generated exports (`registry.json`).
- Records are pointers. Don't copy native manifest content (versions, keywords, tool lists, packages) into records; the model forbids unknown fields — don't loosen `extra="forbid"` to make data fit.
- No machine-local paths in records: paths are relative to the source repository root. Checkout locations go to `verify-sources` on the command line only.
- Point only at committed files. Run `verify-sources` against real checkouts after changing pointers.
- Don't encode live state (git status, branches, tool counts, running services, installed plugins).
- Keep the type set closed at the eight types unless a real record can't be represented. Don't add types or domains speculatively; every declared domain must have at least one record.
- Plugins install from the `bouch-plugins` marketplace; a plugin record here is a pointer to it, not a second install route.

## Commands

```bash
uv sync --group dev
uv run pytest
uv run bouch-registry validate
uv run bouch-registry search "<query>"
```
