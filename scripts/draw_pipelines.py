#!/usr/bin/env python3
"""Draw one pipeline diagram per version where the audio path changed.

    python scripts/draw_pipelines.py          # writes docs/pipeline/v*.svg

No dependencies. Each version is a delta on the one before it, and a box is
coloured by comparing it with the previous version (green = new, amber = its
text changed), so the diagrams show what moved without anyone having to say
so. Positions are a fixed grid shared by all versions, which is what makes
them comparable side by side. The narrative for each version is in
docs/PIPELINE_HISTORY.md; when the audio path changes again, add a delta here.

Lanes: the real-time callback on top; the worker thread below it, for mono
models (downmix first) and, from v4, for stereo separators (wants_stereo).
"""

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent.parent / "docs" / "pipeline"
COL_W, NODE_W, NODE_H, X0 = 150, 132, 62, 36
LANE_Y = {"A": 125, "B": 265, "C": 405}
LANE_NAME = {"A": "Audio callback: real-time thread (PortAudio, 20 ms blocks)",
             "B": "Worker thread: mono models",
             "C": "Worker thread: stereo separators (wants_stereo)"}


class N:
    def __init__(self, lane, col, lines, span=1, opt=False, fifo=False):
        self.lane, self.col, self.lines = lane, col, lines
        self.span, self.opt, self.fifo = span, opt, fifo


# Each version: nodes added or replaced, nodes removed, explicit cross-lane
# edges (the lane chains are drawn automatically), and what the caption says.
VERSIONS = [
    dict(key="v0", title="v0: initial import", date="2026-07-04", ref="c34263c",
         nodes={
             "cap": N("A", 0, ["Null 'trap' sink", ".monitor source"]),
             "cb": N("A", 1, ["PortAudio duplex", "callback, 20 ms", "48 kHz stereo"]),
             "mix": N("A", 8, ["mix dry / wet", "20 ms ramp"]),
             "out": N("A", 10, ["Real output sink"]),
             "q": N("B", 1, ["input queue", "to worker thread"], fifo=True),
             "dm": N("B", 2, ["downmix", "L+R to mono"]),
             "model": N("B", 4, ["[resample down] MODEL [up]", "gtcrn, dpdfnet,", "speechdenoiser"], span=2),
             "wf": N("B", 10, ["wet FIFO (mono)", "cap 160 ms,", "drops oldest"], fifo=True),
         },
         edges=[("cb", "q", "block copy"), ("wf", "mix", "wet")],
         notes=["Dry is the live input block; wet is one mono signal copied to both channels.",
                "If wet is not ready the callback plays dry for the missing samples.",
                "Models: gtcrn, dpdfnet, speechdenoiser. Every model sees a mono downmix."]),
    dict(key="v1", title="v1: v0.1.2 / v0.1.3", date="2026-07-24..25", ref="ae88ab3, a33f1e4",
         nodes={
             "dry": N("A", 3, ["dry FIFO", "lockstep with wet"], fifo=True),
             "mix": N("A", 8, ["mix dry / wet", "ramp, 0-150 % wet"]),
             "ms": N("B", 3, ["mid/side prefilter", "centre emphasis", "(optional, off)"], opt=True),
             "model": N("B", 4, ["[resample down] MODEL [up]", "gtcrn dpdfnet dtln dpdfnet_hr", "speechdenoiser"], span=2),
             "bl": N("B", 6, ["band-limit", "20 Hz - 20 kHz"]),
         },
         edges=[],
         notes=["New: dry FIFO drained in lockstep with wet. Before, the mix used the live block,",
                "so below 100 % wet it sounded like an echo, not a blend (a33f1e4).",
                "New: mid/side prefilter (off by default), band-limit on the wet signal,",
                "dtln and dpdfnet_hr (native 48 kHz, no resampling), wet gain up to 150 %."]),
    dict(key="v2", title="v2: 0.1.4 (main)", date="2026-08-14..15", ref="42fe75d",
         nodes={
             "cb": N("A", 1, ["PortAudio duplex", "callback, 20 ms", "error: dry; watchdog"]),
             "mix": N("A", 8, ["mix dry / wet", "ramp, 0-300 % wet"]),
             "lim": N("A", 9, ["soft limiter", "when wet > 100 %"]),
         },
         edges=[],
         notes=["New: the stream recovers when PortAudio dies silently, and an exception in the",
                "callback falls back to dry passthrough instead of killing the stream.",
                "New: a soft limiter, so the 300 % wet boost cannot clip.",
                "Also: DPDFNet's state is seeded from the model's own calibration (not a path change)."]),
    dict(key="v3", title="v3: stereo rebuild (B3)", date="2026-08-18", ref="997f66f",
         nodes={
             "reb": N("B", 8, ["stereo rebuild", "mask |wet| / |dry|", "applied to L and R"], opt=True),
             "wf": N("B", 10, ["wet FIFO (stereo)", "cap 160 ms,", "drops oldest"], fifo=True),
         },
         edges=[],
         notes=["New: optional stereo rebuild. The mono model's decision, |wet| / |dry mono|, is",
                "applied per bin to the dry L and R, so the image the mono model destroyed returns.",
                "Shipped off. Also added: the wants_stereo seam in processors, for models that take",
                "the stereo pair themselves (no downmix, no rebuild). Nothing used it yet."]),
    dict(key="v4", title="v4: chunked separator + lag alignment", date="2026-08-19", ref="5a214f5, d2a9ee6, 5cfa446",
         nodes={
             "dry": N("A", 3, ["dry FIFO", "lockstep, lag pad"], fifo=True),
             "lag": N("B", 7, ["lag measure", "off-thread xcorr", "pads dry by lag"]),
             "model_s": N("C", 4, ["MODEL.feed, stereo in", "Spleeter, 1.1 s+ chunks", "no downmix, no rebuild"], span=2),
             "bl_s": N("C", 6, ["band-limit", "L and R"]),
         },
         edges=[("lag", "dry", "pad", "dashed"), ("q", "model_s", ""), ("bl_s", "wf", "")],
         notes=["New: the first stereo separator (Spleeter) through the wants_stereo lane.",
                "New: the processor's lag is measured on its own thread and the dry streams are",
                "padded by it. Before, the stereo mask and the mix were each ~50 ms out of step",
                "(dpdfnet_hr), which sounded doubled. Chunked processors were still broken (see v5)."]),
    dict(key="v5", title="v5: now (feat/separator-spike)", date="2026-10-23", ref="6fda7f0, ceda3a8, fee7051",
         nodes={
             "q": N("B", 1, ["input queue", "sized by latency"], fifo=True),
             "dry": N("A", 3, ["dry FIFO", "lockstep, trim to wet"], fifo=True),
             "reb": N("B", 8, ["stereo rebuild", "mask capped at 1", "(uncommitted)"], opt=True),
             "shp": N("B", 9, ["mask shaper", "floor, smoothing", "every model"], opt=True),
             "wf": N("B", 10, ["wet FIFO", "cap from latency", "trim drops dry too"], fifo=True),
             "model_s": N("C", 4, ["MODEL.feed, stereo in", "Spleeter, StemgenRT", "(5.8 ms, 44.1 kHz)"], span=2),
             "shp_s": N("C", 9, ["mask shaper", "floor, smoothing"], opt=True),
         },
         edges=[("shp_s", "wf", "")],
         notes=["Fixed: FIFO caps follow the processor's declared latency, and a trim on either side",
                "drops the matching samples on the other. Chunked separators were 84 % unprocessed.",
                "New: mask shaper (suppression floor, release smoothing) after every model; off by default.",
                "New: StemgenRT, a causal separator, 5.8 ms. Stereo rebuild clamp 8 to 1 is uncommitted."]),
]

