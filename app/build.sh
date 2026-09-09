#!/bin/sh
# Cross-compiles rmclock and rmdash for the reMarkable 2 (ARMv7, static) with Docker; the results are committed.
cd "$(dirname "$0")" && docker run --rm --platform linux/arm/v7 -v "$PWD":/src -w /src alpine:3.20 \
  sh -c 'apk add -q gcc musl-dev && for p in rmclock rmdash rmvocab; do gcc -Wall -Wextra -Os -static -o $p $p.c -lm && strip $p; done' && ls -l rmclock rmdash rmvocab
