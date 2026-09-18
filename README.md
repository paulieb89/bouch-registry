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

Service identity: `https://registry.bouch.dev/` — `/health`, `/registry.json`
and MCP at `/mcp`. `https://bouch-registry.fly.dev/` serves the same app and is
the origin the custom domain points at.

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
   and, for published sources, against the remote exactly as MCP clients
   read them: `uv run bouch-registry verify-sources --remote`
4. `uv run pytest`

## MCP surface

Tools (read-only, structured output):

- `search_capabilities(query, domain?, type?, limit?)` — deterministic
  weighted keyword search with IDF; empty query browses.
- `get_capability(id)` — the full record.
- `list_domains()` — domains, routing summaries and member ids.
- `read_capability_entrypoint(capability_id, entrypoint)` — one declared
  entrypoint read from the canonical remote source; returns `content`,
  `mime_type`, `repository`, `ref`, `commit`, `path` and `git_blob`. The tool
  counterpart of `bouch://source/...` for tool-only clients (e.g. ChatGPT).

Resources:

- `bouch://registry` — the whole registry (identical to `/registry.json`)
- `bouch://domains/{domain}` — a domain with its full records
- `bouch://capabilities/dev.bouch/{name}` — one record
- `bouch://source/dev.bouch/{name}/{path*}` — one declared entrypoint (or
  repo-relative native manifest) read from the record's canonical remote
  source, e.g. `bouch://source/dev.bouch/audio/skills/electronic-production/SKILL.md`

### Reading entrypoints remotely

A consumer without the source checked out can read what a record declares,
one document at a time: resource-capable clients through `bouch://source/...`,
tool-only clients through `read_capability_entrypoint`. Both call the same
resolver (`bouch_registry/remote.py`), so they share one allowlist, ref
resolution, integrity report and failure behaviour. The registry stores no
content: each read follows the pointer at request time.

- **Only declared paths.** Entrypoint paths and a repo-relative
  `native.manifest`; anything else is refused before any network call. To make
  a document reachable (for example a reference a Skill routes to), declare it
  as an entrypoint. `verify-sources` enforces this for records with a
  published source: every repo-relative file link in a declared Markdown
  entrypoint must itself be declared (external URLs, anchors, code and
  directory links are exempt).
- **Pinned to the source.** `source.ref` is resolved to a commit from the
  repository's own git ref advertisement (annotated tags are peeled); a record
  without a ref resolves the remote `HEAD`. The file is then fetched by commit
  id. Only `github.com` sources are supported.
- **Checkable.** Provenance — `repository`, `ref`, `commit`, `path` and
  `git_blob` — is in the resource content's `_meta` and in the tool's
  structured output; `git_blob` equals `git rev-parse <commit>:<path>` in any
  clone.
- **No fallback.** An unpublished source (`source.url` null), a missing ref, a
  path absent at that commit, a non-UTF-8 file or one over 512 KiB is a
  resource error naming the cause. Nothing falls back to a local file, another
  ref or a cached copy.

HTTP routes: `/mcp` (streamable HTTP, stateless, JSON responses, with the
fleet's claude.ai GET/DELETE guard), `/registry.json`, `/health`.

## Commands

```bash
uv sync --group dev
uv run bouch-registry serve --port 8080       # MCP + /registry.json
uv run bouch-registry validate
uv run bouch-registry export registry.json    # static copy, e.g. for static hosting
uv run pytest                                 # offline: model, search, portability, CLI, MCP in-memory + HTTP
scripts/inspector-smoke.sh http://127.0.0.1:8080/mcp     # independent client (MCP Inspector CLI)
scripts/remote-acceptance.sh https://registry.bouch.dev  # the deployed boundary
```

## Deployment

One Fly.io app, `bouch-registry` in `lhr`, running the container in
`Dockerfile`: the package is installed, `registry/` is copied beside it, and
the service is started with `--data /app/registry` so a running container
cannot serve any other copy of the dataset. `/health` reports the capability
and domain counts it loaded, so a stale or truncated deployment is visible
without opening the data.

Deploys run in GitHub Actions (`.github/workflows/deploy.yml`): tests and
`validate`, then `flyctl deploy`. Publishing a GitHub release deploys that
release; `workflow_dispatch` redeploys `main`. Do not deploy from a laptop —
the live service should always correspond to a commit on `main`. There is no
PyPI package: this is a service over a dataset, not a library.

`registry.bouch.dev` is a DNS-only (unproxied) CNAME in Cloudflare to the
app's Fly hostname, with the certificate issued by Fly. Proxying it would
break both certificate validation and the held-open `GET /mcp` event stream
that claude.ai's connector opens.

The registry holds no record of itself: a record's job is to point at a
versioned source and its entrypoints, and a deployment URL is live state,
which records do not carry. `bouch-registry`'s own source is this repository.
