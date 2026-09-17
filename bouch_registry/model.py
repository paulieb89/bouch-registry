"""Registry record model.

A record says *what* a Bouch capability is, *where* its versioned source lives
and *which* native manifest or entrypoint documents to read next. It is a
pointer, not a description: anything a native manifest or a live system can
answer (versions, tool lists, git state, runtime health) stays there.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CapabilityType = Literal[
    "agent-plugin",
    "agent-skill",
    "mcp-server",
    "remote-agent",
    "reference-workbench",
    "evidence-repository",
    "observability-tool",
    "routing-pointer",
]

Lifecycle = Literal["experimental", "active", "frozen", "deprecated"]

EntrypointRole = Literal["contract", "knowledge", "skill", "tool", "evidence"]

ID_PATTERN = r"^dev\.bouch/[a-z0-9]+(-[a-z0-9]+)*$"
SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
HTTPS_PATTERN = r"^https://\S+$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NativeSpec(_Strict):
    """An open specification that natively describes one capability type."""

    id: str
    name: str
    manifest: str = Field(description="Conventional manifest file the spec defines.")
    url: str = Field(pattern=HTTPS_PATTERN)


# The only types with a native cross-provider manifest (see the
# agent-enumeration-lab finding `bouch-registry-standards-fit`). The other four
# types have no native representation and carry no `native` block.
#
# Terminology: an A2A `AgentSkill` is a capability tag inside an A2A Agent Card.
# It is unrelated to an Agent Skills `SKILL.md`, which is what `agent-skill`
# means here.
NATIVE_SPECS: dict[str, NativeSpec] = {
    "agent-plugin": NativeSpec(
        id="agent-plugins",
        name="Agent Plugins 1.0",
        manifest="plugin.json",
        url="https://github.com/agentplugins/agent-plugins-spec",
    ),
    "agent-skill": NativeSpec(
        id="agent-skills",
        name="Agent Skills (SKILL.md)",
        manifest="SKILL.md",
        url="https://agentskills.io/specification",
    ),
    "mcp-server": NativeSpec(
        id="mcp-server-json",
        name="MCP Registry server.json",
        manifest="server.json",
        url="https://github.com/modelcontextprotocol/registry/tree/main/docs/reference/server-json",
    ),
    "remote-agent": NativeSpec(
        id="a2a-agent-card",
        name="A2A Agent Card",
        manifest="agent-card.json",
        url="https://github.com/a2aproject/A2A",
    ),
}

_URL = re.compile(HTTPS_PATTERN)


def _check_repo_path(value: str) -> str:
    """Paths are relative to the root of the record's source repository.

    Absolute, home-relative, parent-escaping or Windows-style paths would bind a
    record to one machine's checkout, which is exactly what records must not do.
    """
    if not value or value != value.strip():
        raise ValueError("path must be non-empty without surrounding whitespace")
    if value.startswith(("/", "~")) or "\\" in value or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"path must be repository-relative, got {value!r}")
    if ".." in value.split("/") or "://" in value:
        raise ValueError(f"path must stay inside the repository, got {value!r}")
    return value


class Source(_Strict):
    """Where the authoritative, versioned source of a capability lives."""

    repository: str = Field(
        pattern=SLUG_PATTERN,
        description="Repository name. Stable identity of the source, independent of any checkout location.",
    )
    url: str | None = Field(
        default=None,
        pattern=HTTPS_PATTERN,
        description="Published remote repository. null means the source is not published remotely yet.",
    )
    ref: str | None = Field(
        default=None,
        description="Durable git tag to cite (e.g. an evidence freeze). Not a live branch.",
    )
    note: str | None = None


class Native(_Strict):
    """Pointer to the native manifest for types that have an open spec."""

    spec: str = Field(description="Id of the native spec; must match the record type.")
    manifest: str | None = Field(
        default=None,
        description="Repository-relative path or https URL of the native manifest. null when none exists yet.",
    )
    note: str | None = Field(default=None, description="Required when manifest is null: what is missing.")

    @field_validator("manifest")
    @classmethod
    def _manifest_location(cls, value: str | None) -> str | None:
        if value is None or _URL.match(value):
            return value
        return _check_repo_path(value)

    @model_validator(mode="after")
    def _absent_manifest_is_explained(self) -> Native:
        if self.manifest is None and not self.note:
            raise ValueError("native.note is required when native.manifest is null")
        return self


class Entrypoint(_Strict):
    role: EntrypointRole
    path: str = Field(description="Repository-relative path to a file or directory.")
    title: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def _repo_relative(cls, value: str) -> str:
        return _check_repo_path(value)


class Capability(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    type: CapabilityType
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(
        min_length=1,
        max_length=800,
        description="Routing summary: when an agent should consult this. Not a copy of a native manifest.",
    )
    domains: list[str] = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    lifecycle: Lifecycle
    source: Source
    native: Native | None = None
    entrypoints: list[Entrypoint] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)

    @field_validator("domains", "tags")
    @classmethod
    def _slugs(cls, values: list[str]) -> list[str]:
        for v in values:
            if not re.match(SLUG_PATTERN, v):
                raise ValueError(f"{v!r} is not a lowercase slug")
        if len(set(values)) != len(values):
            raise ValueError("values must be unique")
        return values

    @field_validator("related")
    @classmethod
    def _related_ids(cls, values: list[str]) -> list[str]:
        for v in values:
            if not re.match(ID_PATTERN, v):
                raise ValueError(f"{v!r} is not a registry id")
        return values

    @model_validator(mode="after")
    def _native_matches_type(self) -> Capability:
        expected = NATIVE_SPECS.get(self.type)
        if expected is None and self.native is not None:
            raise ValueError(f"type {self.type!r} has no native spec; remove the native block")
        if expected is not None:
            if self.native is None:
                raise ValueError(f"type {self.type!r} requires a native block (spec {expected.id!r})")
            if self.native.spec != expected.id:
                raise ValueError(f"type {self.type!r} requires native.spec {expected.id!r}, got {self.native.spec!r}")
        if self.lifecycle == "frozen" and not self.source.ref:
            raise ValueError("frozen capabilities must cite source.ref (the freeze tag)")
        if self.id in self.related:
            raise ValueError("a capability cannot relate to itself")
        return self

    @property
    def slug(self) -> str:
        return self.id.split("/", 1)[1]


class Domain(_Strict):
    id: str = Field(pattern=SLUG_PATTERN)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=800)
