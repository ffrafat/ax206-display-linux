#!/usr/bin/env python3
"""Cross-platform system-monitor dashboard for the AX206 SmartCool USB screen.

Alternates two screens every 3 seconds:
  - Stats: 2×2 cards (CPU, RAM, Disk, Net)
  - Clock: full-screen HH:MM with blinking colon (bold hours, thin minutes)
"""
from __future__ import annotations

import argparse
import os
import socket
import time
from typing import Optional

import psutil
from PIL import Image, ImageDraw, ImageFont

from ax206 import AX206Display

W, H = 480, 320
SS = 2

M = 16
G = 12
HDR = 36

ROTATE_CLOCK = 3.0
ROTATE_STATS = 3.0

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

# ---- Fonts ----
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_LIGHT    = os.path.join(SCRIPT_DIR, "assets", "fonts", "Inter-Light.ttf")
FONT_REGULAR  = os.path.join(SCRIPT_DIR, "assets", "fonts", "Inter-Regular.ttf")
FONT_SEMIBOLD = os.path.join(SCRIPT_DIR, "assets", "fonts", "Inter-SemiBold.ttf")


def _font(size: int, bold: bool = False, light: bool = False) -> ImageFont.FreeTypeFont:
    if light:
        path = FONT_LIGHT
    elif bold:
        path = FONT_SEMIBOLD
    else:
        path = FONT_REGULAR
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        fallbacks: list[str] = []
        if light:
            fallbacks = [
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/System/Library/Fonts/HelveticaNeue.ttc",
            ]
        elif bold:
            fallbacks = [
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            ]
        else:
            fallbacks = [
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "/System/Library/Fonts/Supplemental/Arial.ttf",
            ]
        for p in fallbacks:
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
        return ImageFont.load_default()


F_IP    = _font(12 * SS)
F_LABEL = _font(13 * SS, bold=True)
F_VALUE = _font(28 * SS, bold=True)
F_SUB   = _font(11 * SS)
F_NET   = _font(17 * SS, bold=True)
F_NET_SUB = _font(11 * SS)


# ---- Helpers ----

def get_ip_address() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def fmt_rate(bytes_per_s: float) -> str:
    if bytes_per_s >= 1e6:
        return f"{bytes_per_s / 1e6:.1f} MB/s"
    if bytes_per_s >= 1e3:
        return f"{bytes_per_s / 1e3:.0f} KB/s"
    return f"{bytes_per_s:.0f} B/s"


def fmt_gb(bytes_n: float) -> str:
    if bytes_n >= 1e9:
        return f"{bytes_n / 1e9:.1f} GB"
    if bytes_n >= 1e6:
        return f"{bytes_n / 1e6:.0f} MB"
    return f"{bytes_n / 1e3:.0f} KB"


def fmt_used_total(used: float, total: float) -> str:
    return f"{fmt_gb(used)} / {fmt_gb(total)}"


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
                   accent: tuple[int, int, int], value_color: Optional[tuple[int, int, int]] = None,
                   sub: str = "") -> None:
    x0, y0, x1, y1 = rect
    pad = 14 * SS
    inner_w = x1 - x0 - 2 * pad

    d.rounded_rectangle(rect, radius=10 * SS, fill=PANEL)
    d.text((x0 + pad, y0 + pad), title, font=F_LABEL, fill=MUTED)

    val = fit_text(value, F_VALUE, inner_w)
    color = value_color if value_color else INK
    cy = y0 + (y1 - y0) * (0.38 if sub else 0.44)
    text_centered(d, (x0 + x1) / 2, cy, val, F_VALUE, color)

    if sub:
        sub_text = fit_text(sub, F_SUB, inner_w)
        text_centered(d, (x0 + x1) / 2, cy + 34 * SS, sub_text, F_SUB, MUTED)

    bar_h = 12 * SS
    bar_y1 = y1 - pad
    bar_y0 = bar_y1 - bar_h
    progress_bar(d, x0 + pad, bar_y0, x1 - pad, bar_y1, pct, usage_color(pct, accent))


