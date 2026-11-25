#!/usr/bin/env bash
# Pin the image tags in indiebox/indie-minutes-pro.compose.yml to one release.
#
#   indiebox/pin-images.sh --version 1.2.0 [--file <compose>] [--stdout]
#   indiebox/pin-images.sh --check         [--file <compose>]
#
# Both images in the app file are written as
#
#   ghcr.io/brainbot-com/indie-minutes-pro[-engine]:${INDIE_MINUTES_PRO_TAG:-<pin>}
#
# so a box can still override the version through its shared .env, while the
# default is the released version rather than a floating tag. This rewrites
# those defaults and the indiebox.app.version label and nothing else. The
# indiebox-app-release action calls it from .github/workflows/release.yml, and
# --check runs on every change to indiebox/ (.github/workflows/compose-check.yml).
#
# One variable for both images on purpose: the recorder and its engine speak a
# job protocol of their own (engines/jobserver.py, the model download in
# health), so a box moves both or neither.
#
# Adapted from brainbot-com/indie-chat's indiebox/pin-images.sh: the rewrite
# edits the pin tokens in place (a YAML round trip would drop the comments that
# document the app), asserts an exact count per image, and a final sweep proves
# every ghcr.io reference in the file ended up pinned.
#
# Exit codes: 0 ok, 2 usage, 3 the file's shape drifted, 4 an assertion failed.
set -euo pipefail

# The compose file hardcodes the owner (it is uploaded verbatim to a box, so it
# cannot interpolate github.repository_owner the way the workflows do).
OWNER=brainbot-com

# repo|variable|expected occurrences
IMAGES='indie-minutes-pro|INDIE_MINUTES_PRO_TAG|1
indie-minutes-pro-engine|INDIE_MINUTES_PRO_TAG|1'

# Every ghcr.io reference in the file, and the total lines this script may
# touch: those references plus the version label. Both are asserted, so adding
# a third image to the app file fails here until the table above learns it.
TOTAL_REFS=2
MAX_CHANGED=3

# --check pins to a sentinel and throws the result away, so the PR check
# exercises the real rewrite instead of a weaker approximation of it.
CHECK_VERSION=0.0.0-check

file=indiebox/indie-minutes-pro.compose.yml
version=""
mode=pin
to_stdout=0

die() { local code=$1; shift; echo "error: $*" >&2; exit "$code"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --version) version="${2:?--version needs a value}"; shift 2 ;;
    --file)    file="${2:?--file needs a value}"; shift 2 ;;
    --check)   mode=check; shift ;;
    --stdout)  to_stdout=1; shift ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *)         die 2 "unknown argument '$1'" ;;
  esac
done

[ -f "$file" ] || die 2 "compose file not found: $file"

if [ "$mode" = check ]; then
  [ -z "$version" ] || die 2 "--check takes no --version"
  version="$CHECK_VERSION"
else
  [ -n "$version" ] || die 2 "usage: $0 --version <semver> | --check"
  # Bare semver, matching indiebox.app.version's format: the git tag carries
  # the v, the image tag does not.
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] \
    || die 2 "not a bare semver version: '$version' (expected e.g. 1.2.0, no leading v)"
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
work="$tmpdir/compose.yml"
out="$tmpdir/out.yml"
cp "$file" "$work"

