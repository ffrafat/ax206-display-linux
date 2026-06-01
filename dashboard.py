#!/usr/bin/env python3
"""Live macOS system-monitor dashboard for the AX206 USB screen.

Renders a 480x320 dark dashboard with two ring gauges (CPU, RAM), live
network up/down speeds, load average, a clock, and a scrolling CPU-history
sparkline. Pushes a new frame about once a second.

Run:
  .venv/bin/python dashboard.py
  .venv/bin/python dashboard.py --interval 1.0 --brightness 7
Stop with Ctrl-C.
"""
from __future__ import annotations

import argparse
import collections
import socket
import time

import psutil
from PIL import Image, ImageDraw, ImageFont

from ax206 import AX206Display, to_rgb565_be

W, H = 480, 320
SS = 2  # supersample factor for smooth arcs/text

# ---- palette ----
BG        = (12, 14, 20)
PANEL     = (22, 26, 36)
INK       = (235, 240, 248)
DIM       = (120, 130, 148)
TRACK     = (40, 46, 60)
CYAN      = (38, 208, 222)
GREEN     = (60, 214, 130)
ORANGE    = (245, 170, 60)
RED       = (240, 80, 90)
VIOLET    = (150, 130, 245)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    paths = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


# Pre-load fonts at supersampled sizes.
F_CLOCK = _font(34 * SS, bold=True)
F_BIG   = _font(40 * SS, bold=True)
F_MED   = _font(19 * SS, bold=True)
F_SMALL = _font(15 * SS)
F_TINY  = _font(12 * SS)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def load_color(pct: float):
    """Green -> orange -> red as utilization climbs."""
    if pct < 50:
        return lerp(GREEN, ORANGE, pct / 50)
    return lerp(ORANGE, RED, min(1.0, (pct - 50) / 50))


def fmt_rate(bytes_per_s: float) -> str:
    if bytes_per_s >= 1e6:
        return f"{bytes_per_s / 1e6:.1f} MB/s"
    if bytes_per_s >= 1e3:
        return f"{bytes_per_s / 1e3:.0f} KB/s"
    return f"{bytes_per_s:.0f} B/s"


def text_centered(d, cx, cy, text, font, fill):
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), text, font=font, fill=fill)


def ring(d, cx, cy, r, width, pct, color, label, value_text):
    """Draw a ring gauge centred at (cx, cy)."""
    box = [cx - r, cy - r, cx + r, cy + r]
    # track
    d.arc(box, 0, 360, fill=TRACK, width=width)
    # value arc, starting at top (-90deg), clockwise
    if pct > 0:
        end = -90 + 360 * min(1.0, pct / 100)
        d.arc(box, -90, end, fill=color, width=width)
    # center value
    text_centered(d, cx, cy - 6 * SS, value_text, F_BIG, INK)
    text_centered(d, cx, cy + 26 * SS, label, F_MED, DIM)


