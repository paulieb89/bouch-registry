#!/usr/bin/env bash
# Accept a deployed registry through its real HTTPS boundary: the public routes,
# the claude.ai GET/DELETE guard, structured tool errors, and the invariant that
# the MCP view and the static view are one dataset.
#   scripts/remote-acceptance.sh [BASE_URL]   (default https://registry.bouch.dev)
# The MCP protocol itself is exercised by scripts/inspector-smoke.sh.
set -euo pipefail
BASE="${1:-https://registry.bouch.dev}"
BASE="${BASE%/}"
py() { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }
post() {
  curl -s -X POST "$BASE/mcp" -H 'content-type: application/json' \
    -H 'accept: application/json, text/event-stream' -d "$1"
}

curl -sf "$BASE/health" | py "assert d['status']=='ok' and d['capabilities']>0; print('health:', d)"

curl -sf "$BASE/registry.json" > /tmp/bouch-registry-static.$$.json
python3 -c "
import json,sys
d=json.load(open('/tmp/bouch-registry-static.$$.json'))
print('registry.json:', len(d['capabilities']), 'capabilities,', len(d['domains']), 'domains')
assert d['registry']=='dev.bouch/registry'
"

# One dataset: bouch://registry read over MCP must equal the static file byte for byte.
INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"acceptance","version":"0"}}}'
post "$INIT" | py "assert d['result']['serverInfo']['name']=='bouch-registry'; print('initialize:', d['result']['serverInfo'])"
post '{"jsonrpc":"2.0","id":2,"method":"resources/read","params":{"uri":"bouch://registry"}}' \
  | py "
import json
via_mcp = json.loads(d['result']['contents'][0]['text'])
static = json.load(open('/tmp/bouch-registry-static.$$.json'))
assert via_mcp == static, 'MCP and /registry.json disagree'
print('dataset consistency: MCP bouch://registry == /registry.json')
"
rm -f /tmp/bouch-registry-static.$$.json

# Realistic routing: an agent asking about the audio domain must reach the workbench.
post '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_capabilities","arguments":{"query":"sound design synthesis render verification","domain":"audio"}}}' \
  | py "
hits = d['result']['structuredContent']['hits']
print('routing:', [h['id'] for h in hits])
assert hits and hits[0]['domains'] == ['audio'] or 'audio' in hits[0]['domains']
"

# Failure behaviour: bad input must be a structured error, not an empty success.
post '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_capability","arguments":{"id":"dev.bouch/nope"}}}' \
  | py "assert d['result']['isError'] and 'search_capabilities' in d['result']['content'][0]['text']; print('unknown id ->', d['result']['content'][0]['text'][:60])"
post '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"search_capabilities","arguments":{"domain":"cooking"}}}' \
  | py "assert d['result']['isError'] and 'Known domains' in d['result']['content'][0]['text']; print('unknown domain ->', d['result']['content'][0]['text'][:60])"
post '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"install_capability","arguments":{}}}' \
  | py "assert d.get('error') or d['result']['isError']; print('unknown tool -> rejected')"

# Remote entrypoint reading: a declared path resolves at the record's pinned tag;
# an undeclared path and an unpublished source fail with a named cause.
post '{"jsonrpc":"2.0","id":7,"method":"resources/read","params":{"uri":"bouch://source/dev.bouch/audio/skills/electronic-production/SKILL.md"}}' \
  | py "
import hashlib
c = d['result']['contents'][0]; m = c['_meta']; raw = c['text'].encode()
assert m['ref'] == 'v0.1.0-experimental' and len(m['commit']) == 40
assert m['git_blob'] == hashlib.sha1(b'blob %d\\0' % len(raw) + raw).hexdigest(), 'served bytes do not match git_blob'
print('source read:', m['path'], '@', m['ref'], m['commit'][:12], 'blob', m['git_blob'][:12])
"
post '{"jsonrpc":"2.0","id":8,"method":"resources/read","params":{"uri":"bouch://source/dev.bouch/audio/tools/analyze.py"}}' \
  | py "assert 'not a declared entrypoint' in d['error']['message']; print('undeclared path ->', d['error']['message'][:60])"
post '{"jsonrpc":"2.0","id":9,"method":"resources/read","params":{"uri":"bouch://source/dev.bouch/audio-agent-workbench-v2/CLAUDE.md"}}' \
  | py "assert 'no published remote source' in d['error']['message']; print('unpublished source ->', d['error']['message'][:60])"

# Transport guards claude.ai depends on.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$BASE/mcp" || true)
[ "$code" = "200" ] || { echo "GET /mcp returned $code, expected 200 SSE"; exit 1; }
echo "GET /mcp: 200 held-open event stream"
[ "$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$BASE/mcp")" = "405" ]
echo "DELETE /mcp: 405"

echo "remote acceptance ok: $BASE"
