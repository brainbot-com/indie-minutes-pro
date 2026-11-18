#!/usr/bin/env bash
# Connects brainbot:agent command (indie-chat on ai-knowledge) to the indie-recorder on
# aibox1: the Meetings app for everyone, each person seeing only their own
# meetings. Run as root on ai-knowledge from a copy of this folder, with the
# recorder's service token on stdin (it never appears on a command line):
#
#   ssh aibox1 cat indie-recorder/secrets/indiechat_service_token \
#     | ssh root@ai-knowledge bash /root/indie-recorder-kit/install-ai-knowledge.sh --token-stdin
#
# The token comes from `python -m recorder.cli token add --role agent
# --trusts-identity` on aibox1. A CA certificate path may follow; without one
# the script fetches it from aibox1 and checks it against CA_SHA256 below.
#
# What it changes, each file backed up once as <file>.pre-indie-recorder:
#   1. /opt/kb/brain-tls/Caddyfile: /indie-recorder/mcp, /indie-recorder/api/v1/files/*
#      and /indie-recorder/api/v1/indiechat on https://ai-knowledge.mz.brainbot.com go
#      to https://10.1.9.7:8443, trusting the recorder's CA. Nothing else of the
#      recorder is passed through. Validated first, then a graceful reload.
#   2. /opt/kb/indiechat-src/.env: INDIE_PRIVATE_HOSTS gets ai-knowledge.mz.brainbot.com
#      (a person's own MCP server may point there), INDIE_RECORDER_TOKEN the token.
#   3. /opt/kb/indiechat-config/connectors.json: the "indie-recorder" connector,
#      with "identity": true, so the recorder learns who asks and answers that
#      person only.
#   4. /opt/kb/indiechat-config: the Meetings app, seeded once as an app for everyone.
#   5. Recreates the indie-chat app container (a few seconds) for 2 to 4, and
#      puts them back if brainbot:agent command does not come up again.
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
PATHS="/indie-recorder/mcp /indie-recorder/api/v1/files/* /indie-recorder/api/v1/indiechat /indie-recorder/connect /indie-recorder/api/v1/pair /indie-recorder/api/v1/pair/* /indie-recorder/indie-recorder.apk /indie-recorder/static/*"

