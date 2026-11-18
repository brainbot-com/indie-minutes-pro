#!/usr/bin/env bash
# Connects brainbot:brain (indie-chat on ai-knowledge) to the indie-recorder on
# aibox1, for the Meetings app and a meeting agent. Run as root on ai-knowledge
# from a copy of this folder:
#
#   bash install-ai-knowledge.sh            # fetches the CA certificate from aibox1
#   bash install-ai-knowledge.sh ca.crt     # or uses this copy of it
#
# Either way the certificate must have the fingerprint in CA_SHA256 below: it is
# the public part of the recorder's private CA.
#
# What it changes, each file backed up once as <file>.pre-indie-recorder:
#   1. /opt/kb/brain-tls/Caddyfile: /indie-recorder/mcp and
#      /indie-recorder/api/v1/files/* on https://ai-knowledge.mz.brainbot.com go
#      to https://10.1.9.7:8443, trusting the recorder's CA. Nothing else of the
#      recorder is passed through: the MCP needs the recorder's agent token, a
#      file link is signed and expires after two hours. Validated first, then a
#      graceful `caddy reload`.
#   2. /opt/kb/indiechat-src/.env: INDIE_PRIVATE_HOSTS gets
#      ai-knowledge.mz.brainbot.com, so a person's own MCP server may point there
#      (the name resolves to the private 10.1.1.26).
#   3. /opt/kb/indiechat-config: the Meetings app template. No "seed": it waits
#      in the template picker, nobody gets it unasked.
#   4. Recreates the indie-chat app container (a few seconds) for 2 and 3, and
#      puts 2 and 3 back if brainbot:brain does not come up again.
# Running it again changes nothing that is already in place.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
HOST=ai-knowledge.mz.brainbot.com
RECORDER=10.1.9.7
TLS=/opt/kb/brain-tls
SRC=/opt/kb/indiechat-src
CFG=/opt/kb/indiechat-config
COMPOSE="docker compose -p indiechat -f $SRC/docker-compose.ai-knowledge.yml"
CA_SHA256=${CA_SHA256:-E6:89:A7:83:FA:CE:DC:3C:1E:82:E7:A9:8F:45:6B:54:8A:56:F6:2D:ED:E2:E4:83:F5:43:F9:40:07:F1:2D:9F}

[ "$(id -u)" = 0 ] || { echo "Run this as root." >&2; exit 1; }
[ -f "$HERE/indie-recorder.html" ] || { echo "indie-recorder.html must be next to this script." >&2; exit 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
if [ $# -ge 1 ]; then cp "$1" "$tmp/ca.crt"; else curl -fsS -o "$tmp/ca.crt" "http://$RECORDER:8480/ca.crt"; fi
fp=$(openssl x509 -in "$tmp/ca.crt" -noout -fingerprint -sha256 | cut -d= -f2)
[ "$fp" = "$CA_SHA256" ] || { echo "The CA's fingerprint is $fp, expected $CA_SHA256." >&2; exit 1; }
CA_DER=$(openssl x509 -in "$tmp/ca.crt" -outform der | base64 -w0)

backup() { [ -e "$1.pre-indie-recorder" ] || cp -p "$1" "$1.pre-indie-recorder"; }
# Writes stdin into the file in place: the Caddyfile is bind-mounted into
# brain-tls as a single file, and a new inode would not reach the container.
put() { python3 -c 'import sys; f = open(sys.argv[1], "r+"); f.write(sys.stdin.read()); f.truncate()' "$1"; }
caddy() { docker exec brain-tls caddy "$@" --config /etc/caddy/Caddyfile --adapter caddyfile; }

echo "== 1. Route in brain-tls"
if grep -q "@indie_recorder" $TLS/Caddyfile; then
	echo "already in place"
else
	backup $TLS/Caddyfile
	cp -p $TLS/Caddyfile "$tmp/Caddyfile.before"
	CA_DER=$CA_DER python3 - $TLS/Caddyfile > "$tmp/Caddyfile" <<'PY'
import os, sys
s = open(sys.argv[1]).read()
route = """	# indie-recorder on aibox1: its MCP endpoint and signed file links, for the
	# Meetings app and meeting agents in brainbot:brain. The MCP needs the
	# recorder's agent token, a file link is signed and expires after two hours;
	# nothing else of the recorder is passed through. Added by
	# indie-recorder/integrations/indie-chat/install-ai-knowledge.sh.
	@indie_recorder path /indie-recorder/mcp /indie-recorder/api/v1/files/*
	handle @indie_recorder {
		uri strip_prefix /indie-recorder
		reverse_proxy https://10.1.9.7:8443 {
			# the recorder's Caddy serves the site 10.1.9.7 only
			header_up Host 10.1.9.7:8443
			flush_interval -1
			transport http {
				tls_server_name 10.1.9.7
				tls_trust_pool inline {
					trust_der %s
				}
			}
		}
	}
""" % os.environ["CA_DER"]
site = s.index("ai-knowledge.mz.brainbot.com {\n")
at = s.index("\timport site\n", site)
sys.stdout.write(s[:at] + route + s[at:])
PY
	put $TLS/Caddyfile < "$tmp/Caddyfile"
	if ! caddy validate > "$tmp/validate.log" 2>&1; then
		put $TLS/Caddyfile < "$tmp/Caddyfile.before"
		tail -5 "$tmp/validate.log" >&2
		echo "brain-tls did not accept the route; the Caddyfile is as it was." >&2
		exit 1
	fi
	caddy reload
	echo "added and reloaded"
fi
sleep 2
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" -d "{}" "https://$HOST/indie-recorder/mcp")
case $code in
	401) echo "route works (the recorder asks for its token)" ;;
	403) echo "WARNING: aibox1 does not admit this host yet: ALLOW_CIDRS in ~/indie-recorder/.env there needs 10.1.1.26/32" ;;
	*) echo "WARNING: the route answered $code (expected 401)" ;;
