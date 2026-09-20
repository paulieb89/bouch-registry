"""Reading declared entrypoints from the canonical remote source, against a fake GitHub (offline)."""

import json
import re

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError

from bouch_registry.remote import _parse_advertisement, declared_paths, git_blob_id
from bouch_registry.server import create_server
from bouch_registry.store import load_registry

TAG_OBJECT = "1" * 40
TAG_COMMIT = "2" * 40
HEAD_COMMIT = "3" * 40
SKILL = b"---\nname: electronic-production\n---\n# Electronic production\n"

# Derived, never hardcoded: these tests fake a remote that must advertise
# whatever tag dev.bouch/audio is currently pinned at. Writing the tag name
# in here couples the suite to one release and breaks it on every bump.
AUDIO_REF = load_registry().get("dev.bouch/audio").source.ref


def pkt(line: str) -> bytes:
    data = line.encode() + b"\n"
    return b"%04x" % (len(data) + 4) + data


def advertisement(tags: dict[str, str]) -> bytes:
    body = pkt("# service=git-upload-pack") + b"0000"
    body += pkt(f"{HEAD_COMMIT} HEAD\0multi_ack side-band-64k")
    body += pkt(f"{HEAD_COMMIT} refs/heads/main")
    for name, oid in tags.items():
        body += pkt(f"{oid} refs/tags/{name}")
    return body + b"0000"


class FakeGitHub:
    """Serves ref advertisements and raw files; files exist only at the commits given."""

    def __init__(self, files: dict[tuple[str, str], bytes], tags: dict[str, str]):
        self.files, self.tags, self.requests = files, tags, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        url = request.url
        if url.host == "github.com" and url.path.endswith(".git/info/refs"):
            return httpx.Response(200, content=advertisement(self.tags))
        if url.host == "raw.githubusercontent.com":
            _, _owner, _repo, commit, path = url.path.split("/", 4)
            if (commit, path) in self.files:
                return httpx.Response(200, content=self.files[(commit, path)])
        return httpx.Response(404)


@pytest.fixture
def github():
    return FakeGitHub(
        files={
            (TAG_COMMIT, "skills/electronic-production/SKILL.md"): SKILL,
            (HEAD_COMMIT, "skills/electronic-production/SKILL.md"): b"moved on after the tag\n",
            (HEAD_COMMIT, "README.md"): b"# bouch-agent-core\n",
        },
        tags={AUDIO_REF: TAG_OBJECT, f"{AUDIO_REF}^{{}}": TAG_COMMIT},
    )


@pytest.fixture
def mcp(registry, github):
    return create_server(registry, transport=httpx.MockTransport(github))


def test_parse_advertisement_reads_refs_and_peeled_tags():
    refs = _parse_advertisement(advertisement({"v1": TAG_OBJECT, "v1^{}": TAG_COMMIT}))
    assert refs["HEAD"] == HEAD_COMMIT
    assert refs["refs/tags/v1"] == TAG_OBJECT
    assert refs["refs/tags/v1^{}"] == TAG_COMMIT


def test_declared_paths_include_entrypoints_and_repo_manifest(registry):
    paths = declared_paths(registry.get("dev.bouch/audio"))
    assert "skills/electronic-production/SKILL.md" in paths
    assert paths[-1] == "plugin.json"


async def test_reads_declared_entrypoint_at_the_pinned_tag_commit(mcp, github):
    async with Client(mcp) as client:
        [content] = await client.read_resource("bouch://source/dev.bouch/audio/skills/electronic-production/SKILL.md")
    assert content.text == SKILL.decode()
    assert content.mimeType == "text/markdown"
    assert content.meta == {
        "capability": "dev.bouch/audio",
        "repository": "https://github.com/paulieb89/bouch-audio",
        "ref": AUDIO_REF,
        "commit": TAG_COMMIT,  # peeled annotated tag, not the tag object and not HEAD
        "path": "skills/electronic-production/SKILL.md",
        "git_blob": git_blob_id(SKILL),
    }
    assert any(TAG_COMMIT in url for url in github.requests)


async def test_unpinned_record_resolves_remote_head(mcp):
    async with Client(mcp) as client:
        [content] = await client.read_resource("bouch://source/dev.bouch/bouch-agent-core/README.md")
    assert content.meta["ref"] is None
    assert content.meta["commit"] == HEAD_COMMIT


async def test_undeclared_path_is_refused_without_touching_the_network(mcp, github):
    async with Client(mcp) as client:
        with pytest.raises(McpError, match="not a declared entrypoint.*Declared: README.md"):
            await client.read_resource("bouch://source/dev.bouch/audio/tools/analyze.py")
    assert github.requests == []


