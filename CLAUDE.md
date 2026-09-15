# Instructions for Claude Code

You are setting up an **iMac 27" A2115 (2019, `iMac19,1`)** running Linux: Wi-Fi,
audio, the speaker DSP, and making both survive a suspend. This file is the
complete procedure. Work through it in order.

## Before anything

Confirm the hardware. If `cat /sys/class/dmi/id/product_name` is not `iMac19,1`,
stop and tell the user: the speaker tuning here was read out of *this* model's
macOS driver and describes four specific drivers. On any other Mac the filter
curves are wrong. The kernel driver step may still be useful to them; the DSP is not.

Check what is already true before changing anything:

```bash
cat /proc/asound/cards                 # is there an HDA Intel PCH card?
dkms status | grep macbookpro          # driver already built?
pactl list sinks short                 # does imac_speakers already exist?
```

## Step 1 — Wi-Fi

`brcmfmac` ships with the kernel; Apple's firmware does not. Without it the chip
loads nothing (`Direct firmware load ... failed with error -2`).

```bash
# Arch: the packaged extraction lives in arch-mact2
grep -q '^\[arch-mact2\]' /etc/pacman.conf || sudo tee -a /etc/pacman.conf <<'REPO'

[arch-mact2]
Server = https://github.com/NoaHimesaka1873/arch-mact2-mirror/releases/download/release
SigLevel = Never
REPO
sudo pacman -Sy apple-bcm-firmware
```

Then the part people miss: the driver wants
`brcmfmac4364b2-pcie.apple,midway.txt`, the NVRAM, and the package ships it only
under board-variant names. Without it the firmware loads and halts with
`FW failed to initialize`. Pick the variant by the card, never by guessing —
match the PCI subsystem id against `boardtype=` inside the candidates:

```bash
lspci -nn -d 14e4:4464 -v | grep -i subsystem     # e.g. [106b:07bf]
grep -l 'boardtype=0x07bf' /usr/lib/firmware/brcm/brcmfmac4364b2-pcie.apple,midway-HRPN-*.txt
sudo ln -s <that file> /lib/firmware/updates/brcm/'brcmfmac4364b2-pcie.apple,midway.txt'
```

`/lib/firmware/updates` outranks the package directory and belongs to no package,
so the link survives updates. `install.sh` does all of this.

## Step 2 — kernel driver

The in-kernel `snd_hda_codec_cs8409` module contains **only Dell quirks** — verify
this yourself if you doubt it:

```bash
zstdcat "$(modinfo -F filename snd_hda_codec_cs8409)" | strings | grep -ci apple   # 0
```

