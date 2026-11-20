# bouch-registry

The Bouch capability catalogue: one record per capability (plugins, MCP servers, workbenches, evidence repositories, observability tools), so an agent can find what Bouch already has before building something.

Records are pointers. Each names the capability's versioned source repository, its native manifest where an open spec exists, and the entrypoint documents to read next. It holds no live state (versions, tool counts, git or runtime status); derive those from the systems themselves.

Plugins are also listed in the [`bouch-plugins`](https://github.com/paulieb89/bouch-plugins) marketplace, which Claude Code, Codex and ChatGPT read natively; install them from there. This catalogue covers what a marketplace can't: workbenches, evidence repositories and standalone servers.

> The hosted MCP service at `registry.bouch.dev` has been retired. Read the records from this repository (`registry/entries/`), a local checkout, or the CLI below.

## Data

```
registry/
├── domains.json          # closed domain vocabulary with routing summaries
└── entries/<name>.json   # one record per capability, id dev.bouch/<name>
```

`bouch_registry/model.py` is the schema. Records reject unknown fields, so manifest content such as `version` or `keywords` can't creep in.

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

## Commands

```bash
uv sync --group dev
uv run bouch-registry validate                       # default command
uv run bouch-registry search "uk due diligence"      # --domain, --type, --limit, --json
uv run bouch-registry export registry.json           # whole catalogue as one JSON document
uv run bouch-registry verify-sources --checkout <repository>=<path> ...
uv run pytest
```

## Adding or changing a record

1. Edit `registry/entries/<name>.json`. Point only at files committed in the source repository (at `source.ref` if set). Never use local checkout paths.
2. `uv run bouch-registry validate`
3. `uv run bouch-registry verify-sources --checkout <repository>=<path> ...` against real checkouts.
4. `uv run pytest`, then open a PR. CI runs the same checks.
