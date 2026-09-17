"""The CLI must serve the dataset it was pointed at: the container passes --data."""

import bouch_registry.server as server_module
from bouch_registry import cli
from tests.conftest import read_entry, write_entry


def test_serve_uses_the_data_directory_it_was_given(data_copy, monkeypatch):
    record = read_entry(data_copy, "sessinspect")
    record["title"] = "Only record"
    record["related"] = []
    write_entry(data_copy, "sessinspect", record)
    for path in (data_copy / "entries").glob("*.json"):
        if path.stem != "sessinspect":
            path.unlink()
    (data_copy / "domains.json").write_text('[{"id": "agent-harness", "title": "T", "summary": "S"}]')

    served = {}
    monkeypatch.setattr(server_module, "serve", lambda registry, **kwargs: served.update(registry=registry, **kwargs))

    assert cli.main(["--data", str(data_copy), "serve", "--port", "1234"]) == 0
    assert [c.title for c in served["registry"].capabilities] == ["Only record"]
    assert served["port"] == 1234


def test_invalid_data_is_rejected_before_anything_is_served(data_copy, monkeypatch, capsys):
    record = read_entry(data_copy, "sessinspect")
    record["domains"] = ["cooking"]
    write_entry(data_copy, "sessinspect", record)
    monkeypatch.setattr(server_module, "serve", lambda *a, **k: pytest_fail())

    assert cli.main(["--data", str(data_copy), "serve"]) == 1
    assert "unknown domain 'cooking'" in capsys.readouterr().err


def pytest_fail() -> None:
    raise AssertionError("serve must not run on an invalid registry")
