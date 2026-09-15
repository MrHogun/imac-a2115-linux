#!/usr/bin/env bash
# iMac 27" A2115 (2019, iMac19,1) -- everything Linux gets wrong on this machine.
#
#   Wi-Fi     no firmware for the BCM4364, so no wireless at all
#   Audio     no driver for the CS8409, so no sound at all
#   Speakers  four drivers fed one full-range signal, no crossover
#   Suspend   audio and Wi-Fi both come back dead
#
# Run as your normal user; it asks for sudo where it needs it. Safe to re-run --
# every step checks whether it is already done.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRIVER_SRC="${XDG_CACHE_HOME:-$HOME/.cache}/snd_hda_macbookpro"
CARD=alsa_card.pci-0000_00_1f.3
SINK=alsa_output.pci-0000_00_1f.3.analog-surround-40
PROFILE=output:analog-surround-40+input:analog-stereo

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
step() { printf '\033[1;32m  •\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERROR\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] && die "run as your normal user, not root"

model=$(cat /sys/class/dmi/id/product_name 2>/dev/null || echo unknown)
if [[ $model != iMac19,1 ]]; then
  warn "this machine reports '$model', not iMac19,1."
  warn "The Wi-Fi and driver steps may still apply; the speaker tuning will not."
  read -rp "Continue anyway? [y/N] " r; [[ ${r,,} == y ]] || exit 1
fi

if command -v pacman >/dev/null; then PM=pacman
elif command -v apt-get >/dev/null; then PM=apt
elif command -v dnf >/dev/null; then PM=dnf
else die "unsupported distribution -- see the README for what to install by hand"; fi

pkg_install() {
  case $PM in
    pacman) sudo pacman -S --needed --noconfirm "$@" ;;
    apt)    sudo apt-get install -y "$@" ;;
    dnf)    sudo dnf install -y "$@" ;;
  esac
}

# ---------------------------------------------------------------- Wi-Fi ----
# The BCM4364 needs Apple's firmware, which no distribution may ship. The
# packaged extraction of it lives in the arch-mact2 repository; Omarchy already
# adds that repository, but only for Macs carrying a T2 chip, which this is not.
install_wifi() {
  info "Wi-Fi firmware (BCM4364)"

  if [[ ! -e /usr/lib/firmware/brcm/brcmfmac4364b2-pcie.apple,midway.bin ]]; then
    if [[ $PM == pacman ]]; then
      if ! grep -q '^\[arch-mact2\]' /etc/pacman.conf; then
        step "adding the arch-mact2 repository"
        sudo tee -a /etc/pacman.conf >/dev/null <<'REPO'

[arch-mact2]
Server = https://github.com/NoaHimesaka1873/arch-mact2-mirror/releases/download/release
SigLevel = Never
REPO
      fi
      sudo pacman -Sy --noconfirm
      pkg_install apple-bcm-firmware
    else
      warn "no packaged firmware for this distribution."
      warn "Fetch apple-bcm-firmware from https://github.com/NoaHimesaka1873/apple-bcm-firmware"
      warn "and unpack its brcm/ directory into /usr/lib/firmware/brcm/, then re-run."
      return
    fi
  else
    step "apple-bcm-firmware already present"
  fi

  # The driver asks for brcmfmac4364b2-pcie.apple,midway.txt -- the NVRAM with
  # this machine's antenna calibration. The package ships it only under the
  # board-variant names, so link the one whose boardtype matches this card's
  # PCI subsystem id. /lib/firmware/updates takes precedence over the package's
  # own directory and belongs to no package, so it survives updates.
  local target=/lib/firmware/updates/brcm/'brcmfmac4364b2-pcie.apple,midway.txt'
  if [[ -e $target ]]; then
    step "NVRAM link already in place"
    return
  fi

  local subsys variant found=
  subsys=$(lspci -nn -d 14e4:4464 -v 2>/dev/null | grep -oiE 'Subsystem:.*\[106b:([0-9a-f]{4})\]' | grep -oiE '[0-9a-f]{4}\]$' | tr -d ']')
  [[ -z $subsys ]] && { warn "no BCM4364 found; skipping the NVRAM link"; return; }

  for variant in /usr/lib/firmware/brcm/brcmfmac4364b2-pcie.apple,midway-HRPN-*.txt; do
    [[ -e $variant ]] || continue
    if grep -qi "boardtype=0x${subsys}" "$variant"; then found=$variant; break; fi
  done
  [[ -z $found ]] && { warn "no NVRAM variant matches boardtype 0x$subsys -- see the README"; return; }

  step "linking NVRAM $(basename "$found") (boardtype 0x$subsys)"
  sudo install -dm755 /lib/firmware/updates/brcm
  sudo ln -sf "$found" "$target"
}