[ "$(id -u)" = 0 ] || { echo "Run this as root." >&2; exit 1; }
[ -f "$HERE/indie-recorder.html" ] || { echo "indie-recorder.html must be next to this script." >&2; exit 1; }
TOKEN=""
if [ "${1:-}" = --token-stdin ]; then
	read -r TOKEN
	shift
	[ ${#TOKEN} -ge 20 ] || { echo "No token on stdin." >&2; exit 1; }
fi

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
if grep -qF "@indie_recorder path $PATHS" $TLS/Caddyfile; then
	echo "already in place"
else
	backup $TLS/Caddyfile
	cp -p $TLS/Caddyfile "$tmp/Caddyfile.before"
	CA_DER=$CA_DER PATHS=$PATHS python3 - $TLS/Caddyfile > "$tmp/Caddyfile" <<'PY'
import os, re, sys
s = open(sys.argv[1]).read()
matcher = "\t@indie_recorder path " + os.environ["PATHS"] + "\n"
if "@indie_recorder" in s:
    # an earlier install: only the list of paths grows
    s = re.sub(r"\t@indie_recorder path [^\n]*\n", lambda m: matcher, s, count=1)
else:
    route = """	# indie-recorder on aibox1: its MCP endpoint, signed file links and the
	# endpoint behind the "indie-recorder" connector, for the Meetings app and
	# meeting agents in brainbot:agent command (each needs a recorder token or a
	# signed link); the page a phone's connect code opens, the code's two
	# endpoints, the Android app and the page's stylesheet. Nothing else of the
	# recorder is passed through. Added by
	# indie-recorder/integrations/indie-chat/install-ai-knowledge.sh.
""" + matcher + """	handle @indie_recorder {
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
    s = s[:at] + route + s[at:]
sys.stdout.write(s)
PY
	put $TLS/Caddyfile < "$tmp/Caddyfile"
	if ! caddy validate > "$tmp/validate.log" 2>&1; then
		put $TLS/Caddyfile < "$tmp/Caddyfile.before"
		tail -5 "$tmp/validate.log" >&2
		echo "brain-tls did not accept the route; the Caddyfile is as it was." >&2
		exit 1
	fi
	caddy reload
	echo "updated and reloaded"
fi
sleep 2
for path in /indie-recorder/mcp /indie-recorder/api/v1/indiechat; do
	code=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" -d "{}" "https://$HOST$path")
	case $code in
		401) echo "$path works (the recorder asks for its token)" ;;
		403) echo "WARNING: aibox1 does not admit this host yet: ALLOW_CIDRS in ~/indie-recorder/.env there needs 10.1.1.26/32" ;;
		*) echo "WARNING: $path answered $code (expected 401)" ;;
	esac
done

cp -p $SRC/.env "$tmp/env.before"
[ -f $CFG/connectors.json ] && cp -p $CFG/connectors.json "$tmp/connectors.before"
cp -p $CFG/app-templates.json "$tmp/templates.before"
had_html=0
[ -f $CFG/app-templates/indie-recorder.html ] && had_html=1 && cp -p $CFG/app-templates/indie-recorder.html "$tmp/html.before"
changed=0

echo "== 2. indie-chat .env"
if grep -q "^INDIE_PRIVATE_HOSTS=.*$HOST" $SRC/.env; then
	echo "INDIE_PRIVATE_HOSTS already in place"
else
	backup $SRC/.env
	if grep -q "^INDIE_PRIVATE_HOSTS=" $SRC/.env; then
		sed -i "s/^INDIE_PRIVATE_HOSTS=\(.*\)$/INDIE_PRIVATE_HOSTS=\1,$HOST/" $SRC/.env
	else
		[ -z "$(tail -c1 $SRC/.env)" ] || echo >> $SRC/.env
		echo "INDIE_PRIVATE_HOSTS=$HOST" >> $SRC/.env
	fi
	changed=1
	echo "set INDIE_PRIVATE_HOSTS"
fi
if [ -n "$TOKEN" ] && ! grep -qxF "INDIE_RECORDER_TOKEN=$TOKEN" $SRC/.env; then
	backup $SRC/.env
	TOKEN=$TOKEN python3 - $SRC/.env <<'PY'
import os, re, sys
p = sys.argv[1]
s = open(p).read()
line = "INDIE_RECORDER_TOKEN=" + os.environ["TOKEN"]
s = re.sub(r"^INDIE_RECORDER_TOKEN=.*$", lambda m: line, s, flags=re.M) if re.search(r"^INDIE_RECORDER_TOKEN=", s, flags=re.M) \
    else s.rstrip("\n") + "\n" + line + "\n"
with open(p, "r+") as f:
    f.write(s)
    f.truncate()
PY
	changed=1
	echo "set INDIE_RECORDER_TOKEN"
elif grep -q "^INDIE_RECORDER_TOKEN=" $SRC/.env; then
	echo "INDIE_RECORDER_TOKEN already in place"
else
	echo "WARNING: no INDIE_RECORDER_TOKEN (run with --token-stdin): the connector cannot sign in"
fi

echo "== 3. The indie-recorder connector"
[ -f $CFG/connectors.json ] || { echo "{}" > $CFG/connectors.json; chmod 644 $CFG/connectors.json; }
backup $CFG/connectors.json
if python3 - $CFG/connectors.json "https://$HOST/indie-recorder/api/v1/indiechat" <<'PY'
import json, sys
p, url = sys.argv[1], sys.argv[2]
c = json.load(open(p))
want = {"url": url, "headers": {"Authorization": "Bearer ${INDIE_RECORDER_TOKEN}"}, "identity": True, "timeout": 60000}
if c.get("indie-recorder") == want:
    sys.exit(1)
c["indie-recorder"] = want
with open(p, "r+") as f:
    f.write(json.dumps(c, indent=2, ensure_ascii=False) + "\n")
    f.truncate()
PY
then changed=1; echo "connector added"; else echo "already in place"; fi

echo "== 4. The Meetings app, for everyone"
if cmp -s "$HERE/indie-recorder.html" $CFG/app-templates/indie-recorder.html; then
	echo "html unchanged"
else
	install -m 644 "$HERE/indie-recorder.html" $CFG/app-templates/indie-recorder.html
	changed=1
	echo "html copied"
fi
backup $CFG/app-templates.json
if python3 - $CFG/app-templates.json <<'PY'
import json, sys
p = sys.argv[1]
t = json.load(open(p))
want = {"file": "indie-recorder.html", "key": "indie-recorder", "name": "Meetings", "icon": "🔴",
        "description": "Your meetings from the indie recorder on aibox1: summaries, action items, speakers, Drive or local, your phones. Each person sees only their own.",
        "refresh": True, "seed": {"id": "indie-recorder", "state": {}}}
have = next((e for e in t if e.get("key") == "indie-recorder"), None)
if have == want:
    sys.exit(1)
if have:
    t[t.index(have)] = want
else:
    t.append(want)
with open(p, "r+") as f:
    f.write(json.dumps(t, indent=2, ensure_ascii=False) + "\n")
    f.truncate()
PY
then changed=1; echo "entry set (seeded for everyone at the next start)"; else echo "already in place"; fi

echo "== 5. indie-chat app"
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
	echo "brainbot:agent command is up again"
	docker logs --since 3m indiechat-app-1 2>&1 | grep -iE "connector|template|seeded|private host|error" | tail -8 || true
else
	echo "brainbot:agent command did not come up: putting .env, connectors and templates back" >&2
	docker logs --since 3m indiechat-app-1 2>&1 | tail -15 >&2 || true
	cp -p "$tmp/env.before" $SRC/.env
	if [ -f "$tmp/connectors.before" ]; then cp -p "$tmp/connectors.before" $CFG/connectors.json; else rm -f $CFG/connectors.json; fi
	cp -p "$tmp/templates.before" $CFG/app-templates.json
	if [ $had_html = 1 ]; then cp -p "$tmp/html.before" $CFG/app-templates/indie-recorder.html; else rm -f $CFG/app-templates/indie-recorder.html; fi
	up && echo "back as before" >&2
	exit 1
fi
