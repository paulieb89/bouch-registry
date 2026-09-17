"""Deterministic search and read behaviour."""

AUDIO_QUERY = (
    "I want to create sophisticated original electronic sounds. "
    "What existing Bouch capabilities should I inspect before building anything?"
)
AUDIO_CORE = {
    "dev.bouch/audio-agent-workbench-v2",
    "dev.bouch/production-technique-reference",
    "dev.bouch/reaper-agent-lab",
}


def ids(hits):
    return [h.capability.id for h in hits]


def test_realistic_audio_query_routes_to_the_audio_ecosystem(registry):
    hits = registry.search(AUDIO_QUERY)
    top4 = hits[:4]
    assert all("audio" in h.capability.domains for h in top4)
    assert AUDIO_CORE <= set(ids(top4))
    non_audio = [h.score for h in hits if "audio" not in h.capability.domains]
    assert all(h.score > max(non_audio, default=0) for h in hits if h.capability.id in AUDIO_CORE)


def test_audio_routing_reaches_the_mcp_server_via_the_workbench(registry):
    workbench = registry.get("dev.bouch/audio-agent-workbench-v2")
    assert "dev.bouch/reaper-mcp" in workbench.related


def test_domain_filter_only_returns_that_domain(registry):
    hits = registry.search(AUDIO_QUERY, domain="audio")
    assert hits and all("audio" in h.capability.domains for h in hits)
    assert "dev.bouch/bouch-agent-core" not in ids(hits)


def test_browse_domain_without_query_lists_every_member_deterministically(registry):
    hits = registry.search("", domain="audio", limit=50)
    assert ids(hits) == sorted(c.id for c in registry.in_domain("audio"))
    assert all(h.score == 0 for h in hits)


def test_type_filter(registry):
    hits = registry.search("", type="mcp-server")
    assert ids(hits) == ["dev.bouch/reaper-mcp"]


def test_plural_and_prefix_matching(registry):
    assert "dev.bouch/reaper-mcp" in ids(registry.search("DAWs"))
    assert "dev.bouch/audio-agent-workbench-v2" in ids(registry.search("synth"))


def test_discriminating_terms_outrank_common_ones(registry):
    """'bouch' appears in every id; 'reaper' in a few records. IDF must weight them accordingly."""
    def score(query):
        return next(h.score for h in registry.search(query) if h.capability.id == "dev.bouch/reaper-mcp")

    assert score("reaper") > 5 * score("bouch")


def test_search_is_deterministic(registry):
    runs = [[(h.capability.id, h.score) for h in registry.search(AUDIO_QUERY)] for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


def test_no_match_returns_empty(registry):
    assert registry.search("zzqx nonexistent") == []


def test_limit(registry):
    assert len(registry.search("", limit=2)) == 2


def test_get_by_id_exposes_native_manifest_and_routing(registry):
    plugin = registry.get("dev.bouch/bouch-agent-core")
    assert plugin.native.spec == "agent-plugins" and plugin.native.manifest == "plugin.json"
    skill = registry.get("dev.bouch/production-technique-reference")
    assert skill.native.manifest.endswith("/SKILL.md")
    server = registry.get("dev.bouch/reaper-mcp")
    assert server.native.manifest is None and "server.json" in server.native.note
    assert any(e.role == "contract" for e in registry.get("dev.bouch/audio-agent-workbench-v2").entrypoints)
    assert registry.get("dev.bouch/nope") is None