# ---------------------------------------------------------------- audio ----
# The in-kernel cs8409 module carries Dell quirks only; on Apple machines the
# codec falls back to a generic path that never programs the amplifiers.
install_driver() {
  info "Audio driver (snd_hda_macbookpro, patched)"

  if dkms status 2>/dev/null | grep -q snd_hda_macbookpro; then
    step "DKMS module already installed"
    return 1
  fi

  case $PM in
    pacman) pkg_install git dkms base-devel wget linux-headers ;;
    apt)    sudo apt-get update -qq; pkg_install git dkms build-essential wget patch "linux-headers-$(uname -r)" ;;
    dnf)    pkg_install git dkms make gcc wget patch "kernel-devel-$(uname -r)" ;;
  esac

  if [[ -d $DRIVER_SRC/.git ]]; then
    step "refreshing the driver source"
    git -C "$DRIVER_SRC" checkout -- . && git -C "$DRIVER_SRC" pull --ff-only
  else
    step "cloning davidjo/snd_hda_macbookpro"
    git clone --depth=1 https://github.com/davidjo/snd_hda_macbookpro "$DRIVER_SRC"
  fi

  step "applying the suspend fix"
  git -C "$DRIVER_SRC" apply --check "$REPO/patches/cs8409-apple-resume.patch" 2>/dev/null \
    && git -C "$DRIVER_SRC" apply "$REPO/patches/cs8409-apple-resume.patch" \
    || warn "the patch did not apply cleanly -- upstream may have changed it; audio will work but will not survive suspend"

  step "building (DKMS)"
  ( cd "$DRIVER_SRC" && sudo ./install.cirrus.driver.sh -i )
  return 0
}

install_audio_config() {
  info "Speaker DSP -- Apple's own tuning"
  install -Dm644 "$REPO/config/90-imac-speakers.conf" \
                 "$HOME/.config/pipewire/pipewire.conf.d/90-imac-speakers.conf"
  install -Dm644 "$REPO/config/51-imac-surround.conf" \
                 "$HOME/.config/wireplumber/wireplumber.conf.d/51-imac-surround.conf"
  install -Dm644 "$REPO/config/imac-audio.service" \
                 "$HOME/.config/systemd/user/imac-audio.service"
  systemctl --user daemon-reload
  systemctl --user enable imac-audio.service >/dev/null
  step "filter chain, profile rule and user service installed"
}

# -------------------------------------------------------------- suspend ----
install_sleep_hook() {
  info "Suspend fixes"
  if cmp -s "$REPO/config/50-imac-wifi" /usr/lib/systemd/system-sleep/50-imac-wifi; then
    step "Wi-Fi resume hook already installed"
  else
    sudo install -Dm755 "$REPO/config/50-imac-wifi" /usr/lib/systemd/system-sleep/50-imac-wifi
    step "Wi-Fi resume hook installed"
  fi
  step "audio across suspend is handled by the driver patch, not a hook"
}

apply_now() {
  info "Applying"
  pactl set-card-profile "$CARD" "$PROFILE" 2>/dev/null || {
    warn "could not select the four-channel profile -- reboot first, then re-run"; return; }
  systemctl --user restart pipewire pipewire-pulse wireplumber
  sleep 3
  pactl set-sink-volume "$SINK" 100% 2>/dev/null || true
  pactl set-default-sink imac_speakers 2>/dev/null && step "default output is now 'iMac Speakers'" \
    || warn "filter sink missing; check: journalctl --user -u pipewire -b | grep -i filter"
}

install_wifi
if install_driver; then
  install_audio_config
  install_sleep_hook
  echo
  info "The driver binds at boot, so a reboot is needed before there is sound."
  read -rp "Reboot now? [y/N] " r; [[ ${r,,} == y ]] && systemctl reboot
  exit 0
fi
install_audio_config
install_sleep_hook
apply_now
echo
info "Done. Check it with:  ./verify.sh"
