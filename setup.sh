#!/bin/sh
set -eu
directory=$(cd -P "$(dirname "$0")" && pwd)
destination=${PREFIX:-"$HOME/.local"}/bin
mkdir -p "$destination"
target=$destination/vpn-pin
if [ -L "$target" ] && [ "$(readlink "$target")" = "$directory/bin/vpn-pin" ]; then
    printf 'Already installed: %s\n' "$target"
    exit 0
fi
if [ -e "$target" ] || [ -L "$target" ]; then
    printf 'Refusing to replace existing file: %s\n' "$target" >&2
    exit 1
fi
ln -s "$directory/bin/vpn-pin" "$target"
printf 'Installed: %s\nKeep this checkout in place and add %s to PATH.\n' "$target" "$destination"
