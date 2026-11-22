#!/bin/bash
# The recorder's keys, made once on the box (the service only reads secrets/):
#   secrets/data.key  encryption at rest: meeting files and the index
#   secrets/seal.pem  the sealing key phones encrypt their chunks for
# Existing keys are kept: a new data key would make every meeting unreadable.
# Keep a copy of both somewhere off this box.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
mkdir -p secrets
if [ -e secrets/data.key ]; then
	echo "secrets/data.key exists, kept"
else
	head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n' > secrets/data.key
	echo >> secrets/data.key
	echo "made secrets/data.key"
fi
if [ -e secrets/seal.pem ]; then
	echo "secrets/seal.pem exists, kept"
else
	openssl ecparam -name prime256v1 -genkey -noout | openssl pkcs8 -topk8 -nocrypt -out secrets/seal.pem
	echo "made secrets/seal.pem"
fi
chmod 600 secrets/data.key secrets/seal.pem
echo "Back up secrets/data.key and secrets/seal.pem off this box: without data.key the meetings cannot be read."
