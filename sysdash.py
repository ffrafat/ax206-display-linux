#!/usr/bin/env python3
"""Cross-platform system-monitor dashboard for the AX206 SmartCool USB screen.

Clean 2×2 stat layout: CPU, RAM, Disk, Net — large values, single bar per card.
"""
from __future__ import annotations

import argparse
import os
import platform
import time
from typing import Optional

import psutil
from PIL import Image, ImageDraw, ImageFont

from ax206 import AX206Display

W, H = 480, 320
SS = 2

M = 16   # outer margin
G = 12   # gap between cards
HDR = 36 # header height

# ---- Palette ----
BG     = (10, 12, 16)
PANEL  = (24, 28, 36)
INK    = (245, 247, 250)
MUTED  = (130, 138, 152)
TRACK  = (48, 54, 66)
CPU    = (96, 165, 250)
RAM    = (192, 132, 252)
DISK   = (52, 211, 153)
NET_DN = (74, 222, 128)
NET_UP = (250, 204, 21)
HOT    = (248, 113, 113)
WARN   = (251, 146, 60)

# ---- Fonts (logical pt × SS) ----
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_REGULAR = os.path.join(SCRIPT_DIR, "assets", "fonts", "Inter-Regular.ttf")
FONT_SEMIBOLD = os.path.join(SCRIPT_DIR, "assets", "fonts", "Inter-SemiBold.ttf")


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_SEMIBOLD if bold else FONT_REGULAR
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        for p in (
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/liberation/LiberationSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
            else "/System/Library/Fonts/Supplemental/Arial.ttf",
        ):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
        return ImageFont.load_default()


F_HOST  = _font(20 * SS, bold=True)
F_LABEL = _font(13 * SS, bold=True)
F_VALUE = _font(30 * SS, bold=True)
F_NET   = _font(17 * SS, bold=True)


# ---- Helpers ----

def fmt_rate(bytes_per_s: float) -> str:
    if bytes_per_s >= 1e6:
        return f"{bytes_per_s / 1e6:.1f} MB/s"
    if bytes_per_s >= 1e3:
        return f"{bytes_per_s / 1e3:.0f} KB/s"
    return f"{bytes_per_s:.0f} B/s"


def usage_color(pct: float, accent: tuple[int, int, int]) -> tuple[int, int, int]:
    if pct >= 85:
        return HOT
    if pct >= 70:
        return WARN
    return accent


def temp_color(temp: float) -> tuple[int, int, int]:
    if temp >= 80:
        return HOT
    if temp >= 65:
        return WARN
    return CPU


def text_width(text: str, font: ImageFont.FreeTypeFont) -> float:
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0]


def fit_text(text: str, font: ImageFont.FreeTypeFont, max_w: float) -> str:
    if text_width(text, font) <= max_w:
        return text
    while len(text) > 1 and text_width(text + "…", font) > max_w:
        text = text[:-1]
    return text + "…"


def text_centered(d: ImageDraw.ImageDraw, cx: float, cy: float, text: str,
                  font: ImageFont.FreeTypeFont, fill: tuple[int, int, int]) -> None:
    bbox = font.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), text, font=font, fill=fill)


