#!/usr/bin/env bash
# Remove the speaker setup. Leaves the kernel driver alone unless asked --
# without it this machine has no sound at all.
set -euo pipefail
info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

systemctl --user disable --now imac-audio.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/imac-audio.service" \
      "$HOME/.config/pipewire/pipewire.conf.d/90-imac-speakers.conf" \
      "$HOME/.config/wireplumber/wireplumber.conf.d/51-imac-surround.conf"
systemctl --user daemon-reload

pactl set-card-profile alsa_card.pci-0000_00_1f.3 output:analog-stereo+input:analog-stereo 2>/dev/null || true
systemctl --user restart pipewire pipewire-pulse wireplumber
sleep 2
pactl set-default-sink alsa_output.pci-0000_00_1f.3.analog-stereo 2>/dev/null || true
info "DSP removed; the card is back on plain stereo."

sudo rm -f /usr/lib/systemd/system-sleep/50-imac-wifi
info "Wi-Fi resume hook removed."

read -rp "Also remove the kernel driver? You will lose sound entirely. [y/N] " reply
if [[ ${reply,,} == y ]]; then
  sudo dkms remove snd_hda_macbookpro/0.1 --all
  info "Driver removed. Reboot to fall back to the in-kernel driver."
fi
