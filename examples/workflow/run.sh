#!/bin/sh
set -eu

bundle=build/order.dsp.json
snapshot=build/order.flow.json

suspend() {
    status=0
    "$@" || status=$?
    if [ "$status" -ne 7 ]; then
        printf 'expected suspension (exit 7), got %s\n' "$status" >&2
        exit 1
    fi
}

suspend uv run diaspore run "$bundle" --durable --deterministic --snapshot "$snapshot"
suspend build/diaspore-go resume "$bundle" "$snapshot" --send '"approved"'
suspend node runtimes/ts/src/cli.ts resume \
    "$bundle" "$snapshot" --send '{"txn":"tx-1"}'
uv run diaspore resume "$bundle" "$snapshot" --send '"delivered"'
