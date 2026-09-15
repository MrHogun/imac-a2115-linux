#!/usr/bin/env bash
# Prove the crossover is actually working, digitally -- no microphone involved.
#
# Plays a tone through the filter sink and records what reaches each of the four
# hardware channels from the sink's monitor. With the crossover in place a low
# tone must appear on the woofers and a high tone on the tweeters, with tens of
# dB between them. Flat, equal numbers mean the filter chain is being bypassed.
set -euo pipefail

SINK_NAME=alsa_output.pci-0000_00_1f.3.analog-surround-40
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

for cmd in ffmpeg pw-play pw-record pactl; do
  command -v "$cmd" >/dev/null || { echo "missing: $cmd (ffmpeg and pipewire-tools are required)"; exit 1; }
done

id=$(pactl list sinks short | awk -v s="$SINK_NAME" '$2==s{print $1}')
[[ -n $id ]] || { echo "four-channel sink not found -- is the surround-40 profile active?"; exit 1; }

default=$(pactl info | awk -F': ' '/Default Sink/{print $2}')
[[ $default == imac_speakers ]] || echo "note: default sink is '$default', not 'imac_speakers'"

printf '%-9s %9s %9s %9s %9s\n' tone FL FR RL RR
printf '%-9s %9s %9s %9s %9s\n' '' tweeter tweeter woofer woofer

for f in 150 800 9000; do
  ffmpeg -loglevel error -f lavfi -i "sine=frequency=$f:duration=2:sample_rate=48000" \
         -ac 2 -y "$TMP/t.wav"
  pw-record --target "$id" -P '{ stream.capture.sink=true stream.dont-remix=true }' \
            --channels 4 --rate 48000 "$TMP/c.wav" >/dev/null 2>&1 &
  rec=$!
  sleep 0.5; pw-play "$TMP/t.wav" >/dev/null 2>&1; sleep 0.3
  kill $rec 2>/dev/null || true; wait $rec 2>/dev/null || true

  row=$(printf '%-9s' "${f} Hz")
  for ch in 0 1 2 3; do
    # Band-limit to the tone and skip the first half second: peak level over
    # the whole capture is dominated by the start transient and reads the same
    # on every channel, which hides the crossover entirely.
    v=$(ffmpeg -hide_banner -nostats -ss 0.5 -i "$TMP/c.wav" \
        -af "pan=mono|c0=c${ch},bandpass=f=${f}:width_type=o:w=0.5,volumedetect" \
        -f null - 2>&1 | awk -F': ' '/mean_volume/{print $2}' | cut -d' ' -f1)
    row+=$(printf '%9s' "${v:-?}")
  done
  echo "$row  dB"
done

echo
echo "--- Wi-Fi ---"
if ip -br link | grep -q '^wlp'; then
  echo "interface:  $(ip -br link | awk '/^wlp/{print $1, $2}')"
  echo "networks:   $(nmcli -f SSID device wifi list 2>/dev/null | tail -n +2 | grep -c . ) visible"
else
  echo "no wireless interface -- firmware missing? see README"
fi

echo
echo "--- suspend readiness ---"
grep -q 'setup_amps_reset_i2c_tas576' /usr/src/snd_hda_macbookpro-*/patch_cirrus/cirrus_apple.h 2>/dev/null \
  && echo "audio:      driver carries the resume fix" \
  || echo "audio:      driver is unpatched -- sound will not survive a suspend"
[ -x /usr/lib/systemd/system-sleep/50-imac-wifi ] \
  && echo "wi-fi:      resume hook installed" \
  || echo "wi-fi:      resume hook missing -- wireless will not survive a suspend"

cat <<'NOTE'

Expected on a working setup: the woofers lead by tens of dB at 150 Hz and at
800 Hz, the tweeters lead by tens of dB at 9 kHz. Numbers within a few dB of
each other across all four channels mean the audio is not passing through the
filter chain -- see Troubleshooting in the README.
NOTE
