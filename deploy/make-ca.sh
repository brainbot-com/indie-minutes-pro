#!/usr/bin/env bash
# Private CA for the phone plus the server certificate for https://BIND_IP:8443.
# The CA is made once and kept; the server certificate is renewed by running
# this again (397 days, the most Chrome accepts). Run on the recorder's machine:
# from the repo root on aibox1, or from standalone/ (BIND_IP=<its LAN address>
# TLS_DNS_NAMES="<its names>" ../deploy/make-ca.sh secrets/tls).
set -euo pipefail
OUT=${1:-secrets/tls}
# the address phones reach the recorder on: BIND_IP, else the one in .env
IP=${BIND_IP:-$(sed -n 's/^BIND_IP=//p' .env 2>/dev/null | tail -1)}
[ -n "$IP" ] || { echo "set BIND_IP to the address phones reach (or put it in .env)" >&2; exit 1; }
NAMES=${TLS_DNS_NAMES:-$(hostname -s)}

mkdir -p "$OUT/public"
chmod 700 "$OUT"
if [ ! -f "$OUT/ca.key" ]; then
  openssl ecparam -name prime256v1 -genkey -noout -out "$OUT/ca.key"
  openssl req -x509 -new -key "$OUT/ca.key" -sha256 -days 3650 \
    -subj "/CN=${CA_NAME:-Minutes Pro private CA ($(hostname -s))}" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" \
    -out "$OUT/ca.crt"
  echo "new CA created"
fi

SAN="IP:$IP"
for n in $NAMES; do SAN="$SAN,DNS:$n"; done
openssl ecparam -name prime256v1 -genkey -noout -out "$OUT/server.key"
openssl req -new -key "$OUT/server.key" -subj "/CN=$IP" -out "$OUT/server.csr"
openssl x509 -req -in "$OUT/server.csr" -CA "$OUT/ca.crt" -CAkey "$OUT/ca.key" -CAcreateserial \
  -days 397 -sha256 -out "$OUT/server.crt" -extfile <(printf '%s\n' \
    "subjectAltName=$SAN" \
    "basicConstraints=critical,CA:FALSE" \
    "keyUsage=critical,digitalSignature" \
    "extendedKeyUsage=serverAuth" \
    "authorityKeyIdentifier=keyid,issuer" \
    "subjectKeyIdentifier=hash")
rm -f "$OUT/server.csr"
cp "$OUT/ca.crt" "$OUT/public/ca.crt"
openssl x509 -in "$OUT/ca.crt" -outform der -out "$OUT/public/ca.der"
chmod 600 "$OUT"/*.key
chmod 644 "$OUT"/*.crt "$OUT"/public/*

echo "server certificate for $SAN, valid until $(openssl x509 -in "$OUT/server.crt" -noout -enddate | cut -d= -f2)"
echo "CA fingerprint (compare on the phone after installing):"
openssl x509 -in "$OUT/ca.crt" -noout -fingerprint -sha256
