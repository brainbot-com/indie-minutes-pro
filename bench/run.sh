#!/usr/bin/env bash
# Reproduces BENCHMARK.md on aibox1, from the repo root:
#   bench/run.sh
# Test data, the GPU-vs-CPU proof, an idle LLM baseline, every engine on every
# set (with the LLM probe running), scores and the action-item spot check.
# Resumable: finished items are skipped. ENGINES and SETS narrow it down.
set -euo pipefail
docker compose build --quiet recorder
docker compose --profile bench build --quiet bench
docker compose up -d engine-nemo engine-qwen
B() { docker compose --profile bench run --rm -T bench python -m "$@" < /dev/null; }
B bench.data ami
B bench.data mls-de
B bench.run --probe-only 300
B bench.run --cpu-proof --engines "${ENGINES:-A-iq4}"
# Engine B is left out by default since 2026-11-18 (ENGINES=A-iq4,A-q8,B brings it back).
B bench.run --engines "${ENGINES:-A-iq4,A-q8}" --sets "${SETS:-ami-sdm,ami-ihm-mix,mls-de}"
B bench.score
B bench.spotcheck
echo "done: data/bench/results.md, data/bench/spotcheck.md, data/bench/runs/"
