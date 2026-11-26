#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
echo "Building static ARMv7 binary for Kindle Paperwhite 10th Gen (PW4)..."
GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0 go build -ldflags="-s -w" -o hermes-client-arm ./cmd/hermes-client
echo "Built: $(pwd)/hermes-client-arm"
echo "Copy this file into /mnt/us/extensions/kindlehermes/bin/ on your Kindle."
