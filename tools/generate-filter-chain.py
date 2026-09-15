#!/usr/bin/env python3
"""Build the PipeWire filter-chain from Apple's extracted tuning.

Reads data/layout16-dsp.json (see extract-apple-tuning.py) and writes
config/pipewire/90-imac-speakers.conf.

Apple's chain for this machine, read off its own patchbay:

    stereo in
      -> DspEqualization32        input EQ, per channel
      -> DspMozartCompressor      \
      -> DspLoudness               |  level-dependent stages, not ported
      -> DspMozartCompressorDualBand
      -> DspGainStage             pre-split gain
      -> Dsp2To4Splitter          fan out to four
           ports 0,1 -> DspGainStage -> DspEqualization32 -> DspBuzzKill
           ports 2,3 -> DspGainStage -> DspEqualization32 -> DspDelay
      -> DspControlFreak4ch       ports 0,1 from the delayed branch,
                                  ports 2,3 from the BuzzKill branch
      -> Dsp4ChOutput

The branch that reaches output ports 0,1 is band-limited by highpasses near
3 kHz; the one reaching ports 2,3 by lowpasses near 1.39 kHz. Output ports 0,1
are therefore the tweeters and 2,3 the woofers, which matches both the cs8409
driver's channel order (FL, FR tweeters; RL, RR woofers) and a measurement of
this machine: the rear pair is 6-11 dB louder at 200 Hz.

Not ported: the compressors, Loudness, BuzzKill, ControlFreak4ch and the
8-sample delay. Those are level-dependent behaviour rather than a fixed
response, and PipeWire's builtin filters have no equivalent. What this
reproduces is the static voicing -- the crossover, the gain structure and every
equaliser section.
"""
import argparse, json, os, sys

LABELS = {"lowpass": "bq_lowpass", "highpass": "bq_highpass", "peaking": "bq_peaking"}
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def stage(chain, name, instance=None):
    for c in chain:
        if c["name"] == name and (instance is None or c["instance"] == instance):
            return c
    sys.exit(f"stage not found in the extracted data: {name}"
             + (f" instance {instance}" if instance is not None else ""))


def split_channels(sections):
    """Apple concatenates the left channel's sections, then the right channel's.

    Both halves open with the same section, so the second occurrence of the
    opening (type, frequency) marks the boundary. Falling back to a halfway cut
    would silently mis-assign filters, so an unsplittable list is an error.
    """
    head = (sections[0]["type"], round(sections[0]["freq_hz"], 2))
    for i in range(1, len(sections)):
        if (sections[i]["type"], round(sections[i]["freq_hz"], 2)) == head:
            return sections[:i], sections[i:]
    sys.exit("could not tell the two channels apart in an equaliser: "
             "the section list does not restart. Extracted data may be from a "
             "different layout than this generator understands.")


class Graph:
    def __init__(self):
        self.nodes, self.links = [], []

    def node(self, name, label, **control):
        ctrl = " ".join(f'"{k}" = {v}' for k, v in control.items())
        body = f"{{ type = builtin name = {name:20s} label = {label:12s}"
        body += f" control = {{ {ctrl} }} }}" if ctrl else " }"
        self.nodes.append("          " + body)
        return name

    def link(self, src, dst):
        self.links.append(f'          {{ output = "{src}:Out" input = "{dst}:In" }}')

    def eq(self, prefix, sections, source):
        """Chain one channel's equaliser sections; returns the last node."""
        previous = source
        for i, s in enumerate(sections):
            label = LABELS.get(s["kind"])
            if label is None:
                sys.exit(f"unsupported filter kind {s['kind']!r} in {prefix}")
            ctrl = {"Freq": f"{s['freq_hz']:.2f}", "Q": f"{s['q']:.4f}"}
            if label == "bq_peaking":
                ctrl["Gain"] = f"{s['gain_db']:.2f}"
            name = self.node(f"{prefix}{i}", label, **ctrl)
            if previous:
                self.link(previous, name)
            previous = name
        return previous


