"""Records must be portable and must not copy native manifests."""

import json
import re

from bouch_registry.store import DEFAULT_ROOT

# Anything that binds a record to one machine's checkout.
LOCAL_PATH = re.compile(r"(/home/|/Users/|/tmp/|~/|file://|[A-Za-z]:\\)")


def test_no_local_paths_in_source_data():
    for path in sorted(DEFAULT_ROOT.rglob("*.json")):
        text = path.read_text()
        assert not LOCAL_PATH.search(text), f"{path.name}: {LOCAL_PATH.search(text).group(0)}"


def test_no_local_paths_in_rendered_registry(registry):
    text = json.dumps(registry.to_json())
    assert not LOCAL_PATH.search(text)


def test_instrument_detects_a_local_path():
    assert LOCAL_PATH.search('{"path": "/home/bch/experiments/x"}')
    assert LOCAL_PATH.search('{"path": "~/src/reaper-mcp"}')


def test_records_hold_pointers_not_manifest_content(registry):
    """Native manifests own version, keywords, author, tools, packages... The model forbids
    such fields (see test_model), and native-typed records point at a manifest path instead."""
    manifest_fields = {"version", "keywords", "author", "packages", "remotes", "tools", "skills", "capabilities"}
    for record in registry.to_json()["capabilities"]:
        assert not manifest_fields & set(record), record["id"]
        if "native" in record:
            assert set(record["native"]) <= {"spec", "manifest", "note"}
