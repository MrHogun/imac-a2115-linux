#!/usr/bin/env bash
# Mount the macOS System volume read-only, to extract Apple's speaker tuning.
#
# apfs-fuse can only read APFS, never write it, so the macOS install is not at
# risk. On Catalina and later a Mac has two volumes in one container: the Data
# volume holds your files, the System volume holds System/Library. Only the
# latter carries AppleHDA.kext, and it is usually not volume 0 -- so this walks
# the container until it finds the one that has it.
#
# Usage: sudo tools/mount-macos.sh [device] [mountpoint]
set -euo pipefail

DEV="${1:-}"
MNT="${2:-/mnt/macos}"

[[ $EUID -eq 0 ]] || { echo "run with sudo: sudo $0" >&2; exit 1; }

if ! command -v apfs-fuse >/dev/null; then
  echo "apfs-fuse is not installed."
  echo "  Arch:   an AUR helper, e.g.  yay -S apfs-fuse-git"
  echo "  Debian: build from https://github.com/sgan81/apfs-fuse"
  exit 1
fi

if [[ -z $DEV ]]; then
  DEV=$(lsblk -rno NAME,FSTYPE | awk '$2=="apfs"{print "/dev/"$1; exit}')
  [[ -n $DEV ]] || { echo "no APFS partition found; pass the device explicitly" >&2; exit 1; }
  echo "found APFS partition: $DEV"
fi

mkdir -p "$MNT"
umount "$MNT" 2>/dev/null || true

for vol in 0 1 2 3 4 5; do
  umount "$MNT" 2>/dev/null || true
  apfs-fuse -v "$vol" -o allow_other "$DEV" "$MNT" 2>/dev/null || continue
  sleep 1
  if [[ -d $MNT/root/System/Library/Extensions ]]; then
    echo "volume $vol is the System volume -- mounted at $MNT"
    echo
    echo "Extract this machine's tuning with (layout id from macOS: ioreg -l | grep layout-id):"
    echo "  tools/extract-apple-tuning.py --macos $MNT/root --layout 16 -o data/layout16-dsp.json"
    exit 0
  fi
  echo "volume $vol: not the System volume"
done

umount "$MNT" 2>/dev/null || true
echo "no System volume found in $DEV (is FileVault enabled? apfs-fuse will ask for the passphrase)" >&2
exit 1