# Replace the text between a fixed prefix and the next terminator, on every
# occurrence, and fail unless the occurrence count is exactly `want`. The count
# check lives in END and the caller only installs the output on exit 0, so a
# miscount cannot leave a half-pinned file behind.
rewrite() { # <prefix> <terminator> <replacement> <want>
  # `set -e` does NOT apply inside a function invoked in a `||` list, so awk's
  # status is captured by hand: without this an assertion failure below would
  # fall through to the `mv` and be reported as success.
  local rc=0
  awk -v prefix="$1" -v term="$2" -v repl="$3" -v want="$4" '
    {
      line = $0; head = ""
      while ((i = index(line, prefix)) > 0) {
        head = head substr(line, 1, i - 1) prefix
        rest = substr(line, i + length(prefix))
        j = index(rest, term)
        if (j == 0) {
          printf("%s:%d: no closing %s after %s\n", FILENAME, FNR, term, prefix) > "/dev/stderr"
          bad = 3; exit 3
        }
        inner = substr(rest, 1, j - 1)
        # An image default is a plain tag. A nested ${} (as in the volume paths,
        # ${SECURE_STORAGE_DIR:-${DATA_DIR:-/data}}) would make "the next }" the
        # wrong boundary, so refuse rather than corrupt the line.
        if (term == "}" && index(inner, "${") > 0) {
          printf("%s:%d: nested ${} inside an image default\n", FILENAME, FNR) > "/dev/stderr"
          bad = 3; exit 3
        }
        head = head repl term
        line = substr(rest, j + 1)
        n++
      }
      print head line
    }
    END {
      if (bad) exit bad
      if (n + 0 != want) {
        printf("matched %d occurrence(s) of %s, expected %d\n", n + 0, prefix, want) > "/dev/stderr"
        exit 3
      }
    }
  ' "$work" > "$out" || rc=$?
  [ "$rc" = 0 ] || return "$rc"
  mv "$out" "$work"
}

while IFS='|' read -r repo var want; do
  [ -n "$repo" ] || continue
  rewrite "ghcr.io/$OWNER/$repo:\${$var:-" '}' "$version" "$want" \
    || die $? "could not pin ghcr.io/$OWNER/$repo (\$$var) in $file"
done <<EOF
$IMAGES
EOF

# The app version label and the image pins are one release, so they move
# together or not at all. This is the line the box reports as the installed
# version: a label that outruns its images is a lie nobody notices until a bug
# report names a version that was never shipped.
rewrite 'indiebox.app.version: "' '"' "$version" 1 \
  || die $? "could not set indiebox.app.version in $file"

# --- table-independent post-conditions ---------------------------------------
# The loop above proves each KNOWN image was pinned. These prove no UNKNOWN one
# was left floating, which is the failure the table cannot catch by itself.
refs=$(grep -c 'ghcr\.io/' "$work" || true)
[ "$refs" = "$TOTAL_REFS" ] \
  || die 3 "expected $TOTAL_REFS ghcr.io references in $file, found $refs -- a new image the table in $0 does not know about, or a comment that mentions the registry host (only real references may)"

pinned=$(grep -oE "ghcr\.io/[^[:space:]]+:\\\$\{[A-Z_]+:-$version\}" "$work" | wc -l)
[ "$pinned" = "$TOTAL_REFS" ] \
  || die 4 "$((refs - pinned)) ghcr.io reference(s) are not pinned to $version"

if grep -n ':-latest}' "$work" >&2; then
  die 4 "a floating ':-latest' default survived the rewrite"
fi

# Only the pin tokens and the label may move: same number of lines in and out,
# and at most one changed line per pin plus the label. Catches an accidental
# whole-file rewrite even when every assertion above passes.
changed=$(diff "$file" "$work" | grep -c '^<' || true)
added=$(diff "$file" "$work" | grep -c '^>' || true)
[ "$changed" = "$added" ] || die 4 "rewrite changed the line count ($changed removed, $added added)"
[ "$changed" -le "$MAX_CHANGED" ] \
  || die 4 "rewrite touched $changed lines, expected at most $MAX_CHANGED"

if [ "$mode" = check ]; then
  echo "ok: $file can be pinned ($TOTAL_REFS references + the version label)"
  exit 0
fi

if [ "$to_stdout" = 1 ]; then
  cat "$work"
else
  cat "$work" > "$file"   # `cat >` not `mv`, to keep the file's mode and owner
  echo "pinned $file to $version ($changed line(s) changed)"
fi
