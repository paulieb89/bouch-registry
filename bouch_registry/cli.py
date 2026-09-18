"""bouch-registry command line: serve, validate, export, verify-sources."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from .store import DEFAULT_ROOT, Registry, RegistryError, load_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bouch-registry")
    parser.add_argument("--data", type=Path, default=DEFAULT_ROOT, help="registry data directory")
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="run the MCP server over streamable HTTP (default)")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int)

    sub.add_parser("validate", help="validate registry data and exit")

    p_export = sub.add_parser("export", help="write the static registry.json")
    p_export.add_argument("output", type=Path, nargs="?", help="output file (default: stdout)")

    p_verify = sub.add_parser(
        "verify-sources",
        help="check that manifest and entrypoint paths exist in local git checkouts of the source repositories",
    )
    p_verify.add_argument(
        "--checkout", action="append", default=[], metavar="REPOSITORY=PATH",
        help="map a source repository name to a local checkout; repeatable. Never stored in the registry.",
    )
    p_verify.add_argument(
        "--remote", action="store_true",
        help="instead, read every declared pointer of published records from its remote source, as MCP clients do",
    )

    args = parser.parse_args(argv)
    command = args.command or "serve"

    try:
        registry = load_registry(args.data)
    except RegistryError as exc:
        print(exc, file=sys.stderr)
        return 1

    if command == "serve":
        from .server import serve

        serve(registry, host=getattr(args, "host", "127.0.0.1"), port=getattr(args, "port", None))
        return 0
    if command == "validate":
        print(f"ok: {len(registry.capabilities)} capabilities in {len(registry.domains)} domains")
        return 0
    if command == "export":
        text = json.dumps(registry.to_json(), indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.write_text(text)
        else:
            sys.stdout.write(text)
        return 0
    if command == "verify-sources" and args.remote:
        import asyncio

        return asyncio.run(verify_remote(registry))
    if command == "verify-sources":
        checkouts = dict(item.split("=", 1) for item in args.checkout)
        return verify_sources(registry, {k: Path(v).expanduser() for k, v in checkouts.items()})
    parser.error(f"unknown command {command}")
    return 2


def verify_sources(registry: Registry, checkouts: dict[str, Path]) -> int:
    """Check pointers against the versioned source: paths must exist in git at source.ref, else HEAD.

    Uncommitted files do not count — the registry points at versioned sources.
    """
    failures = 0
    for cap in registry.capabilities:
        repo = checkouts.get(cap.source.repository)
        if repo is None:
            print(f"SKIP {cap.id}: no checkout given for repository {cap.source.repository}")
            continue
        rev = cap.source.ref or "HEAD"
        paths = [e.path for e in cap.entrypoints]
        if cap.native and cap.native.manifest and not cap.native.manifest.startswith("https://"):
            paths.append(cap.native.manifest)
        for path in paths:
            ok = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{rev}:{path}"], capture_output=True).returncode == 0
            print(f"{'ok  ' if ok else 'FAIL'} {cap.id}: {rev}:{path}")
            failures += not ok
        if cap.native and cap.native.manifest and not cap.native.manifest.startswith("https://"):
            problem = _native_manifest_problem(repo, rev, cap.native.spec, cap.native.manifest)
            print(f"{'FAIL' if problem else 'ok  '} {cap.id}: native {cap.native.spec} manifest {problem or 'is well-formed'}")
            failures += bool(problem)
    print(f"{failures} failure(s)")
    return 1 if failures else 0


async def verify_remote(registry: Registry) -> int:
    """Resolve every declared path of every published record exactly as the bouch://source resource does."""
    import httpx

    from .remote import SourceError, declared_paths, read_declared

    failures = 0
    async with httpx.AsyncClient() as client:
        for cap in registry.capabilities:
            if cap.source.url is None:
                print(f"SKIP {cap.id}: source not published remotely")
                continue
            for path in declared_paths(cap):
                try:
                    doc = await read_declared(cap, path, client)
                except SourceError as exc:
                    print(f"FAIL {cap.id}: {exc}")
                    failures += 1
                    continue
                print(f"ok   {cap.id}: {doc.ref or 'HEAD'}@{doc.commit[:12]}:{path} blob {doc.blob[:12]}")
    print(f"{failures} failure(s)")
    return 1 if failures else 0


def _native_manifest_problem(repo: Path, rev: str, spec: str, path: str) -> str | None:
    shown = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{path}"], capture_output=True, text=True)
    if shown.returncode != 0:
        return "is missing"
    text = shown.stdout
    if spec == "agent-plugins":
        try:
            data = json.loads(text)
        except ValueError as exc:
            return f"is not JSON ({exc})"
        if "$schema" not in data or "name" not in data:
            return "lacks required $schema/name"
    if spec == "agent-skills":
        match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        name = re.search(r"^name:\s*(\S+)\s*$", match.group(1), re.M) if match else None
        if not name:
            return "has no frontmatter name"
        if name.group(1) != Path(path).parent.name:
            return f"name {name.group(1)!r} does not match its directory"
    return None


if __name__ == "__main__":
    sys.exit(main())