def build(data):
    chain = data["chain"]

    eq_in = stage(chain, "DspEqualization32", 0)["sections"]
    pre_gain = stage(chain, "DspGainStage", 4)["params"]
    eq_a = stage(chain, "DspEqualization32", 8)          # via gain stage 6
    eq_b = stage(chain, "DspEqualization32", 9)          # via gain stage 7
    gain_a = stage(chain, "DspGainStage", 6)["params"]
    gain_b = stage(chain, "DspGainStage", 7)["params"]

    # Identify the branches by what band-limits them rather than by position.
    def band(eq):
        kinds = {s["kind"] for s in eq["sections"]}
        if "lowpass" in kinds:
            return "woofer"
        if "highpass" in kinds:
            return "tweeter"
        sys.exit(f"equaliser instance {eq['instance']} has no band limit; "
                 "cannot tell which drivers it feeds")

    branches = {band(eq_a): (eq_a, gain_a), band(eq_b): (eq_b, gain_b)}
    if set(branches) != {"woofer", "tweeter"}:
        sys.exit("expected one woofer and one tweeter branch, got: " + ", ".join(branches))

    g = Graph()
    ends, starts = {}, {}
    for side, key in (("l", "2"), ("r", "3")):
        chan = 0 if side == "l" else 1

        sections = split_channels(eq_in)[chan]
        first = g.eq(f"in{side}_", sections, None)
        starts[side] = f"in{side}_0"
        # `first` is the last node of the input EQ chain
        pre = g.node(f"pre{side}", "linear", Mult=f"{pre_gain[key]:.6f}", Add="0.0")
        g.link(first, pre)
        split = g.node(f"split{side}", "copy")
        g.link(pre, split)

        for branch in ("tweeter", "woofer"):
            eq, gains = branches[branch]
            gate = g.node(f"g{branch[:2]}{side}", "linear",
                          Mult=f"{gains[key]:.6f}", Add="0.0")
            g.link(split, gate)
            ends[f"{branch}_{side}"] = g.eq(f"{branch[:2]}{side}_",
                                            split_channels(eq["sections"])[chan], gate)

    return g, starts, ends, branches, pre_gain


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", default=os.path.join(REPO, "data", "layout16-dsp.json"))
    ap.add_argument("-o", "--output",
                    default=os.path.join(REPO, "config", "90-imac-speakers.conf"))
    args = ap.parse_args()

    data = json.load(open(args.input))
    g, starts, ends, branches, pre_gain = build(data)

    counts = {b: [len(split_channels(eq["sections"])[i]) for i in (0, 1)]
              for b, (eq, _) in branches.items()}
    limits = {}
    for b, (eq, _) in branches.items():
        edge = [s for s in eq["sections"] if s["kind"] in ("lowpass", "highpass")]
        limits[b] = (edge[0]["kind"], edge[0]["freq_hz"], len(edge) // 2)

    header = f"""# iMac 27" A2115 (iMac19,1) internal speakers -- Apple's own tuning in PipeWire.
#
# Generated by tools/generate-filter-chain.py from data/layout16-dsp.json.
# Do not edit this file; edit the generator or re-extract the data.
#
# Every coefficient comes from macOS, AppleHDA.kext layout {data['layout_id']}, which is the
# layout Apple's firmware selects for this machine (`ioreg -l | grep layout-id`).
# Nothing here was fitted by ear or guessed.
#
#   stereo in
#     -> input EQ, {len(split_channels(data['chain'][0]['sections'])[0])} sections per channel
#     -> pre-split gain {pre_gain['2']:.4f} / {pre_gain['3']:.4f}
#     -> split to four
#          tweeters -> FL, FR: {counts['tweeter'][0]}/{counts['tweeter'][1]} sections,
#                      {limits['tweeter'][2]} cascaded {limits['tweeter'][0]}es near {limits['tweeter'][1]:.0f} Hz
#          woofers  -> RL, RR: {counts['woofer'][0]}/{counts['woofer'][1]} sections,
#                      {limits['woofer'][2]} cascaded {limits['woofer'][0]}es near {limits['woofer'][1]:.0f} Hz
#
# Apple's level-dependent stages are not reproduced -- see the generator.
context.modules = [
  {{ name = libpipewire-module-filter-chain
    flags = [ nofail ]
    args = {{
      node.description = "iMac Speakers"
      media.name       = "iMac Speakers"

      filter.graph = {{
        nodes = [
"""
    body = "\n".join(g.nodes) + "\n        ]\n        links = [\n" + "\n".join(g.links)
    footer = f"""
        ]
        inputs  = [ "{starts['l']}:In" "{starts['r']}:In" ]
        outputs = [ "{ends['tweeter_l']}:Out" "{ends['tweeter_r']}:Out" "{ends['woofer_l']}:Out" "{ends['woofer_r']}:Out" ]
      }}

      capture.props = {{
        node.name      = "imac_speakers"
        media.class    = Audio/Sink
        audio.position = [ FL FR ]
      }}
      playback.props = {{
        node.name         = "imac_speakers_out"
        audio.position    = [ FL FR RL RR ]
        target.object     = "alsa_output.pci-0000_00_1f.3.analog-surround-40"
        stream.dont-remix = true
        node.passive      = true
      }}
    }}
  }}
]
"""
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    open(args.output, "w").write(header + body + footer)
    print(f"nodes: {len(g.nodes)}, links: {len(g.links)}")
    print(f"tweeters {counts['tweeter']}, woofers {counts['woofer']}")
    print(f"written: {args.output}")


if __name__ == "__main__":
    main()
