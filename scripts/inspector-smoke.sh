#!/usr/bin/env bash
# Exercise a running registry through an independent MCP client (the official
# MCP Inspector CLI, TypeScript SDK) rather than this repo's own FastMCP client.
#   scripts/inspector-smoke.sh [MCP_URL]   (default http://127.0.0.1:8080/mcp)
set -euo pipefail
URL="${1:-http://127.0.0.1:8080/mcp}"
I=(npx -y @modelcontextprotocol/inspector@latest --cli "$URL" --transport http)
py() { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }

"${I[@]}" --method tools/list | py "names=sorted(t['name'] for t in d['tools']); print('tools:', names); assert names==['get_capability','list_domains','search_capabilities']"
"${I[@]}" --method resources/list | py "u=[r['uri'] for r in d['resources']]; print('resources:', u); assert 'bouch://registry' in u"
"${I[@]}" --method resources/templates/list | py "print('templates:', [t['uriTemplate'] for t in d['resourceTemplates']])"
"${I[@]}" --method tools/call --tool-name search_capabilities \
  --tool-arg 'query=I want to create sophisticated original electronic sounds. What existing Bouch capabilities should I inspect before building anything?' \
  | py "h=d['structuredContent']['hits']; [print('  ', x['score'], x['id']) for x in h]; assert h and 'audio' in h[0]['domains']"
"${I[@]}" --method tools/call --tool-name get_capability --tool-arg 'id=dev.bouch/bouch-agent-core' \
  | py "n=d['structuredContent']['native']; print('native:', n); assert n['manifest']=='plugin.json'"
"${I[@]}" --method resources/read --uri 'bouch://source/dev.bouch/audio/skills/electronic-production/SKILL.md' \
  | py "c=d['contents'][0]; m=c['_meta']; print('source read:', m['ref'], m['commit'][:12], m['git_blob'][:12]); assert c['text'].startswith('---') and m['ref']=='v0.1.0-experimental'"
echo "smoke ok: $URL"
