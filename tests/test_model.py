"""Schema and dataset validation, including proof that invalid data is rejected."""

import json

import pytest

from bouch_registry.model import NATIVE_SPECS
from bouch_registry.store import RegistryError, load_registry

from .conftest import read_entry, write_entry


def test_real_registry_validates(registry):
    assert len(registry.capabilities) == len({c.id for c in registry.capabilities})
    assert registry.domains


def test_seeded_types_are_real_and_cover_native_and_bouch_types(registry):
    types = {c.type for c in registry.capabilities}
    assert types & set(NATIVE_SPECS), "at least one natively-specified type is seeded"
    assert types - set(NATIVE_SPECS), "at least one Bouch-only type is seeded"


def test_native_block_matches_type(registry):
    for cap in registry.capabilities:
        if cap.type in NATIVE_SPECS:
            assert cap.native is not None and cap.native.spec == NATIVE_SPECS[cap.type].id
        else:
            assert cap.native is None


def _expect_problem(root, fragment):
    with pytest.raises(RegistryError) as exc:
        load_registry(root)
    assert any(fragment in p for p in exc.value.problems), exc.value.problems


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda e: e["entrypoints"].append({"role": "contract", "path": "/home/someone/repo/CLAUDE.md", "title": "x"}), "repository-relative"),
        (lambda e: e["entrypoints"].append({"role": "contract", "path": "~/repo/CLAUDE.md", "title": "x"}), "repository-relative"),
        (lambda e: e["entrypoints"].append({"role": "contract", "path": "../other/CLAUDE.md", "title": "x"}), "inside the repository"),
        (lambda e: e.update(version="0.2.0"), "Extra inputs"),
        (lambda e: e.update(keywords=["copied", "from", "plugin.json"]), "Extra inputs"),
        (lambda e: e.pop("native"), "requires a native block"),
        (lambda e: e["native"].update(spec="mcp-server-json"), "requires native.spec"),
        (lambda e: e["native"].update(manifest=None), "native.note is required"),
        (lambda e: e.update(type="made-up-type"), "type"),
        (lambda e: e.update(domains=["no-such-domain"]), "unknown domain"),
        (lambda e: e.update(related=["dev.bouch/does-not-exist"]), "does not exist"),
        (lambda e: e.update(id="com.example/bouch-agent-core"), "id"),
        (lambda e: e.update(lifecycle="frozen"), "source.ref"),
    ],
)
def test_invalid_native_entry_is_rejected(data_copy, mutate, fragment):
    entry = read_entry(data_copy, "bouch-agent-core")
    mutate(entry)
    write_entry(data_copy, "bouch-agent-core", entry)
    _expect_problem(data_copy, fragment)


def test_bouch_only_type_rejects_native_block(data_copy):
    entry = read_entry(data_copy, "sessinspect")
    entry["native"] = {"spec": "agent-plugins", "manifest": "plugin.json"}
    write_entry(data_copy, "sessinspect", entry)
    _expect_problem(data_copy, "has no native spec")


def test_file_name_must_match_id(data_copy):
    (data_copy / "entries" / "sessinspect.json").rename(data_copy / "entries" / "renamed.json")
    _expect_problem(data_copy, "file name must be 'sessinspect.json'")


def test_unused_domain_is_rejected(data_copy):
    domains = json.loads((data_copy / "domains.json").read_text())
    domains.append({"id": "robotics", "title": "Robotics", "summary": "Nothing registered yet."})
    (data_copy / "domains.json").write_text(json.dumps(domains))
    _expect_problem(data_copy, "has no capabilities")


def test_all_problems_are_reported_together(data_copy):
    for slug in ("sessinspect", "reaper-mcp"):
        entry = read_entry(data_copy, slug)
        entry["domains"] = ["nowhere"]
        write_entry(data_copy, slug, entry)
    with pytest.raises(RegistryError) as exc:
        load_registry(data_copy)
    assert len([p for p in exc.value.problems if "nowhere" in p]) == 2
