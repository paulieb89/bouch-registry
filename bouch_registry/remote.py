"""Read a record's declared entrypoints from its canonical remote source.

The registry stores pointers, never content. This module follows a pointer at
read time: it resolves the record's `source.ref` (or the remote HEAD when the
record cites no ref) to a commit on the published repository, then fetches the
declared path at that commit. Nothing is cached or copied into the registry,
and there is no local-checkout fallback.

Only paths the record declares (its entrypoints and native manifest) can be
read, which keeps this a pointer-follower rather than a repository browser.

Integrity: the ref is resolved from the repository's own git ref advertisement
(`info/refs`, peeled for annotated tags), content is fetched by commit id (an
immutable address), and the result reports the git blob id of the bytes served
— equal to `git rev-parse <commit>:<path>` in any clone, so a consumer can
check it independently.
"""

from __future__ import annotations

import hashlib
import mimetypes
import posixpath
import re
from dataclasses import dataclass

import httpx

from .model import Capability

MAX_BYTES = 512 * 1024
TIMEOUT = httpx.Timeout(10.0)

_GITHUB = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_TEXT_TYPES = {".md": "text/markdown", ".json": "application/json", ".toml": "application/toml", ".py": "text/x-python"}


class SourceError(Exception):
    """A declared pointer could not be resolved from its remote source. The message says why."""


@dataclass(frozen=True)
class SourceDocument:
    capability_id: str
    path: str
    text: str
    mime_type: str
    repository_url: str
    ref: str | None
    commit: str
    blob: str

    @property
    def provenance(self) -> dict[str, str | None]:
        return {
            "capability": self.capability_id,
            "repository": self.repository_url,
            "ref": self.ref,
            "commit": self.commit,
            "path": self.path,
            "git_blob": self.blob,
        }


def declared_paths(cap: Capability) -> list[str]:
    """The paths a record lets clients read: its entrypoints, then a repo-relative native manifest."""
    paths = [e.path for e in cap.entrypoints]
    if cap.native and cap.native.manifest and not cap.native.manifest.startswith("https://"):
        paths.append(cap.native.manifest)
    return list(dict.fromkeys(paths))


_FENCE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)
_CODE_SPAN = re.compile(r"`[^`\n]*`")
_INLINE_LINK = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'(][^)]*)?\)")
_REFERENCE_LINK = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)", re.M)


def undeclared_links(cap: Capability, path: str, text: str) -> list[str]:
    """Repo-relative file links in a declared Markdown entrypoint whose targets are not declared.

    A remote reader can only follow a link the record declares, so a declared
    document that routes to an undeclared file is a dead end. External URLs,
    anchors and directory links (trailing slash) are not file links.
    """
    if not path.endswith(".md"):
        return []
    prose = _CODE_SPAN.sub("", _FENCE.sub("", text))
    targets = _INLINE_LINK.findall(prose) + _REFERENCE_LINK.findall(prose)
    declared = set(declared_paths(cap))
    problems = []
    for target in dict.fromkeys(targets):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
            continue
        file_part = re.split(r"[#?]", target, maxsplit=1)[0]
        if not file_part or file_part.endswith("/"):
            continue
        if file_part.startswith("/"):
            resolved = posixpath.normpath(file_part.lstrip("/"))
        else:
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), file_part))
        if resolved == ".." or resolved.startswith("../"):
            problems.append(f"{path} links {target!r}, which is outside the repository")
        elif resolved not in declared:
            problems.append(f"{path} links {target!r} -> {resolved}, which is not a declared entrypoint")
    return problems


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


async def read_declared(cap: Capability, path: str, client: httpx.AsyncClient) -> SourceDocument:
    declared = declared_paths(cap)
    if path not in declared:
        raise SourceError(
            f"{path!r} is not a declared entrypoint of {cap.id}; only declared paths are readable. "
            f"Declared: {', '.join(declared) or 'none'}."
        )
    url = cap.source.url
    if url is None:
        raise SourceError(
            f"{cap.id} has no published remote source (repository {cap.source.repository!r}); "
            "its entrypoints cannot be read through the registry."
        )
    match = _GITHUB.match(url)
    if match is None:
        raise SourceError(f"{cap.id}: remote reading supports github.com sources only, not {url}")
    owner, repo = match.groups()

    commit = await _resolve(client, owner, repo, cap.source.ref)
    response = await _get(client, f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}/{path}")
    if response.status_code == 404:
        raise SourceError(
            f"{cap.id}: declared entrypoint {path!r} is missing from {url} at "
            f"{cap.source.ref or 'HEAD'} ({commit}); the record is stale or the path is a directory."
        )
    _raise_for_status(response, f"{cap.id}: fetching {path!r}")
    data = response.content
    if len(data) > MAX_BYTES:
        raise SourceError(f"{cap.id}: {path!r} is {len(data)} bytes, over the {MAX_BYTES}-byte read limit")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SourceError(f"{cap.id}: {path!r} is not UTF-8 text") from None
    return SourceDocument(
        capability_id=cap.id,
        path=path,
        text=text,
        mime_type=_mime(path),
        repository_url=url,
        ref=cap.source.ref,
        commit=commit,
        blob=git_blob_id(data),
    )


async def _resolve(client: httpx.AsyncClient, owner: str, repo: str, ref: str | None) -> str:
    """Resolve a tag/branch name, or HEAD when ref is None, to a commit id via git smart HTTP."""
    response = await _get(client, f"https://github.com/{owner}/{repo}.git/info/refs?service=git-upload-pack")
    _raise_for_status(response, f"listing refs of github.com/{owner}/{repo}")
    refs = _parse_advertisement(response.content)
    if ref is None:
        candidates = ["HEAD"]
    elif ref.startswith("refs/"):
        candidates = [ref]
    else:
        candidates = [f"refs/tags/{ref}", f"refs/heads/{ref}"]
    for name in candidates:
        # Annotated tags point at a tag object; the peeled `^{}` entry is the commit.
        commit = refs.get(f"{name}^{{}}") or refs.get(name)
        if commit:
            return commit
    raise SourceError(f"ref {ref or 'HEAD'!r} does not exist at https://github.com/{owner}/{repo}")


def _parse_advertisement(body: bytes) -> dict[str, str]:
    """Parse a git-upload-pack ref advertisement (pkt-line framed) into {refname: object id}."""
    refs: dict[str, str] = {}
    i = 0
    while i + 4 <= len(body):
        size = int(body[i : i + 4], 16)
        if size == 0:  # flush-pkt
            i += 4
            continue
        line = body[i + 4 : i + size].rstrip(b"\n").split(b"\0", 1)[0].decode()
        i += size
        if line.startswith("#"):
            continue
        oid, _, name = line.partition(" ")
        if re.fullmatch(r"[0-9a-f]{40}", oid) and name:
            refs[name] = oid
    return refs


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    try:
        return await client.get(url, timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise SourceError(f"could not reach {url}: {exc}") from exc


def _raise_for_status(response: httpx.Response, action: str) -> None:
    if response.status_code != 200:
        raise SourceError(f"{action}: remote returned HTTP {response.status_code}")


def _mime(path: str) -> str:
    for suffix, mime in _TEXT_TYPES.items():
        if path.endswith(suffix):
            return mime
    return mimetypes.guess_type(path)[0] or "text/plain"
