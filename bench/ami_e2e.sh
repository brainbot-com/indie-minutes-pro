#!/usr/bin/env bash
# One command for the end-to-end run: every prepared AMI meeting goes through
# the phone simulator (WebM/Opus, 30 s chunks, the real upload API over HTTPS)
# into the running recorder, which transcribes it with the default engine and
# summarizes it; the follow-up agent then proposes its actions. Run on aibox1
# from the repo root:
#   bench/ami_e2e.sh              (SET=ami-ihm-mix for the headset mix)
# OUT=<earlier results file> resumes: meetings already done there are skipped.
# Meetings run one after another, so only one heavy GPU job runs at a time.
set -euo pipefail
SET=${SET:-ami-sdm}
TOKEN=${TOKEN:-secrets/sim_token}
OUT=${OUT:-data/bench/e2e/$(date -u +%Y%m%dT%H%MZ)-$SET.jsonl}
mkdir -p "$(dirname "$OUT")"
set -a; . ./.env; set +a
if [ ! -s "$TOKEN" ]; then
  umask 077
  docker compose exec -T recorder python -m recorder.cli token add --name simulator --role device < /dev/null > "$TOKEN"
fi
[ -d "data/bench/datasets/$SET" ] || docker compose --profile bench run --rm -T bench python -m bench.data ami < /dev/null
touch "$OUT"
for d in data/bench/datasets/"$SET"/*/; do
  m=$(basename "$d")
  if grep -F "\"AMI $m ($SET)\"" "$OUT" | grep -qF '"state": "done"'; then continue; fi
  python3 sim/phone_sim.py --url "$PUBLIC_URL" --cacert secrets/tls/ca.crt --token-file "$TOKEN" \
    --title "AMI $m ($SET)" --language en --wait "$d/audio.wav" \
    | python3 -c 'import json, sys; print(json.dumps(json.load(sys.stdin)))' >> "$OUT" || echo "$m failed" >&2
  tail -1 "$OUT"
done
echo "results: $OUT"
