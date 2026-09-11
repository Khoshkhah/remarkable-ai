#!/bin/sh
# Cross-compiles rmclock, rmdash and rmvocab for the reMarkable 2 (ARMv7, static musl); the results are committed.
# Needs an ARM compiler: a local one if it is there (seconds), otherwise Docker (minutes, and Docker Desktop
# has to be running). Get the local one once, no root needed:
#   mkdir -p ~/.local/share/armv7-musl && cd ~/.local/share/armv7-musl \
#     && curl -O https://toolchains.bootlin.com/downloads/releases/toolchains/armv7-eabihf/tarballs/armv7-eabihf--musl--stable-2024.02-1.tar.bz2 \
#     && tar xf *.tar.bz2 --strip-components=1 && rm *.tar.bz2
cd "$(dirname "$0")" || exit 1
CC="$HOME/.local/share/armv7-musl/bin/arm-linux-gcc"
if [ -x "$CC" ]; then
  for p in rmclock rmdash rmvocab; do
    "$CC" -Wall -Wextra -Os -static -o "$p" "$p.c" -lm || exit 1
    "$HOME/.local/share/armv7-musl/bin/arm-linux-strip" "$p"
  done
else
  docker run --rm --platform linux/arm/v7 -v "$PWD":/src -w /src alpine:3.20 \
    sh -c 'apk add -q gcc musl-dev && for p in rmclock rmdash rmvocab; do gcc -Wall -Wextra -Os -static -o $p $p.c -lm && strip $p; done' || exit 1
fi
ls -l rmclock rmdash rmvocab
