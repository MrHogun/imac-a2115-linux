# iMac 27" A2115 (2019) on Linux

[![Stand With Ukraine](https://raw.githubusercontent.com/vshymanskyy/StandWithUkraine/main/badges/StandWithUkraine.svg)](#support-ukraine)

Everything this machine gets wrong under Linux, and the fixes, in one place.

| | Out of the box | After |
|---|---|---|
| **Wi-Fi** | no wireless at all — no firmware for the BCM4364 | works |
| **Audio** | no sound at all — the in-kernel driver never programs the amplifiers | works |
| **Speakers** | four drivers fed one full-range signal, no crossover | Apple's own tuning |
| **Audio after suspend** | silent until a reboot | works |
| **Wi-Fi after suspend** | radio deaf until a reload | works |

The speaker tuning is not a fit by ear: the crossover frequencies, filter curves
and gain structure were read out of macOS, from the DSP chain Apple's own driver
runs on this hardware.

The suspend fix for audio is a patch to the out-of-tree driver. Its author
documents sleep as unimplemented — *"Power down/sleep completely unknown and
untested"* — and this closes that gap for the iMac amplifier set.

---

## Install

```bash
git clone https://github.com/MrHogun/imac-a2115-linux
cd imac-a2115-linux
./install.sh          # Wi-Fi firmware, audio driver, DSP, suspend fixes
./install.sh          # run again after the reboot it asks for
./verify.sh
```

Using Claude Code? Hand it [`CLAUDE.md`](CLAUDE.md) — the whole procedure, including
the measurement traps that make a working setup look broken.

---

## Tested on

One machine, and every number in this README was measured on it.

| | |
|---|---|
| Hardware | iMac19,1 — iMac 27" 5K, 2019 (A2115), board `Mac-AA95B1DDAB278B95` |
| CPU | Intel Core i5-9600K · Firmware 2094.80.5.0.0 |
| Wi-Fi | Broadcom BCM4364 rev B2 (`14e4:4464`, subsystem `106b:07bf`) |
| Audio | Cirrus CS8409 + CS42L83, subsystem `0x106b1000`, 4× TAS5764 amps |
| Distro | Omarchy 4.0.3 (Arch) · kernel 7.2.3-arch1-3 · PipeWire 1.6.8 |
| Driver | `davidjo/snd_hda_macbookpro` `89b22ff`, patched, via DKMS |
| Tuning from | macOS 15.7.9, `AppleHDA.kext` layout 16 |

`install.sh` also knows apt and dnf, but only the setup above has been run end to end.

---

## Wi-Fi

`brcmfmac` is in every kernel; the firmware is not, and cannot be — it is Apple's.
Without it:

```
brcmfmac: using brcm/brcmfmac4364b2-pcie for chip BCM4364/3
brcmfmac: Direct firmware load for brcmfmac4364b2-pcie.bin failed with error -2
brcmfmac: brcmf_pcie_setup: Dongle setup failed
```

The packaged extraction is `apple-bcm-firmware`, from the `arch-mact2` repository.
Installing it is not quite enough: it ships the NVRAM — the antenna calibration —
only under board-variant names, while the driver asks for
`brcmfmac4364b2-pcie.apple,midway.txt`. Without it the firmware loads and then
halts: `FW failed to initialize`.

Which variant is right is decided by the card, not by guesswork. This machine's
PCI subsystem id is `106b:07bf`, and only the `-HRPN-m` file carries
`boardtype=0x07bf` (`-HRPN-u` has `0x081d`). `install.sh` reads the id and links
the matching file into `/lib/firmware/updates/brcm/`, which takes precedence over
the package's own directory and belongs to no package, so updates leave it alone.

Note for Omarchy users: Omarchy already knows about `arch-mact2`, but only adds it
for Macs carrying a T2 chip. The iMac19,1 has none, so it never qualifies —
[upstream PR #8632](https://github.com/omacom/omarchy/pull/8632) widens that test.

---

## Audio

### Why there is no sound

The mainline `snd_hda_codec_cs8409` module supports **Dell laptops only**:

```bash
zstdcat "$(modinfo -F filename snd_hda_codec_cs8409)" | strings | grep -ci apple   # 0
```

On an Apple machine the codec falls through to a generic path. Pins get set, a
sink appears, PipeWire is content — and the four TAS5764 amplifiers are never
programmed. [davidjo/snd_hda_macbookpro](https://github.com/davidjo/snd_hda_macbookpro)
carries the Apple initialisation sequences, reverse-engineered from macOS traces,
and is the only reason this machine makes sound.

### The speakers

Four drivers, one amplifier each, on the codec's I2C bus — straight from Apple's data:

| Amp | I2C | Driver | ALSA channel |
|---|---|---|---|
| TAS5764 | `0x6c` | left tweeter | FL |
| TAS5764 | `0x6d` | right tweeter | FR |
| TAS5764 | `0x6e` | left woofer | RL |
| TAS5764 | `0x6f` | right woofer | RR |

Confirmed by measurement: at 200 Hz the rear pair is 6–11 dB louder.

### Apple's tuning

macOS keeps the DSP for the internal speakers inside the HDA driver, at
`AppleHDA.kext/Contents/Resources/layout<N>.xml.zlib`. Which layout applies is
chosen by Apple's firmware and readable only from macOS
(`ioreg -l | grep layout-id`). On this iMac it is **16**:

```
stereo in
  → input EQ, 12 sections per channel
  → compressor · loudness · dual-band compressor      (not ported, see below)
  → pre-split gain −5.0 dB
  → split to four
       ├─ tweeters  −2.5 dB → 10 sections, 2 cascaded highpasses @ 3.0 kHz
       └─ woofers    0.0 dB → 15/16 sections, 3 cascaded lowpasses @ 1.39 kHz
  → spline limiters · BuzzKill · ControlFreak4ch      (not ported)
  → four-channel output
```

Apple's crossover is **asymmetric** — woofers rolled off at 1.39 kHz with a
6th-order slope, tweeters at 3.0 kHz with a 4th-order one. Nobody lands on that by
ear, and it is the single biggest reason a hand-tuned crossover sounds wrong here.

Reproduced: the input EQ, the gain structure, the crossover and all 75 filter
sections — 83 PipeWire nodes. Not reproduced: the level-dependent stages, which
have no PipeWire equivalent. The tonal balance matches; macOS still holds loud
material together better.

---

## Suspend

Two separate failures, two different fixes.

### Audio — a driver patch

After a suspend the codec resumes, the stream prepares, and the speakers stay
silent. The log shows no I2C traffic at all: `cs_8409_apple_resume()` restored the
codec's own registers and nothing else. The amplifiers sit behind the codec's I2C
master, where regmap cannot see them, so they come back empty.

Every mainline driver for this class of part re-drives its external devices on
resume — cs8409's own Dell path rewrites the CS42L42 init sequence, CS35L41 resets
and reloads its DSP, TAS2781 voids its cached program state and reloads.
[`patches/cs8409-apple-resume.patch`](patches/cs8409-apple-resume.patch) does the
same for the iMac amplifiers:

```c
spec->play_init = 0;                 /* the cached setup state is void */
init_for_node_vendor(codec);         /* vendor registers, ASP clocking */
enable_i2c(codec);                   /* bring the I2C master back up */
setup_amps_reset_i2c_tas576(codec);  /* reset over GPIO, then reprogram */
```

Order matters, and each piece was established by elimination:

- **Replaying all of `cs_8409_boot_setup()` does not work.** Its CS42L83 stage polls
  an interrupt status that never settles on a codec that is already up; three
  `read_status_and_clear_interrupt - max count exceeded` timeouts later the
  hardware is worse off than before. The headphone codec is not what goes quiet.
- **Programming the amps without `enable_i2c()` does nothing.** The I2C master lives
  behind the vendor widget's PROC_STATE and coefficients, which regmap does not
  restore. Writes go into a disabled bus: no error, no effect.
- **Without `init_for_node_vendor()` the amps are configured but silent** — they hold
  their settings and no data reaches them, because the ASP clocking is unset.

With all three, a resume writes 544 I2C transactions — 136 to each amplifier — and
the speakers come back.

### Wi-Fi — a sleep hook

The BCM4364 survives a suspend as a device but not as a radio: the interface is
there, no firmware crash is logged, and a scan returns nothing. Unlike the HDA
codec, `brcmfmac` unloads cleanly, so
[`config/50-imac-wifi`](config/50-imac-wifi) reloads it on resume and
NetworkManager reconnects by itself.

---

## Verifying

`./verify.sh` plays a tone through the filter sink and reads what arrives at each
hardware channel from the sink's monitor — a digital measurement, no microphone:

```
tone             FL        FR        RL        RR
            tweeter   tweeter    woofer    woofer
150 Hz       -91.0    -91.0    -37.2    -36.8  dB
800 Hz       -91.0    -91.0    -43.4    -42.7  dB
9000 Hz      -35.9    -35.2    -91.0    -91.0  dB
```

−91 dB is digital silence. It also reports the Wi-Fi interface and whether both
suspend fixes are in place.

---

## Re-extracting Apple's tuning

Nothing here has to be taken on trust:

```bash
sudo tools/mount-macos.sh        # mounts the macOS System volume read-only
tools/extract-apple-tuning.py --macos /mnt/macos/root --layout 16 \
    -o data/layout16-dsp.json
tools/generate-filter-chain.py   # regenerates the PipeWire config
```

`apfs-fuse` cannot write APFS, so the macOS install is never at risk. On Catalina
and later the volume holding your files is the *Data* volume; the kexts are on the
System volume, which the script finds for you.

---

## Files

```
├── install.sh                  Wi-Fi + driver + DSP + suspend, idempotent
├── uninstall.sh                takes it all back out
├── verify.sh                   digital crossover measurement and health check
├── CLAUDE.md                   the whole procedure, for Claude Code
├── patches/
│   └── cs8409-apple-resume.patch    the suspend fix for the audio driver
├── config/
│   ├── 90-imac-speakers.conf        PipeWire filter chain (generated)
│   ├── 51-imac-surround.conf        hold the four-channel profile
│   ├── imac-audio.service           re-apply profile and sink after login
│   └── 50-imac-wifi                 reload brcmfmac on resume
├── data/layout16-dsp.json      Apple's decoded DSP chain
└── tools/                      mount macOS · extract tuning · generate config
```

---

## Troubleshooting

**No sound after installing the driver.** It binds at boot — reboot. Then
`cat /proc/asound/card*/codec#0 | grep Codec` must say `CS8409/CS42L83`. Seeing
`Primary patch_cs8409 NOT FOUND trying APPLE` in `journalctl -k` is correct: it
means the Apple path was taken.

**`imac_speakers` never appears.** The filter chain targets the four-channel sink,
so that profile has to be active first:
`pactl set-card-profile alsa_card.pci-0000_00_1f.3 output:analog-surround-40+input:analog-stereo`,
then restart PipeWire.

**Only two speakers play.** The card fell back to stereo, which has a much higher
priority — that is what the WirePlumber rule and the user service are for.

**Wi-Fi works but drops on resume.** Check the hook:
`ls -l /usr/lib/systemd/system-sleep/50-imac-wifi`, and
`journalctl -t imac-wifi-sleep -b` for what it did.

**Sound is gone after a suspend again.** A kernel update rebuilt the driver from
unpatched sources. `install.sh` re-applies the patch; check with
`grep -c init_for_node_vendor /usr/src/snd_hda_macbookpro-*/patch_cirrus/cirrus_apple.h`.

**It broke after a kernel update.** DKMS rebuilds automatically, but silently does
not if the headers were missing at update time — `dkms status` should list
`snd_hda_macbookpro` for the running kernel.

---

## Credits

- [davidjo/snd_hda_macbookpro](https://github.com/davidjo/snd_hda_macbookpro) — the
  driver and the reverse engineering behind it. Without it this machine is silent;
  the suspend patch here is a small addition to a great deal of work.
- [NoaHimesaka1873/apple-bcm-firmware](https://github.com/NoaHimesaka1873/apple-bcm-firmware)
  and the arch-mact2 repository — the packaged Wi-Fi firmware.
- Apple — the speaker tuning, read from macOS on the machine that shipped with it.
  No Apple file is redistributed here; `data/layout16-dsp.json` holds decoded filter
  parameters, and the tools let you reproduce it from your own install.

## Disclaimer

Built by someone without a background in DSP or kernel development, with help from
Claude Code (Opus 5), by extracting Apple's own data rather than inventing values.
Tested on exactly one machine. The driver patch is an out-of-tree change to an
out-of-tree module: it works here, it is not upstream, and a kernel or driver
update can require re-applying it. Pull requests very welcome.

## Support Ukraine

Written in Ukraine. If this saved you an evening, consider
[supporting Ukraine](https://u24.gov.ua/).

## License

MIT — see [LICENSE](LICENSE), covering the code in this repository.
