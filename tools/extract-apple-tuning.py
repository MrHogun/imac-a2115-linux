#!/usr/bin/env python3
"""Extract Apple's speaker DSP tuning from a mounted macOS system volume.

macOS keeps the tuning for the internal speakers inside the HDA driver:

    /System/Library/Extensions/AppleHDA.kext/Contents/Resources/layout<N>.xml.zlib

Each file is a zlib-compressed property-list *fragment* (it starts at <dict>,
with no XML header) describing one machine's audio path: which amplifiers sit on
which I2C addresses, and the DSP chain applied to the internal speakers.

Which layout belongs to a machine is decided by Apple's firmware and is only
visible from macOS itself:

    ioreg -l | grep -i layout-id

On the iMac 27" A2115 (iMac19,1) that returns 16.

This script decodes one layout into JSON: the filter sections of every
equaliser, the gain stages, and the amplifier map. Run it against a macOS
volume mounted read-only (see mount-macos.sh), or against an already
decompressed layout file.

Usage:
    extract-apple-tuning.py --macos /mnt/macos/root --layout 16 -o data/layout16-dsp.json
    extract-apple-tuning.py --file layout16.xml -o data/layout16-dsp.json
"""
import argparse, json, os, plistlib, struct, sys, zlib

PLIST_HEAD = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
              b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
              b'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0">\n')
PLIST_TAIL = b'\n</plist>\n'

RESOURCES = "System/Library/Extensions/AppleHDA.kext/Contents/Resources"

# Filter types, as identified from how Apple uses them in this data:
#   0 appears only as cascades of identical sections near 1.39 kHz on the woofer
#     branch, and 1 as cascades near 60 Hz (input) and 3.0 kHz (tweeter branch).
#     Those positions are band limits, so 0 is a lowpass and 1 a highpass.
#   4 carries a meaningful gain at scattered frequencies: a peaking section.
FILTER_TYPES = {0: "lowpass", 1: "highpass", 4: "peaking"}


def f32(value):
    """Apple stores floats in these plists as their int32 bit pattern."""
    return struct.unpack("<f", struct.pack("<i", value))[0]


def read_layout(path):
    raw = open(path, "rb").read()
    if path.endswith(".zlib"):
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw[8:])
    return plistlib.loads(PLIST_HEAD + raw + PLIST_TAIL)


def decode_sections(filt):
    out = []
    for s in filt:
        kind = s.get("5")
        out.append({
            "type": kind,
            "kind": FILTER_TYPES.get(kind, f"unknown({kind})"),
            "freq_hz": round(f32(s.get("6", 0)), 4),
            "q": round(f32(s.get("7", 0)), 5),
            "gain_db": round(f32(s.get("8", 0)), 4),
        })
    return out


def decode(layout):
    path_map = layout["PathMapRef"][0]
    speaker = path_map["IntSpeaker"]["SignalProcessing"][0]["SoftwareDSP"]

    chain = []
    for key in sorted(speaker, key=lambda k: int(k.replace("DspFunction", ""))):
        func = speaker[key]
        info = func["FunctionInfo"]
        params = func.get("ParameterInfo", {})
        entry = {
            "function": key,
            "name": info["DspFuncName"],
            "instance": info.get("DspFuncInstance"),
            "inputs": {
                port: {"from_instance": spec.get("SourceFuncInstance"),
                       "from_port": spec.get("SourcePortIndex")}
                for port, spec in sorted(func.get("PatchbayInfo", {}).items())
            },
        }
        if "Filter" in params:
            entry["sections"] = decode_sections(params["Filter"])
        else:
            entry["params"] = {k: (f32(v) if isinstance(v, int) else v)
                               for k, v in params.items()
                               if not isinstance(v, (list, dict))}
        chain.append(entry)

    return {
        "layout_id": layout.get("LayoutID"),
        "path_map_id": path_map.get("PathMapID"),
        "amplifiers": [{"device": hex(a.get("Device", 0)),
                        "i2c_address": hex(a.get("I2Caddress", 0))}
                       for a in path_map.get("TDMDevices", [])],
        "chain": chain,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--macos", metavar="PATH",
                     help="mounted macOS *System* volume (the one holding System/Library)")
    src.add_argument("--file", metavar="PATH",
                     help="a layout file, compressed (.zlib) or already decoded")
    ap.add_argument("--layout", type=int, default=16,
                    help="layout id to extract (default: 16, the iMac A2115)")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()

    if args.macos:
        path = os.path.join(args.macos, RESOURCES, f"layout{args.layout}.xml.zlib")
        if not os.path.exists(path):
            sys.exit(f"not found: {path}\n"
                     "Is that the System volume? On Catalina and later the volume "
                     "holding your files is the Data volume and has no "
                     "System/Library/Extensions -- see tools/mount-macos.sh.")
    else:
        path = args.file

    data = decode(read_layout(path))
    data["source"] = os.path.basename(path)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as fh:
        json.dump(data, fh, indent=1)

    eqs = [c for c in data["chain"] if "sections" in c]
    print(f"layout {data['layout_id']}: {len(data['chain'])} stages, "
          f"{len(eqs)} equalisers, {sum(len(c['sections']) for c in eqs)} filter sections")
    print(f"amplifiers: {', '.join(a['i2c_address'] for a in data['amplifiers'])}")
    print(f"written: {args.output}")


if __name__ == "__main__":
    main()