Apple machines fall through to a generic path that configures pins but never
initialises the TAS5764 amplifiers, so the card appears and plays silence.
[davidjo/snd_hda_macbookpro](https://github.com/davidjo/snd_hda_macbookpro) is
the driver that does initialise them.

```bash
sudo pacman -S --needed git dkms linux-headers base-devel wget    # Arch
# sudo apt install git dkms linux-headers-$(uname -r) build-essential wget patch   # Debian/Ubuntu

git clone --depth=1 https://github.com/davidjo/snd_hda_macbookpro ~/.cache/snd_hda_macbookpro
cd ~/.cache/snd_hda_macbookpro
git apply /path/to/this/repo/patches/cs8409-apple-resume.patch   # suspend fix
sudo ./install.cirrus.driver.sh -i      # -i selects the DKMS install
```

**Patch the right file.** For kernels 6.17 and later the installer copies
`patch_cirrus/cirrus_apple.h`; `patch_cirrus/patch_cirrus_apple.h` is the pre-6.17
copy and editing it has no effect at all. Always confirm a change reached the
built module *before* rebooting:

```bash
zstdcat /lib/modules/$(uname -r)/updates/dkms/snd-hda-codec-cs8409.ko.zst |
    strings | grep -c <a string from your change>
```

**A reboot is required.** The module is bound at boot; reloading it by hand fails
with `Module snd_hda_codec_cs8409 is in use`. Do not try to work around this —
ask the user to reboot, then continue.

After the reboot, the driver is working when the codec renames itself:

```bash
cat /proc/asound/card*/codec#0 | grep Codec      # "Cirrus Logic CS8409/CS42L83"
journalctl -k -b | grep -i cs8409
```

`Primary patch_cs8409 NOT FOUND trying APPLE` in the log is **correct**, not an
error: it means the Apple initialisation path was taken.

## Step 3 — audio configuration

```bash
./install.sh        # idempotent; re-run it after the reboot
```

It copies three files and enables one service:

| File | Purpose |
|---|---|
| `~/.config/pipewire/pipewire.conf.d/90-imac-speakers.conf` | the filter chain (83 nodes) |
| `~/.config/wireplumber/wireplumber.conf.d/51-imac-surround.conf` | keeps the card on the four-channel profile |
| `~/.config/systemd/user/imac-audio.service` | re-applies profile, volume and default sink after login |

## Step 4 — verify

```bash
./verify.sh
```

This is a digital measurement, not a listening test: it plays a tone through the
filter sink and reads what reaches each hardware channel from the sink monitor.
A working setup looks like this — the opposite pair sits at digital silence:

```
tone             FL        FR        RL        RR
            tweeter   tweeter    woofer    woofer
150 Hz       -91.0    -91.0    -37.2    -36.8  dB
9000 Hz      -35.9    -35.2    -91.0    -91.0  dB
```

## Things that will waste your time

- **Do not measure with a microphone** to decide whether the crossover works.
  Use the sink monitor as `verify.sh` does. A mic is only good enough to tell
  which physical pair is the woofers.
- **`pw-record --target` needs the numeric node id**, not the sink name. Given a
  name it silently falls back to the default source, and every channel then
  reads identical — which looks exactly like a dead filter chain.
- **Use `mean_volume` with a bandpass**, not `max_volume`. Peak level over a whole
  capture is dominated by the start transient and reads the same on all four
  channels.
- **Do not run `aplay -D plughw:...` while testing.** It grabs the ALSA device
  exclusively and PipeWire then logs
  `suspended -> error (Start error: Device or resource busy)`; every measurement
  after that is meaningless until PipeWire is restarted.
- **ALSA card numbers move between boots.** The built-in card is not reliably
  `card0`. Resolve it every time:
  `for c in /proc/asound/card[0-9]; do grep -l PCH $c/id; done`.
  Its hardware volume control is `PCM`; a `Speaker` control on another card
  belongs to a USB device.
- **Silence after a suspend is the amplifiers, not power management.**
  `power_save=0` and `power_save_controller=N` were tried on this machine and
  changed nothing; so did switching the sleep state to `s2idle`. The fix is the
  driver patch. If sound stops surviving suspend, a kernel update most likely
  rebuilt the driver from unpatched sources — check with
  `grep -c init_for_node_vendor /usr/src/snd_hda_macbookpro-*/patch_cirrus/cirrus_apple.h`.
- **Never try to recover a dead codec live.** All three routes make it worse, and
  all three were tried: `echo 1 > /sys/class/sound/hwC0D0/reconfig` fails with
  `probe ... failed with error -22`, a PCI remove/rescan fails the same way, and
  reloading the module oopses the kernel. The only recovery is a reboot. Tell the
  user that instead of experimenting.
- **Absence of a log line is not absence of a call.** The driver's `myprintk` is
  compiled out unless `-DMYSOUNDDEBUG` is in `KBUILD_EXTRA_CFLAGS`, and the I2C
  traffic needs `-DMYSOUNDDEBUGFULL`. Reading "the resume handler never ran" from
  an empty grep cost an afternoon here; it ran all along.
- **Check timestamps, not just windows.** The driver's resume work can land a
  second *before* `PM: suspend exit`, so a `journalctl --since` anchored on that
  line hides it.
- **Do not re-derive the EQ.** Every coefficient came out of macOS. If something
  sounds wrong, check routing and gain staging first.

## If the user wants to re-extract the tuning

Only needed to verify the data or to port this to another Mac. Requires their
macOS install and `apfs-fuse`:

```bash
sudo tools/mount-macos.sh                                  # finds the System volume
tools/extract-apple-tuning.py --macos /mnt/macos/root --layout 16 -o data/layout16-dsp.json
tools/generate-filter-chain.py                             # rewrites the .conf
```

The layout id is per-machine and is **only readable from macOS**
(`ioreg -l | grep layout-id`). It is not in ACPI, not in the EFI properties Linux
parses, and not in any kext plist — do not spend time looking for it under Linux.
