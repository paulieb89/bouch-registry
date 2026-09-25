#!/usr/bin/env bash
# Exercise a running registry through an independent MCP client (the official
# MCP Inspector CLI, TypeScript SDK) rather than this repo's own FastMCP client.
#   scripts/inspector-smoke.sh [MCP_URL]   (default http://127.0.0.1:8080/mcp)
set -euo pipefail
URL="${1:-http://127.0.0.1:8080/mcp}"
I=(npx -y @modelcontextprotocol/inspector@latest --cli "$URL" --transport http)
py() { python3 -c "import json,sys,os,hashlib; d=json.load(sys.stdin); $1"; }

"${I[@]}" --method tools/list | py "names=sorted(t['name'] for t in d['tools']); print('tools:', names); assert names==['get_capability','list_domains','read_capability_entrypoint','search_capabilities']"
"${I[@]}" --method resources/list | py "u=[r['uri'] for r in d['resources']]; print('resources:', u); assert 'bouch://registry' in u"
"${I[@]}" --method resources/templates/list | py "print('templates:', [t['uriTemplate'] for t in d['resourceTemplates']])"
"${I[@]}" --method tools/call --tool-name search_capabilities \
  --tool-arg 'query=I want to create sophisticated original electronic sounds. What existing Bouch capabilities should I inspect before building anything?' \
  | py "h=d['structuredContent']['hits']; [print('  ', x['score'], x['id']) for x in h]; assert h and 'audio' in h[0]['domains']"
"${I[@]}" --method tools/call --tool-name get_capability --tool-arg 'id=dev.bouch/bouch-agent-core' \
  | py "n=d['structuredContent']['native']; print('native:', n); assert n['manifest']=='plugin.json'"

# Derived, never hardcoded: dev.bouch/audio's pinned ref advances on every
# release bump (see registry commit 200fb2b) — read it from the server under
# test instead of baking in whatever tag was current when this script was written.
export AUDIO_REF=$("${I[@]}" --method tools/call --tool-name get_capability --tool-arg 'id=dev.bouch/audio' \
  | py "print(d['structuredContent']['source']['ref'])")

"${I[@]}" --method resources/read --uri 'bouch://source/dev.bouch/audio/skills/electronic-production/SKILL.md' \
  | py "c=d['contents'][0]; m=c['_meta']; raw=c['text'].encode(); print('source read:', m['ref'], m['commit'][:12], m['git_blob'][:12]); assert c['text'].startswith('---') and m['ref']==os.environ['AUDIO_REF']; assert m['git_blob']==hashlib.sha1(b'blob %d\0'%len(raw)+raw).hexdigest(), 'served bytes do not match git_blob'"
"${I[@]}" --method tools/call --tool-name read_capability_entrypoint \
  --tool-arg 'capability_id=dev.bouch/audio' --tool-arg 'entrypoint=skills/electronic-production/SKILL.md' \
  | py "s=d['structuredContent']; raw=s['content'].encode(); print('tool read:', s['ref'], s['commit'][:12], s['git_blob'][:12]); assert s['content'].startswith('---') and s['ref']==os.environ['AUDIO_REF']; assert s['git_blob']==hashlib.sha1(b'blob %d\0'%len(raw)+raw).hexdigest(), 'served bytes do not match git_blob'"
echo "smoke ok: $URL"
