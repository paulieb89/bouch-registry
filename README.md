# bouch-registry

The Bouch capability registry: one remote place for an agent to discover what
Bouch already knows and has built before treating a domain or environment as
capability-empty.

It is a **metaregistry**. Each record identifies a capability, names its
versioned source repository, points at the native manifest where an open
specification exists, and lists the entrypoint documents to read next. It is
not a package manager, installer, orchestrator or copy of native manifests, and
it holds no live state (versions, tool counts, git or runtime status) — derive
those from the systems themselves.

Intended service identity: `https://registry.bouch.dev/` with MCP at `/mcp`
and the static registry at `/registry.json`. Not deployed yet.

## Data

`registry/` is the only dataset. The MCP tools, MCP resources,
`/registry.json` and `bouch-registry export` are all rendered from it.

```
registry/
├── domains.json          # closed domain vocabulary with routing summaries
└── entries/<name>.json   # one record per capability, id dev.bouch/<name>
```

The model (`bouch_registry/model.py`) is the schema. Records reject unknown
fields, so manifest content such as `version` or `keywords` cannot creep in.

| field | meaning |
|---|---|
| `id` | `dev.bouch/<name>`; file name must be `<name>.json` |
| `type` | one of the eight types below |
| `title`, `summary` | summary says *when to consult this*, not what a manifest already says |
| `domains`, `tags` | lowercase slugs; domains must be declared in `domains.json` |
| `lifecycle` | `experimental`, `active`, `frozen` (requires `source.ref`) or `deprecated` |
| `source` | `repository` name, `url` (null if unpublished), `ref` (durable tag), `note` |
| `native` | only for native types: `spec`, `manifest` (repo-relative path or https URL, or null with a `note`) |
| `entrypoints` | `role` (`contract`, `knowledge`, `skill`, `tool`, `evidence`), repo-relative `path`, `title` |
| `related` | other registry ids |

### Types

| type | native spec | manifest |
|---|---|---|
| `agent-plugin` | Agent Plugins 1.0 | `plugin.json` |
| `agent-skill` | Agent Skills | `SKILL.md` |
| `mcp-server` | MCP Registry `server.json` | `server.json` |
| `remote-agent` | A2A Agent Card | `agent-card.json` |
| `reference-workbench` | none (Bouch-specific) | — |
| `evidence-repository` | none (Bouch-specific) | — |
| `observability-tool` | none (Bouch-specific) | — |
| `routing-pointer` | none (Bouch-specific) | — |

`agent-skill` means an Agent Skills `SKILL.md`. It is unrelated to an A2A
`AgentSkill`, which is a capability tag inside an Agent Card.

The type set and the native/Bouch-specific split come from the
agent-enumeration-lab finding `bouch-registry-standards-fit`.

### Adding or changing a record

1. Edit `registry/entries/<name>.json`. Point only at files committed in the
   source repository (at `source.ref` if set). Never use local checkout paths.
2. `uv run bouch-registry validate`
3. Check the pointers against real checkouts (paths are passed on the command
   line, never stored):
   `uv run bouch-registry verify-sources --checkout <repository>=<path> ...`
4. `uv run pytest`

## MCP surface

Tools (read-only, structured output):

- `search_capabilities(query, domain?, type?, limit?)` — deterministic
  weighted keyword search with IDF; empty query browses.
- `get_capability(id)` — the full record.
- `list_domains()` — domains, routing summaries and member ids.

Resources:

- `bouch://registry` — the whole registry (identical to `/registry.json`)
- `bouch://domains/{domain}` — a domain with its full records
- `bouch://capabilities/dev.bouch/{name}` — one record

HTTP routes: `/mcp` (streamable HTTP, stateless, JSON responses, with the
fleet's claude.ai GET/DELETE guard), `/registry.json`, `/health`.

## Commands

```bash
uv sync --group dev
uv run bouch-registry serve --port 8080       # MCP + /registry.json
uv run bouch-registry validate
uv run bouch-registry export registry.json    # static copy, e.g. for static hosting
uv run pytest                                 # offline: model, search, portability, MCP in-memory + HTTP
scripts/inspector-smoke.sh http://127.0.0.1:8080/mcp   # independent client (MCP Inspector CLI)
```
