"""Load the versioned registry data, and derive every view from it.

`registry/` in this repository is the only dataset. The MCP tools, MCP
resources, `/registry.json` and `bouch-registry export` all read the
`Registry` built here.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .model import NATIVE_SPECS, Capability, Domain

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "registry"

RESOURCE_PREFIX = "bouch://"


class RegistryError(Exception):
    """The registry data is invalid. Carries every problem found, not just the first."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("invalid registry:\n  " + "\n  ".join(problems))


def capability_uri(capability_id: str) -> str:
    return f"{RESOURCE_PREFIX}capabilities/{capability_id}"


def domain_uri(domain_id: str) -> str:
    return f"{RESOURCE_PREFIX}domains/{domain_id}"


@dataclass(frozen=True)
class SearchHit:
    capability: Capability
    score: float
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class Registry:
    domains: tuple[Domain, ...]
    capabilities: tuple[Capability, ...]

    def get(self, capability_id: str) -> Capability | None:
        return next((c for c in self.capabilities if c.id == capability_id), None)

    def domain(self, domain_id: str) -> Domain | None:
        return next((d for d in self.domains if d.id == domain_id), None)

    def in_domain(self, domain_id: str) -> list[Capability]:
        return [c for c in self.capabilities if domain_id in c.domains]

    def search(
        self,
        query: str = "",
        domain: str | None = None,
        type: str | None = None,
        limit: int = 10,
    ) -> list[SearchHit]:
        return _search(self, query, domain, type, limit)

    def to_json(self) -> dict[str, Any]:
        """The static machine-readable registry (served as /registry.json)."""
        return {
            "registry": "dev.bouch/registry",
            "native_specs": {
                type_: spec.model_dump(mode="json") for type_, spec in NATIVE_SPECS.items()
            },
            "domains": [d.model_dump(mode="json") for d in self.domains],
            "capabilities": [c.model_dump(mode="json", exclude_none=True) for c in self.capabilities],
        }


def load_registry(root: Path = DEFAULT_ROOT) -> Registry:
    problems: list[str] = []

    domains: list[Domain] = []
    try:
        raw_domains = json.loads((root / "domains.json").read_text())
        for item in raw_domains:
            try:
                domains.append(Domain.model_validate(item))
            except ValidationError as exc:
                problems.append(f"domains.json {item.get('id', '?')}: {_first_lines(exc)}")
    except (OSError, ValueError) as exc:
        problems.append(f"domains.json: {exc}")

    capabilities: list[Capability] = []
    for path in sorted((root / "entries").glob("*.json")):
        try:
            cap = Capability.model_validate_json(path.read_text())
        except ValidationError as exc:
            problems.append(f"entries/{path.name}: {_first_lines(exc)}")
            continue
        if path.stem != cap.slug:
            problems.append(f"entries/{path.name}: file name must be '{cap.slug}.json' to match id {cap.id}")
        capabilities.append(cap)

    domain_ids = [d.id for d in domains]
    ids = [c.id for c in capabilities]
    for dup in sorted({i for i in domain_ids if domain_ids.count(i) > 1}):
        problems.append(f"domains.json: duplicate domain {dup}")
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"duplicate capability id {dup}")
    used_domains = {d for c in capabilities for d in c.domains}
    for cap in capabilities:
        for d in cap.domains:
            if d not in domain_ids:
                problems.append(f"{cap.id}: unknown domain {d!r} (declare it in domains.json)")
        for rel in cap.related:
            if rel not in ids:
                problems.append(f"{cap.id}: related id {rel} does not exist")
    for d in domain_ids:
        if d not in used_domains:
            problems.append(f"domains.json: domain {d!r} has no capabilities")

    if problems:
        raise RegistryError(problems)
    return Registry(domains=tuple(domains), capabilities=tuple(capabilities))


def _first_lines(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in exc.errors())


# ---------------------------------------------------------------------------
# Deterministic search
# ---------------------------------------------------------------------------
#
# Weighted term matching with inverse document frequency, so words that occur
# in most records ("agent", "capability") rank below words that discriminate
# ("synthesis", "reaper"). No stemming library, no embeddings: a plural fold and
# a prefix match (min 4 chars) cover "sounds"/"sound" and "synth"/"synthesis".

_WORD = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    """a about after all also an and any anything are as at be before being but by can could
    do does for from get has have how i if in into is it its me my of on or our should so
    some that the their them then there these this those to up us use want was we what when
    where which who why will with would you your""".split()
)

_FIELD_WEIGHTS = {"id": 2.0, "title": 3.0, "tags": 3.0, "domains": 3.0, "summary": 1.0, "entrypoints": 1.0}


def _fold(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def terms(text: str) -> list[str]:
    return [_fold(t) for t in _WORD.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


def _matches(query_term: str, doc_term: str) -> bool:
    if query_term == doc_term:
        return True
    short, long_ = sorted((query_term, doc_term), key=len)
    return len(short) >= 4 and long_.startswith(short)


def _fields(cap: Capability) -> dict[str, set[str]]:
    return {
        "id": set(terms(cap.id)),
        "title": set(terms(cap.title)),
        "tags": {t for tag in cap.tags for t in terms(tag)},
        "domains": {t for d in cap.domains for t in terms(d)},
        "summary": set(terms(cap.summary)),
        "entrypoints": {t for e in cap.entrypoints for t in terms(e.title)},
    }


def _search(registry: Registry, query: str, domain: str | None, type_: str | None, limit: int) -> list[SearchHit]:
    candidates = [
        c
        for c in registry.capabilities
        if (domain is None or domain in c.domains) and (type_ is None or c.type == type_)
    ]
    query_terms = list(dict.fromkeys(terms(query)))
    if not query_terms:
        return [SearchHit(c, 0.0, ()) for c in sorted(candidates, key=lambda c: c.id)][:limit]

    # IDF over the whole registry, so filtering does not change term weights.
    all_fields = {c.id: _fields(c) for c in registry.capabilities}
    n = len(registry.capabilities)

    def best_weight(fields: dict[str, set[str]], q: str) -> float:
        return max(
            (_FIELD_WEIGHTS[name] for name, toks in fields.items() if any(_matches(q, t) for t in toks)),
            default=0.0,
        )

    idf = {}
    for q in query_terms:
        df = sum(1 for f in all_fields.values() if best_weight(f, q) > 0)
        idf[q] = math.log((n + 1) / (df + 0.5)) if df else 0.0

    hits = []
    for cap in candidates:
        fields = all_fields[cap.id]
        score, matched = 0.0, []
        for q in query_terms:
            w = best_weight(fields, q)
            if w:
                score += w * idf[q]
                matched.append(q)
        if score > 0:
            hits.append(SearchHit(cap, round(score, 3), tuple(matched)))
    hits.sort(key=lambda h: (-h.score, h.capability.id))
    return hits[:limit]