def render(state) -> Image.Image:
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d = ImageDraw.Draw(img)

    # ---- header ----
    clock = time.strftime("%H:%M:%S")
    d.text((16 * SS, 10 * SS), clock, font=F_CLOCK, fill=INK)
    date = time.strftime("%a %d %b")
    la = state["load"]
    d.text((W * SS - 16 * SS, 12 * SS), date, font=F_SMALL, fill=DIM, anchor="ra")
    d.text((W * SS - 16 * SS, 32 * SS),
           f"load {la[0]:.2f}  {la[1]:.2f}  {la[2]:.2f}",
           font=F_TINY, fill=DIM, anchor="ra")

    # ---- ring gauges ----
    cpu = state["cpu"]
    ram = state["ram"]
    ring(d, 112 * SS, 150 * SS, 64 * SS, 14 * SS, cpu,
         load_color(cpu), "CPU", f"{cpu:.0f}%")
    ring(d, 368 * SS, 150 * SS, 64 * SS, 14 * SS, ram,
         CYAN, "RAM", f"{ram:.0f}%")

    # RAM detail under ring
    text_centered(d, 368 * SS, 226 * SS,
                  f"{state['ram_used']:.1f} / {state['ram_total']:.1f} GB",
                  F_TINY, DIM)
    # CPU detail
    text_centered(d, 112 * SS, 226 * SS,
                  f"{state['cores']} cores", F_TINY, DIM)

    # ---- network (center column) ----
    cx = 240 * SS
    d.text((cx, 116 * SS), f"↓ {fmt_rate(state['rx'])}",
           font=F_MED, fill=GREEN, anchor="ma")
    d.text((cx, 150 * SS), f"↑ {fmt_rate(state['tx'])}",
           font=F_MED, fill=ORANGE, anchor="ma")
    text_centered(d, cx, 192 * SS, "NET", F_TINY, DIM)

    # ---- CPU history sparkline ----
    hx0, hy0, hx1, hy1 = 16 * SS, 250 * SS, (W - 16) * SS, (H - 12) * SS
    d.rounded_rectangle([hx0, hy0, hx1, hy1], radius=8 * SS, fill=PANEL)
    hist = state["cpu_hist"]
    if len(hist) >= 2:
        n = len(hist)
        plot_w = hx1 - hx0 - 12 * SS
        plot_h = hy1 - hy0 - 16 * SS
        x0 = hx0 + 6 * SS
        y_base = hy1 - 8 * SS
        pts = []
        for i, v in enumerate(hist):
            x = x0 + plot_w * i / (n - 1)
            y = y_base - plot_h * (v / 100)
            pts.append((x, y))
        # filled area
        poly = pts + [(pts[-1][0], y_base), (pts[0][0], y_base)]
        d.polygon(poly, fill=(CYAN[0] // 4, CYAN[1] // 4, CYAN[2] // 4))
        d.line(pts, fill=CYAN, width=2 * SS, joint="curve")
    text_centered(d, hx0 + 34 * SS, hy0 + 13 * SS, "CPU", F_TINY, DIM)

    # downsample for smoothness
    return img.resize((W, H), Image.LANCZOS)


def collect_state(prev_net, dt, cpu_hist):
    cpu = psutil.cpu_percent()
    vm = psutil.virtual_memory()
    net = psutil.net_io_counters()
    rx = (net.bytes_recv - prev_net.bytes_recv) / dt if dt > 0 else 0
    tx = (net.bytes_sent - prev_net.bytes_sent) / dt if dt > 0 else 0
    cpu_hist.append(cpu)
    try:
        load = psutil.getloadavg()
    except Exception:
        load = (0, 0, 0)
    return {
        "cpu": cpu,
        "ram": vm.percent,
        "ram_used": vm.used / 1e9,
        "ram_total": vm.total / 1e9,
        "cores": psutil.cpu_count(),
        "rx": rx,
        "tx": tx,
        "load": load,
        "cpu_hist": list(cpu_hist),
    }, net


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between frames")
    ap.add_argument("--frames", type=int, default=0, help="stop after N frames (0=forever)")
    args = ap.parse_args()

    cpu_hist = collections.deque(maxlen=120)
    psutil.cpu_percent()  # prime
    prev_net = psutil.net_io_counters()
    last = time.time()

    with AX206Display() as s:
        print(f"dashboard running ({s.width}x{s.height}), Ctrl-C to stop")
        count = 0
        glitches = 0
        consec = 0
        MAX_CONSEC = 6  # after this many failures in a row, give up (replug needed)
        while True:
            time.sleep(args.interval)
            now = time.time()
            dt = now - last
            last = now
            state, prev_net = collect_state(prev_net, dt, cpu_hist)
            t0 = time.time()
            frame = render(state)
            try:
                s.draw_image(frame, fit="stretch")
            except Exception as e:
                glitches += 1
                consec += 1
                print(f"frame {count + 1}: glitch ({e}) consec={consec}", flush=True)
                # Escalating recovery: clear_halt+MSC-reset first, then a full
                # reopen of the handle if that wasn't enough.
                if consec <= 2:
                    s.recover()
                else:
                    print("  attempting full reopen…", flush=True)
                    ok = s.reopen()
                    print(f"  reopen {'OK' if ok else 'FAILED'}", flush=True)
                if consec >= MAX_CONSEC:
                    print("Too many consecutive failures — the device is wedged "
                          "and needs a physical replug. Exiting.", flush=True)
                    return 1
                continue
            consec = 0
            push_ms = (time.time() - t0) * 1000
            count += 1
            if count == 1 or count % 10 == 0:
                print(f"frame {count}: cpu {state['cpu']:.0f}% ram {state['ram']:.0f}% "
                      f"render+push {push_ms:.0f}ms  (glitches {glitches})", flush=True)
            if args.frames and count >= args.frames:
                break
    return 0


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
