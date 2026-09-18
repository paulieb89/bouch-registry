"""Reading declared entrypoints from the canonical remote source, against a fake GitHub (offline)."""

import json

import httpx
import pytest
from fastmcp import Client
from mcp.shared.exceptions import McpError

from bouch_registry.remote import _parse_advertisement, declared_paths, git_blob_id
from bouch_registry.server import create_server

TAG_OBJECT = "1" * 40
TAG_COMMIT = "2" * 40
HEAD_COMMIT = "3" * 40
SKILL = b"---\nname: electronic-production\n---\n# Electronic production\n"


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
        tags={"v0.1.0-experimental": TAG_OBJECT, "v0.1.0-experimental^{}": TAG_COMMIT},
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
        "ref": "v0.1.0-experimental",
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
        with pytest.raises(McpError, match=r"README.md' is missing from .* at v0.1.0-experimental"):
            await client.read_resource("bouch://source/dev.bouch/audio/README.md")


async def test_missing_ref_is_a_clear_error(registry, github):
    github.tags = {}
    async with Client(create_server(registry, transport=httpx.MockTransport(github))) as client:
        with pytest.raises(McpError, match="ref 'v0.1.0-experimental' does not exist"):
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
