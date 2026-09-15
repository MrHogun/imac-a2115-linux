#!/usr/bin/env python3
"""Convert Apple's Broadcom Bluetooth firmware (Intel HEX) to a Linux .hcd patch.

macOS ships the patch for the UART-attached Broadcom controllers as Intel HEX at
/usr/share/firmware/bluetooth/. Linux's btbcm expects a .hcd: a flat sequence of
HCI commands, each [opcode LE16][plen][params], which it replays after putting
the chip in download mode with HCI_Download_Minidriver (0xFC2E) itself -- so the
MiniDriver .hex that macOS uploads separately is not needed here.

The conversion is therefore: decode the HEX into (address, bytes) runs, emit each
run as Write_RAM commands (0xFC4C: 4-byte little-endian address followed by the
data), and close with Launch_RAM (0xFC4E) at 0xFFFFFFFF.

Usage: hex2hcd.py BCM4364B0ID1-Updater.hex BCM.hcd
"""
import struct, sys

WRITE_RAM = 0xFC4C
LAUNCH_RAM = 0xFC4E
# Parameter length is one byte, and 4 of it goes to the address. 240 keeps the
# chunks aligned, which is what Broadcom's own tooling emits.
CHUNK = 240


def parse_ihex(path):
    """Intel HEX -> list of (address, data), merging consecutive records."""
    runs, base = [], 0
    for line in open(path):
        line = line.strip()
        if not line.startswith(":"):
            continue                      # Apple's files can start with a stray '$'
        raw = bytes.fromhex(line[1:])
        length, offset, rectype = raw[0], int.from_bytes(raw[1:3], "big"), raw[3]
        data = raw[4:4 + length]
        if rectype == 0x00:
            addr = base + offset
            if runs and runs[-1][0] + len(runs[-1][1]) == addr:
                runs[-1] = (runs[-1][0], runs[-1][1] + data)
            else:
                runs.append((addr, bytearray(data)))
        elif rectype == 0x04:
            base = int.from_bytes(data, "big") << 16
        elif rectype == 0x02:
            base = int.from_bytes(data, "big") << 4
        elif rectype == 0x01:
            break
    return runs


def to_hcd(runs):
    out = bytearray()
    for addr, data in runs:
        for i in range(0, len(data), CHUNK):
            piece = data[i:i + CHUNK]
            out += struct.pack("<HB I", WRITE_RAM, 4 + len(piece), addr + i) + piece
    out += struct.pack("<HB I", LAUNCH_RAM, 4, 0xFFFFFFFF)
    return bytes(out)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    runs = parse_ihex(sys.argv[1])
    if not runs:
        sys.exit("no data records found -- is that an Intel HEX file?")

    payload = sum(len(d) for _, d in runs)
    blob = to_hcd(runs)
    open(sys.argv[2], "wb").write(blob)

    commands = sum(-(-len(d) // CHUNK) for _, d in runs) + 1
    print(f"runs {len(runs)}, payload {payload} bytes")
    print(f"-> {sys.argv[2]}: {len(blob)} bytes, {commands} HCI commands")
    print(f"   address range 0x{runs[0][0]:08x}-0x{runs[-1][0] + len(runs[-1][1]):08x}")


main()
