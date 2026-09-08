#!/bin/sh
# Cross-compiles rmclock for the reMarkable 2 (ARMv7, static) with Docker; the result is committed.
cd "$(dirname "$0")" && docker run --rm --platform linux/arm/v7 -v "$PWD":/src -w /src alpine:3.20 \
  sh -c 'apk add -q gcc musl-dev && gcc -Wall -Wextra -Os -static -o rmclock rmclock.c && strip rmclock' && ls -l rmclock