def get_cpu_temp() -> Optional[float]:
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for key in ("coretemp", "cpu_thermal", "cpu-thermal", "k10temp", "zenpower"):
                if key in temps and temps[key]:
                    return temps[key][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
    except Exception:
        pass
    try:
        path = "/sys/class/thermal/thermal_zone0/temp"
        if os.path.exists(path):
            with open(path) as f:
                return float(f.read().strip()) / 1000.0
    except Exception:
        pass
    return None


def card_rect(col: int, row: int) -> tuple[float, float, float, float]:
    col_w = (W - 2 * M - G) / 2
    row_h = (H - HDR - M - G) / 2
    x0 = M + col * (col_w + G)
    y0 = HDR + row * (row_h + G)
    return x0 * SS, y0 * SS, (x0 + col_w) * SS, (y0 + row_h) * SS


def progress_bar(d: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float,
                 pct: float, color: tuple[int, int, int]) -> None:
    d.rounded_rectangle([x0, y0, x1, y1], radius=3 * SS, fill=TRACK)
    if pct > 0:
        fill_w = (x1 - x0) * min(1.0, pct / 100)
        d.rounded_rectangle([x0, y0, x0 + fill_w, y1], radius=3 * SS, fill=color)


def draw_stat_card(d: ImageDraw.ImageDraw, rect: tuple[float, float, float, float],
                   title: str, value: str, pct: float,
                   accent: tuple[int, int, int], value_color: Optional[tuple[int, int, int]] = None) -> None:
    x0, y0, x1, y1 = rect
    pad = 14 * SS
    inner_w = x1 - x0 - 2 * pad

    d.rounded_rectangle(rect, radius=10 * SS, fill=PANEL)
    d.text((x0 + pad, y0 + pad), title, font=F_LABEL, fill=MUTED)

    val = fit_text(value, F_VALUE, inner_w)
    color = value_color if value_color else INK
    cy = y0 + (y1 - y0) * 0.44
    text_centered(d, (x0 + x1) / 2, cy, val, F_VALUE, color)

    bar_h = 12 * SS
    bar_y1 = y1 - pad
    bar_y0 = bar_y1 - bar_h
    progress_bar(d, x0 + pad, bar_y0, x1 - pad, bar_y1, pct, usage_color(pct, accent))


def draw_net_card(d: ImageDraw.ImageDraw, rect: tuple[float, float, float, float],
                  rx: float, tx: float) -> None:
    x0, y0, x1, y1 = rect
    pad = 14 * SS
    inner_w = x1 - x0 - 2 * pad

    d.rounded_rectangle(rect, radius=10 * SS, fill=PANEL)
    d.text((x0 + pad, y0 + pad), "NET", font=F_LABEL, fill=MUTED)

    mid_y = y0 + (y1 - y0) * 0.52
    gap = 36 * SS
    down = fit_text(f"↓ {fmt_rate(rx)}", F_NET, inner_w)
    up = fit_text(f"↑ {fmt_rate(tx)}", F_NET, inner_w)
    text_centered(d, (x0 + x1) / 2, mid_y - gap / 2, down, F_NET, NET_DN)
    text_centered(d, (x0 + x1) / 2, mid_y + gap / 2, up, F_NET, NET_UP)


# ---- Renderer ----

def render(state: dict) -> Image.Image:
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d = ImageDraw.Draw(img)

    hostname = fit_text(platform.node().split(".")[0].upper(), F_HOST, (W - 2 * M) * SS)
    d.text((M * SS, 10 * SS), hostname, font=F_HOST, fill=INK)
    d.line([(M * SS, (HDR - 6) * SS), ((W - M) * SS, (HDR - 6) * SS)], fill=TRACK, width=2 * SS)

    cpu_pct = state["cpu"]
    temp = state["temp"]
    ram_pct = state["ram"]
    disk_pct = state["disk_root_pct"]

    if temp is not None:
        cpu_value = f"{temp:.0f}°C"
        cpu_bar = cpu_pct
        cpu_val_color = temp_color(temp)
    else:
        cpu_value = f"{cpu_pct:.0f}%"
        cpu_bar = cpu_pct
        cpu_val_color = usage_color(cpu_pct, CPU)

    draw_stat_card(d, card_rect(0, 0), "CPU", cpu_value, cpu_bar, CPU, cpu_val_color)
    draw_stat_card(d, card_rect(1, 0), "RAM", f"{ram_pct:.0f}%", ram_pct, RAM)
    draw_stat_card(d, card_rect(0, 1), "DISK", f"{disk_pct:.0f}%", disk_pct, DISK)
    draw_net_card(d, card_rect(1, 1), state["rx"], state["tx"])

    return img.resize((W, H), Image.Resampling.LANCZOS)


# ---- Core Cycle ----

def collect_state(prev_net, dt: float) -> tuple[dict, object]:
    cpu = psutil.cpu_percent()
    vm = psutil.virtual_memory()
    net = psutil.net_io_counters()

    try:
        disk_pct = psutil.disk_usage("/").percent
    except Exception:
        disk_pct = 0.0

    rx = (net.bytes_recv - prev_net.bytes_recv) / dt if dt > 0 else 0
    tx = (net.bytes_sent - prev_net.bytes_sent) / dt if dt > 0 else 0

    return {
        "cpu": cpu,
        "ram": vm.percent,
        "disk_root_pct": disk_pct,
        "rx": rx,
        "tx": tx,
        "temp": get_cpu_temp(),
    }, net


def main() -> int:
    ap = argparse.ArgumentParser(description="System monitor dashboard for the AX206 USB display")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between screen updates")
    ap.add_argument("--frames", type=int, default=0, help="exit after N frames (0=run forever)")
    args = ap.parse_args()

    psutil.cpu_percent()
    prev_net = psutil.net_io_counters()
    last = time.time()

    with AX206Display() as s:
        print(f"sysdash running ({s.width}x{s.height}), Ctrl-C to stop")
        count = 0
        glitches = 0
        consec = 0

        while True:
            time.sleep(args.interval)
            now = time.time()
            dt = now - last
            last = now

            state, prev_net = collect_state(prev_net, dt)
            t0 = time.time()

            try:
                s.draw_image(render(state), fit="stretch")
            except Exception as e:
                glitches += 1
                consec += 1
                print(f"frame {count + 1}: glitch ({e}) consec={consec}", flush=True)
                if consec <= 2:
                    s.recover()
                else:
                    print("  attempting full reopen…", flush=True)
                    ok = s.reopen()
                    print(f"  reopen {'OK' if ok else 'FAILED'}", flush=True)
                if consec >= 6:
                    print("Too many consecutive failures — display needs physical replug. Exiting.", flush=True)
                    return 1
                continue

            consec = 0
            count += 1
            if count == 1 or count % 10 == 0:
                ms = (time.time() - t0) * 1000
                print(f"frame {count}: cpu {state['cpu']:.0f}% ram {state['ram']:.0f}% "
                      f"disk {state['disk_root_pct']:.0f}% push {ms:.0f}ms (glitches {glitches})", flush=True)

            if args.frames and count >= args.frames:
                break
    return 0


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