esac
echo "brainbot:brain: $(curl -s -o /dev/null -w "%{http_code}" "https://$HOST/api/branding")"

echo "== 2. INDIE_PRIVATE_HOSTS"
cp -p $SRC/.env "$tmp/env.before"
cp -p $CFG/app-templates.json "$tmp/templates.before"
changed=0
if grep -q "^INDIE_PRIVATE_HOSTS=.*$HOST" $SRC/.env; then
	echo "already in place"
else
	backup $SRC/.env
	if grep -q "^INDIE_PRIVATE_HOSTS=" $SRC/.env; then
		sed -i "s/^INDIE_PRIVATE_HOSTS=\(.*\)$/INDIE_PRIVATE_HOSTS=\1,$HOST/" $SRC/.env
	else
		[ -z "$(tail -c1 $SRC/.env)" ] || echo >> $SRC/.env
		echo "INDIE_PRIVATE_HOSTS=$HOST" >> $SRC/.env
	fi
	changed=1
	echo "set: $(grep '^INDIE_PRIVATE_HOSTS=' $SRC/.env)"
fi

echo "== 3. Meetings app template"
had_html=0
[ -f $CFG/app-templates/indie-recorder.html ] && had_html=1 && cp -p $CFG/app-templates/indie-recorder.html "$tmp/html.before"
if cmp -s "$HERE/indie-recorder.html" $CFG/app-templates/indie-recorder.html; then
	echo "html unchanged"
else
	install -m 644 "$HERE/indie-recorder.html" $CFG/app-templates/indie-recorder.html
	changed=1
	echo "html copied"
fi
if python3 -c 'import json, sys; sys.exit(0 if any(e.get("key") == "indie-recorder" for e in json.load(open(sys.argv[1]))) else 1)' $CFG/app-templates.json; then
	echo "entry already in app-templates.json"
else
	backup $CFG/app-templates.json
	python3 - $CFG/app-templates.json <<'PY'
import json, sys
p = sys.argv[1]
t = json.load(open(p))
t.append({"file": "indie-recorder.html", "key": "indie-recorder", "name": "Meetings", "icon": "🎙️",
          "description": "Meetings recorded with the indie recorder on aibox1: summaries, action items, speakers, Drive or local. Needs your own indie-recorder MCP server.",
          "refresh": True})
with open(p, "r+") as f:
    f.write(json.dumps(t, indent=2, ensure_ascii=False) + "\n")
    f.truncate()
PY
	changed=1
	echo "entry added"
fi

echo "== 4. indie-chat app"
if [ $changed = 0 ]; then
	echo "nothing changed, no restart"
	exit 0
fi
up() {
	$COMPOSE up -d --force-recreate app
	for _ in $(seq 60); do
		[ "$(curl -s -o /dev/null -w "%{http_code}" "https://$HOST/api/branding")" = 200 ] && return 0
		sleep 2
	done
	return 1
}
if up; then
	echo "brainbot:brain is up again"
	docker logs --since 3m indiechat-app-1 2>&1 | grep -iE "template|private host|error" | tail -5 || true
else
	echo "brainbot:brain did not come up: putting .env and the templates back" >&2
	docker logs --since 3m indiechat-app-1 2>&1 | tail -15 >&2 || true
	cp -p "$tmp/env.before" $SRC/.env
	cp -p "$tmp/templates.before" $CFG/app-templates.json
	if [ $had_html = 1 ]; then cp -p "$tmp/html.before" $CFG/app-templates/indie-recorder.html; else rm -f $CFG/app-templates/indie-recorder.html; fi
	up && echo "back as before" >&2
	exit 1
fi
