import json
import shutil
from pathlib import Path

import pytest

from bouch_registry.store import DEFAULT_ROOT, load_registry


@pytest.fixture(scope="session")
def registry():
    return load_registry()


@pytest.fixture
def data_copy(tmp_path) -> Path:
    """A writable copy of the real registry data, for negative cases."""
    root = tmp_path / "registry"
    shutil.copytree(DEFAULT_ROOT, root)
    return root


def read_entry(root: Path, slug: str) -> dict:
    return json.loads((root / "entries" / f"{slug}.json").read_text())


def write_entry(root: Path, slug: str, data: dict) -> None:
    (root / "entries" / f"{slug}.json").write_text(json.dumps(data))