async def test_missing_entrypoint_at_the_ref_is_a_clear_error(mcp):
    # Declared and present at HEAD in reality, but absent at the pinned commit here: stale, no fallback.
    async with Client(mcp) as client:
        with pytest.raises(McpError, match=rf"README.md' is missing from .* at {re.escape(AUDIO_REF)}"):
            await client.read_resource("bouch://source/dev.bouch/audio/README.md")


async def test_missing_ref_is_a_clear_error(registry, github):
    github.tags = {}
    async with Client(create_server(registry, transport=httpx.MockTransport(github))) as client:
        with pytest.raises(McpError, match=rf"ref '{re.escape(AUDIO_REF)}' does not exist"):
            await client.read_resource("bouch://source/dev.bouch/audio/skills/electronic-production/SKILL.md")


async def test_unpublished_source_is_refused(mcp, github):
    async with Client(mcp) as client:
        with pytest.raises(McpError, match="no published remote source"):
            await client.read_resource("bouch://source/dev.bouch/audio-agent-workbench-v2/CLAUDE.md")
        with pytest.raises(McpError, match="Unknown capability"):
            await client.read_resource("bouch://source/dev.bouch/nope/README.md")
    assert github.requests == []


async def test_registry_json_is_unchanged_by_remote_reading(mcp, registry):
    async with Client(mcp) as client:
        full = json.loads((await client.read_resource("bouch://registry"))[0].text)
    assert full == registry.to_json()


# --- the same resolver as a tool, for tool-only clients ----------------------


async def test_tool_and_resource_return_the_same_document(mcp):
    async with Client(mcp) as client:
        [resource] = await client.read_resource("bouch://source/dev.bouch/audio/skills/electronic-production/SKILL.md")
        tool = await client.call_tool(
            "read_capability_entrypoint",
            {"capability_id": "dev.bouch/audio", "entrypoint": "skills/electronic-production/SKILL.md"},
        )
    out = tool.structured_content
    assert out["content"] == resource.text
    assert out["mime_type"] == resource.mimeType
    assert {k: out[k] for k in resource.meta} == resource.meta
    assert out["commit"] == TAG_COMMIT and out["git_blob"] == git_blob_id(SKILL)


@pytest.mark.parametrize(
    "capability_id, entrypoint, message",
    [
        ("dev.bouch/audio", "tools/analyze.py", "not a declared entrypoint"),
        ("dev.bouch/audio", "README.md", rf"is missing from .* at {re.escape(AUDIO_REF)}"),
        ("dev.bouch/audio-agent-workbench-v2", "CLAUDE.md", "no published remote source"),
        ("dev.bouch/nope", "README.md", "Unknown capability"),
    ],
)
async def test_tool_fails_clearly_like_the_resource(mcp, capability_id, entrypoint, message):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=message):
            await client.call_tool("read_capability_entrypoint", {"capability_id": capability_id, "entrypoint": entrypoint})


async def test_tool_is_read_only_and_structured(mcp):
    async with Client(mcp) as client:
        [tool] = [t for t in await client.list_tools() if t.name == "read_capability_entrypoint"]
    assert tool.annotations.readOnlyHint is True and tool.annotations.openWorldHint is True
    assert set(tool.outputSchema["properties"]) >= {"content", "repository", "ref", "commit", "path", "git_blob"}


# --- declared Markdown must not route to undeclared files --------------------


def test_links_to_undeclared_files_are_reported(registry):
    from bouch_registry.remote import undeclared_links

    cap = registry.get("dev.bouch/audio")
    text = """
See [evidence](../../references/evidence-status.md#tiers) and [bass](../../references/bass-sub-kick-interaction.md).
Undeclared: [analyzer](../../tools/analyze.py), ![plot](/docs/plot.png "a plot") and [ref-style][r].
Escapes: [up](../../../other-repo/README.md)

[r]: ../../evals/README.md

Not file links: [web](https://example.com/x.md), [anchor](#top), [dir](../../tools/),
`[in code](../../hidden.md)`

```
[in a fence](../../fenced.md)
```
"""
    problems = undeclared_links(cap, "skills/electronic-production/SKILL.md", text)
    assert problems == [
        "skills/electronic-production/SKILL.md links '../../tools/analyze.py' -> tools/analyze.py, which is not a declared entrypoint",
        "skills/electronic-production/SKILL.md links '/docs/plot.png' -> docs/plot.png, which is not a declared entrypoint",
        "skills/electronic-production/SKILL.md links '../../../other-repo/README.md', which is outside the repository",
        "skills/electronic-production/SKILL.md links '../../evals/README.md' -> evals/README.md, which is not a declared entrypoint",
    ]
    assert undeclared_links(cap, "plugin.json", "[x](nope.md)") == []