GREEN, AMBER, GREY = ("#d3f9d8", "#2f9e44"), ("#fff3bf", "#e67700"), ("#f1f3f5", "#868e96")


def node_xy(n: N):
    x = X0 + n.col * COL_W
    w = NODE_W + (n.span - 1) * COL_W
    return x, LANE_Y[n.lane] - NODE_H / 2, w, NODE_H


def build(state: dict, edges_extra: list):
    """The lane chains plus explicit cross-lane edges, as (a, b, label, style)."""
    edges = []
    for lane in "ABC":
        ids = sorted((i for i, n in state.items() if n.lane == lane), key=lambda i: state[i].col)
        if lane == "B":   # the wet FIFO ends the B chain but belongs to the lane's right edge
            ids = [i for i in ids if i != "wf"] + (["wf"] if "wf" in state else [])
        for a, b in zip(ids, ids[1:]):
            edges.append((a, b, "", "solid"))
    edges += [(e[0], e[1], e[2], e[3] if len(e) > 3 else "solid") for e in edges_extra]
    return edges


def route(a: N, b: N, ia: str, ib: str):
    ax, ay, aw, ah = node_xy(a)
    bx, by, bw, bh = node_xy(b)
    if a.lane == b.lane:
        return [(ax + aw, ay + ah / 2), (bx, by + bh / 2)]
    acx, bcx = ax + aw / 2, bx + bw / 2
    if (ia, ib) == ("cb", "q"):
        return [(acx, ay + ah), (bcx, by)]
    if ib == "wf" and a.lane == "C":
        return [(ax + aw, ay + ah / 2), (bcx, ay + ah / 2), (bcx, by + bh)]
    if ib == "mix":
        ym = (LANE_Y["A"] + LANE_Y["B"]) / 2 + 6
        return [(acx, ay), (acx, ym), (bcx, ym), (bcx, by + bh)]
    if ib == "dry":
        ym = (LANE_Y["A"] + LANE_Y["B"]) / 2 - 12
        return [(acx, ay), (acx, ym), (bcx, ym), (bcx, by + bh)]
    if ia == "q":
        return [(acx, ay + ah), (acx, by + bh / 2), (bx, by + bh / 2)]
    return [(acx, ay + ah), (bcx, by)]


