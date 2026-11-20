"""The CLI validates and searches the dataset it was pointed at."""

import json

from bouch_registry import cli
from tests.conftest import read_entry, write_entry


def test_validate_is_the_default_command(capsys):
    assert cli.main([]) == 0
    assert capsys.readouterr().out.startswith("ok: ")


def test_invalid_data_is_rejected(data_copy, capsys):
    record = read_entry(data_copy, "sessinspect")
    record["domains"] = ["cooking"]
    write_entry(data_copy, "sessinspect", record)

    assert cli.main(["--data", str(data_copy), "validate"]) == 1
    assert "unknown domain 'cooking'" in capsys.readouterr().err


def test_search_uses_the_data_directory_it_was_given(data_copy, capsys):
    record = read_entry(data_copy, "sessinspect")
    record["title"] = "Only record"
    record["related"] = []
    write_entry(data_copy, "sessinspect", record)
    for path in (data_copy / "entries").glob("*.json"):
        if path.stem != "sessinspect":
            path.unlink()
    (data_copy / "domains.json").write_text('[{"id": "agent-harness", "title": "T", "summary": "S"}]')

    assert cli.main(["--data", str(data_copy), "search", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert [r["title"] for r in records] == ["Only record"]


def test_search_filters_by_type(capsys):
    assert cli.main(["search", "--type", "mcp-server", "--limit", "50"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines and all("[mcp-server," in line for line in lines)