def draw_net_card(d: ImageDraw.ImageDraw, rect: tuple[float, float, float, float],
                  rx: float, tx: float, rx_total: float, tx_total: float) -> None:
    x0, y0, x1, y1 = rect
    pad = 14 * SS
    inner_w = x1 - x0 - 2 * pad

    d.rounded_rectangle(rect, radius=10 * SS, fill=PANEL)
    d.text((x0 + pad, y0 + pad), "NET", font=F_LABEL, fill=MUTED)

    mid_y = y0 + (y1 - y0) * 0.50
    gap = 40 * SS
    down = fit_text(f"↓ {fmt_rate(rx)}", F_NET, inner_w)
    up = fit_text(f"↑ {fmt_rate(tx)}", F_NET, inner_w)
    text_centered(d, (x0 + x1) / 2, mid_y - gap / 2, down, F_NET, NET_DN)
    text_centered(d, (x0 + x1) / 2, mid_y + gap / 2, up, F_NET, NET_UP)

    sub = fit_text(f"{fmt_gb(tx_total)} / {fmt_gb(rx_total)}", F_NET_SUB, inner_w)
    text_centered(d, (x0 + x1) / 2, y1 - pad - 8 * SS, sub, F_NET_SUB, MUTED)


def _clock_fonts(max_w: float) -> tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    """Pick the largest hour/separator/minute fonts that fit the panel width."""
    for pt in range(118, 48, -2):
        f_hour = _font(pt * SS, bold=True)
        f_sep = _font(pt * SS, bold=True)
        f_min = _font(pt * SS, light=True)
        sample_w = (
            text_width("88", f_hour) + text_width(":", f_sep) + text_width("88", f_min)
        )
        if sample_w <= max_w:
            return f_hour, f_sep, f_min
    f = _font(48 * SS, bold=True)
    return f, f, _font(48 * SS, light=True)


# ---- Renderers ----

def render_stats(state: dict) -> Image.Image:
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d = ImageDraw.Draw(img)

    ip = fit_text(state["ip"], F_IP, (W - 2 * M) * SS)
    text_centered(d, (W * SS) / 2, 18 * SS, ip, F_IP, MUTED)
    d.line([(M * SS, (HDR - 6) * SS), ((W - M) * SS, (HDR - 6) * SS)], fill=TRACK, width=2 * SS)

    cpu_pct = state["cpu"]
    temp = state["temp"]
    ram_pct = state["ram"]
    disk_pct = state["disk_root_pct"]

    if temp is not None:
        cpu_value = f"{temp:.0f}°C"
        cpu_bar = cpu_pct
        cpu_val_color = temp_color(temp)
        cpu_sub = f"{cpu_pct:.0f}% · {state['cpu_freq']:.1f} GHz"
    else:
        cpu_value = f"{cpu_pct:.0f}%"
        cpu_bar = cpu_pct
        cpu_val_color = usage_color(cpu_pct, CPU)
        cpu_sub = f"{state['cpu_freq']:.1f} GHz"

    draw_stat_card(d, card_rect(0, 0), "CPU", cpu_value, cpu_bar, CPU, cpu_val_color, sub=cpu_sub)
    draw_stat_card(d, card_rect(1, 0), "RAM", f"{ram_pct:.0f}%", ram_pct, RAM,
                   sub=fmt_used_total(state["ram_used"], state["ram_total"]))
    draw_stat_card(d, card_rect(0, 1), "DISK", f"{disk_pct:.0f}%", disk_pct, DISK,
                   sub=fmt_used_total(state["disk_used"], state["disk_total"]))
    draw_net_card(d, card_rect(1, 1), state["rx"], state["tx"],
                  state["net_recv_total"], state["net_sent_total"])

    return img.resize((W, H), Image.Resampling.LANCZOS)


