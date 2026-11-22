#!/usr/bin/env bash
# Copy this checkout to aibox1 and (re)start the stack. Run from the repo root
# on a machine that can ssh to the box (over the VPN):
#   deploy/deploy.sh [ssh-host]            (default: aibox1)
# SSH_OPTS can carry extra ssh options, e.g. SSH_OPTS="-F ~/vpn/ssh_config".
# First run: creates .env from .env.example, the private CA and server
# certificate, and an admin token in secrets/admin_token (mode 600).
set -euo pipefail
HOST=${1:-aibox1}
DEST=${DEST:-indie-recorder}
SSH="ssh ${SSH_OPTS:-}"

rsync -az --delete -e "$SSH" \
  --exclude .git --exclude data --exclude models --exclude secrets --exclude .env \
  --exclude __pycache__ --exclude .pytest_cache --exclude 'bench/data' --exclude 'bench/runs' \
  ./ "$HOST:$DEST/"

$SSH "$HOST" "bash -s" <<EOF
set -euo pipefail
cd "$DEST"
mkdir -p data models secrets
chmod 700 secrets
[ -f .env ] || cp .env.example .env
set -a; . ./.env; set +a
[ -f secrets/tls/server.crt ] || BIND_IP=\$BIND_IP deploy/make-ca.sh secrets/tls
docker compose build --quiet recorder
docker compose up -d --remove-orphans
for i in \$(seq 30); do curl -fsS http://127.0.0.1:18000/api/v1/health >/dev/null 2>&1 && break; sleep 1; done
if [ ! -s secrets/admin_token ]; then
  umask 077
  docker compose exec -T recorder python -m recorder.cli token add --name admin --role admin < /dev/null > secrets/admin_token
  echo "admin token written to ~/$DEST/secrets/admin_token"
fi
mkdir -p ~/.config/systemd/user
cp deploy/systemd/indie-recorder-followup.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now indie-recorder-followup.timer >/dev/null
curl -fsS http://127.0.0.1:18000/api/v1/health; echo
EOF