def render(v: dict, prev: dict, state: dict) -> str:
    lanes = [l for l in "ABC" if any(n.lane == l for n in state.values())]
    n_cols = 11
    W = X0 * 2 + n_cols * COL_W - (COL_W - NODE_W)
    last_y = LANE_Y[lanes[-1]] + NODE_H / 2
    H = int(last_y + 40 + 18 * (len(v["notes"]) + 1) + 24)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
           f'height="{H}" font-family="Helvetica, Arial, sans-serif">',
           '<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
           'markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#495057"/></marker></defs>',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
           f'<text x="{X0}" y="34" font-size="22" font-weight="700" fill="#212529">{escape(v["title"])}</text>',
           f'<text x="{X0}" y="56" font-size="13" fill="#495057">{escape(v["date"])}   commits: {escape(v["ref"])}</text>']
    lx = W - 440
    for i, (label, (fill, stroke)) in enumerate((("new in this version", GREEN),
                                                  ("text changed", AMBER), ("unchanged", GREY))):
        out.append(f'<rect x="{lx + i * 150}" y="22" width="14" height="14" fill="{fill}" stroke="{stroke}"/>'
                   f'<text x="{lx + i * 150 + 19}" y="34" font-size="11" fill="#495057">{label}</text>')
    out.append(f'<text x="{lx}" y="56" font-size="11" fill="#495057">dashed outline = optional, user-toggled</text>')
    for l in lanes:
        y = LANE_Y[l]
        out.append(f'<rect x="12" y="{y - NODE_H / 2 - 22}" width="{W - 24}" height="{NODE_H + 34}" rx="8" '
                   f'fill="#f8f9fa" stroke="#dee2e6"/>')
        out.append(f'<text x="{22 if l == "A" else 262}" y="{y - NODE_H / 2 - 7}" font-size="11" font-weight="700" fill="#868e96">'
                   f'{escape(LANE_NAME[l])}</text>')
    for a, b, label, style in build(state, v["edges"]):
        if a not in state or b not in state:
            continue
        pts = route(state[a], state[b], a, b)
        d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        dash = ' stroke-dasharray="5,4"' if style == "dashed" else ""
        out.append(f'<polyline points="{d}" fill="none" stroke="#495057" stroke-width="1.6"{dash} marker-end="url(#ar)"/>')
        if label:
            mx, my = pts[len(pts) // 2 - 1] if len(pts) > 2 else ((pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2)
            out.append(f'<text x="{mx + 6:.1f}" y="{my - 5:.1f}" font-size="10" fill="#495057">{escape(label)}</text>')
    for i, n in state.items():
        x, y, w, h = node_xy(n)
        if i not in prev:
            fill, stroke = GREEN
        elif prev[i].lines != n.lines:
            fill, stroke = AMBER
        else:
            fill, stroke = GREY
        dash = ' stroke-dasharray="6,3"' if n.opt else ""
        rx = 26 if n.fifo else 6
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
                   f'stroke="{stroke}" stroke-width="1.8"{dash}/>')
        top = y + h / 2 - (len(n.lines) - 1) * 7.5 + 4
        for k, line in enumerate(n.lines):
            weight = "700" if k == 0 else "400"
            out.append(f'<text x="{x + w / 2:.1f}" y="{top + k * 15:.1f}" font-size="10.5" font-weight="{weight}" '
                       f'text-anchor="middle" fill="#212529">{escape(line)}</text>')
    y = last_y + 44
    out.append(f'<text x="{X0}" y="{y}" font-size="13" font-weight="700" fill="#212529">What changed</text>')
    for k, line in enumerate(v["notes"]):
        out.append(f'<text x="{X0}" y="{y + 20 + k * 18}" font-size="12" fill="#343a40">{escape(line)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    state: dict = {}
    edges: dict = {}
    for v in VERSIONS:
        prev = dict(state)
        state = {**state, **v["nodes"]}
        edges.update({(e[0], e[1]): e for e in v["edges"]})
        v = {**v, "edges": list(edges.values())}
        path = OUT / f"{v['key']}.svg"
        path.write_text(render(v, prev, state))
        print(path)


if __name__ == "__main__":
    main()