def render_clock(show_sep: bool, now: Optional[time.struct_time] = None) -> Image.Image:
    """Full-screen digital clock: bold HH, thin MM, blinking colon."""
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d = ImageDraw.Draw(img)

    t = now or time.localtime()
    hh = f"{t.tm_hour:02d}"
    mm = f"{t.tm_min:02d}"

    max_w = (W - 32) * SS
    f_hour, f_sep, f_min = _clock_fonts(max_w)
    sep_color = INK if show_sep else BG  # blink without layout shift

    parts = (hh, ":", mm)
    fonts = (f_hour, f_sep, f_min)
    widths = [text_width(p, f) for p, f in zip(parts, fonts)]
    total_w = sum(widths)
    cy = (H * SS) / 2
    x = (W * SS - total_w) / 2

    for part, font, w in zip(parts, fonts, widths):
        fill = sep_color if part == ":" else INK
        bbox = font.getbbox(part)
        th = bbox[3] - bbox[1]
        d.text((x - bbox[0], cy - th / 2 - bbox[1]), part, font=font, fill=fill)
        x += w

    return img.resize((W, H), Image.Resampling.LANCZOS)


# ---- Core Cycle ----

def collect_state(prev_net, dt: float) -> tuple[dict, object]:
    cpu = psutil.cpu_percent()
    vm = psutil.virtual_memory()
    net = psutil.net_io_counters()

    try:
        freq = psutil.cpu_freq()
        cpu_freq = freq.current / 1000.0 if freq and freq.current else 0.0
    except Exception:
        cpu_freq = 0.0

    try:
        disk = psutil.disk_usage("/")
        disk_pct = disk.percent
        disk_used = disk.used
        disk_total = disk.total
    except Exception:
        disk_pct = 0.0
        disk_used = 0.0
        disk_total = 0.0

    rx = (net.bytes_recv - prev_net.bytes_recv) / dt if dt > 0 else 0
    tx = (net.bytes_sent - prev_net.bytes_sent) / dt if dt > 0 else 0

    return {
        "cpu": cpu,
        "cpu_freq": cpu_freq,
        "ram": vm.percent,
        "ram_used": vm.used,
        "ram_total": vm.total,
        "disk_root_pct": disk_pct,
        "disk_used": disk_used,
        "disk_total": disk_total,
        "rx": rx,
        "tx": tx,
        "net_recv_total": net.bytes_recv,
        "net_sent_total": net.bytes_sent,
        "temp": get_cpu_temp(),
        "ip": get_ip_address(),
    }, net


def main() -> int:
    ap = argparse.ArgumentParser(description="System monitor dashboard for the AX206 USB display")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="seconds between frame updates (use 1.0 for clock blink)")
    ap.add_argument("--clock-secs", type=float, default=ROTATE_CLOCK,
                    help="seconds to show the clock screen")
    ap.add_argument("--stats-secs", type=float, default=ROTATE_STATS,
                    help="seconds to show the stats screen")
    ap.add_argument("--frames", type=int, default=0, help="exit after N frames (0=run forever)")
    args = ap.parse_args()

    psutil.cpu_percent()
    prev_net = psutil.net_io_counters()
    last = time.time()

    screen = "clock"
    screen_started = time.time()

    with AX206Display() as s:
        print(f"sysdash running ({s.width}x{s.height}), "
              f"clock {args.clock_secs}s / stats {args.stats_secs}s, Ctrl-C to stop")
        count = 0
        glitches = 0
        consec = 0

        while True:
            now = time.time()
            elapsed = now - screen_started
            if screen == "clock" and elapsed >= args.clock_secs:
                screen = "stats"
                screen_started = now
            elif screen == "stats" and elapsed >= args.stats_secs:
                screen = "clock"
                screen_started = now

            dt = now - last
            last = now

            if screen == "clock":
                show_sep = int(now) % 2 == 0
                frame = render_clock(show_sep)
            else:
                state, prev_net = collect_state(prev_net, dt)
                frame = render_stats(state)

            t0 = time.time()
            try:
                s.draw_image(frame, fit="stretch")
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
                label = "clock" if screen == "clock" else "stats"
                print(f"frame {count} [{label}]: push {ms:.0f}ms (glitches {glitches})", flush=True)

            if args.frames and count >= args.frames:
                break

            if screen == "clock":
                time.sleep(max(0.05, 1.0 - (time.time() % 1.0)))
            else:
                time.sleep(args.interval)

    return 0


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
