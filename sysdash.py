#!/usr/bin/env python3
"""Linux-focused system-monitor dashboard for the AX206 SmartCool USB screen.

Renders a 480x320 dark dashboard for the SmartCool AX206 variant with:
  - Header: Hostname, OS distro info, clock, date, and CPU temp.
  - Three Ring Gauges: CPU (%), RAM (%), and Disk (%).
  - Bottom-Left Panel: Live Network download/upload rates.
  - Bottom-Right Panel: CPU load history sparkline.
"""
from __future__ import annotations

import argparse
import collections
import os
import platform
import socket
import time
from typing import Optional

import psutil
from PIL import Image, ImageDraw, ImageFont

from ax206 import AX206Display, to_rgb565_be

W, H = 480, 320
SS = 2  # Supersample factor for crisp lines and text

# ---- Modern Futuristic Palette ----
BG        = (10, 11, 15)      # Charcoal Black
PANEL     = (18, 20, 28)      # Dark Slate
INK       = (245, 246, 250)    # Ice White
DIM       = (143, 152, 179)    # Muted Silver
TRACK     = (34, 38, 51)       # Dark Track Ring
CYAN      = (0, 229, 255)      # CPU (Cyan)
PURPLE    = (188, 0, 255)      # RAM (Purple)
GREEN     = (0, 230, 118)      # Disk / Net Down (Green)
ORANGE    = (255, 145, 0)      # Net Up (Orange)
RED       = (255, 23, 68)      # Alert / High Temp (Red)

# Blended transparent fill for sparkline (15% Cyan over BG)
SPARK_FILL = (8, 44, 51)

# ---- Font Loading ----
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_REGULAR_PATH = os.path.join(SCRIPT_DIR, "assets", "fonts", "Inter-Regular.ttf")
FONT_SEMIBOLD_PATH = os.path.join(SCRIPT_DIR, "assets", "fonts", "Inter-SemiBold.ttf")


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_SEMIBOLD_PATH if bold else FONT_REGULAR_PATH
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        fallback_paths = [
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/liberation/LiberationSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
            else "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
        for p in fallback_paths:
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
        return ImageFont.load_default()


# Pre-load scaled fonts
F_CLOCK = _font(28 * SS, bold=True)
F_BIG   = _font(24 * SS, bold=True)
F_MED   = _font(15 * SS, bold=True)
F_SMALL = _font(13 * SS)
F_TINY  = _font(11 * SS)


# ---- Helper Functions ----

def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def cpu_color(pct: float) -> tuple[int, int, int]:
    """Transitions CPU color Cyan -> Orange -> Red."""
    if pct < 50:
        return lerp(CYAN, ORANGE, pct / 50)
    return lerp(ORANGE, RED, min(1.0, (pct - 50) / 50))


def fmt_rate(bytes_per_s: float) -> str:
    if bytes_per_s >= 1e6:
        return f"{bytes_per_s / 1e6:.1f} MB/s"
    if bytes_per_s >= 1e3:
        return f"{bytes_per_s / 1e3:.0f} KB/s"
    return f"{bytes_per_s:.0f} B/s"


def text_centered(d: ImageDraw.ImageDraw, cx: float, cy: float, text: str, font: ImageFont.FreeTypeFont, fill: tuple[int, int, int]):
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), text, font=font, fill=fill)


def get_os_string() -> str:
    try:
        info = platform.freedesktop_os_release()
        return f"{info.get('NAME', 'Linux')} {info.get('VERSION_ID', '')}".strip()
    except Exception:
        sys_name = platform.system()
        if sys_name == "Darwin":
            return f"macOS {platform.mac_ver()[0]}"
        return f"{sys_name} {platform.release()}"


def get_cpu_temp() -> Optional[float]:
    # 1. Try psutil sensors
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for key in ["coretemp", "cpu_thermal", "cpu-thermal", "k10temp", "zenpower"]:
                if key in temps and temps[key]:
                    return temps[key][0].current
            for name, entries in temps.items():
                if entries:
                    return entries[0].current
    except Exception:
        pass

    # 2. Try sysfs (Linux/Raspberry Pi) directly
    try:
        if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                val = float(f.read().strip())
                return val / 1000.0
    except Exception:
        pass

    return None


def ring(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, width: float, pct: float, color: tuple[int, int, int], label: str, value_text: str, sub_text: str = ""):
    """Draws a premium ring gauge with center value, label, and subtext below."""
    box = [cx - r, cy - r, cx + r, cy + r]
    # Background track
    d.arc(box, 0, 360, fill=TRACK, width=width)
    # Arc segment (starts at -90 deg / top, moves clockwise)
    if pct > 0:
        end = -90 + 360 * min(1.0, pct / 100)
        d.arc(box, -90, end, fill=color, width=width)
    # Text inside
    text_centered(d, cx, cy - 10 * SS, value_text, F_BIG, INK)
    text_centered(d, cx, cy + 16 * SS, label, F_TINY, DIM)
    # Subtext below
    if sub_text:
        text_centered(d, cx, cy + 96 * SS, sub_text, F_TINY, DIM)


# ---- Renderer ----

def render(state: dict) -> Image.Image:
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d = ImageDraw.Draw(img)

    # ---- 1. Header ----
    hostname = socket.gethostname().split(".")[0]
    os_info = get_os_string()
    clock = time.strftime("%H:%M:%S")
    date_str = time.strftime("%a %d %b")

    # Host & OS Info (Left)
    d.text((16 * SS, 12 * SS), hostname, font=F_MED, fill=CYAN)
    d.text((16 * SS, 42 * SS), os_info, font=F_TINY, fill=DIM)

    # Clock & Date (Right)
    d.text((W * SS - 16 * SS, 8 * SS), clock, font=F_CLOCK, fill=INK, anchor="ra")
    d.text((W * SS - 16 * SS, 42 * SS), date_str, font=F_TINY, fill=DIM, anchor="ra")

    # Separator Line
    d.line([(16 * SS, 70 * SS), ((W - 16) * SS, 70 * SS)], fill=TRACK, width=1 * SS)

    # ---- 2. Middle Ring Gauges ----
    cy = 200 * SS
    r = 66 * SS
    w = 10 * SS

    # CPU Ring (Left)
    cpu_pct = state["cpu"]
    temp_str = f"{state['temp']:.1f}°C" if state["temp"] else "--°C"
    ring(d, 200 * SS, cy, r, w, cpu_pct, cpu_color(cpu_pct), "CPU", f"{cpu_pct:.0f}%", temp_str)

    # RAM Ring (Center)
    ram_pct = state["ram"]
    ram_str = f"{state['ram_used']:.1f}/{state['ram_total']:.0f} GB"
    ring(d, 480 * SS, cy, r, w, ram_pct, PURPLE, "RAM", f"{ram_pct:.0f}%", ram_str)

    # Disk Ring (Right)
    disk_pct = state["disk"]
    disk_str = f"{state['disk_used']:.0f}/{state['disk_total']:.0f} GB"
    ring(d, 760 * SS, cy, r, w, disk_pct, GREEN, "DISK", f"{disk_pct:.0f}%", disk_str)

    # ---- 3. Bottom Panels ----
    by0, by1 = 360 * SS, 608 * SS

    # Panel 1: Network Stats (Left)
    nx0, nx1 = 32 * SS, 464 * SS
    d.rounded_rectangle([nx0, by0, nx1, by1], radius=12 * SS, fill=PANEL)
    d.text((nx0 + 16 * SS, by0 + 16 * SS), "NETWORK", font=F_TINY, fill=DIM)

    # Down Arrow and Rate
    d.text((nx0 + 24 * SS, by0 + 72 * SS), "↓", font=F_BIG, fill=GREEN)
    d.text((nx0 + 64 * SS, by0 + 74 * SS), fmt_rate(state["rx"]), font=F_MED, fill=INK)
    d.text((nx0 + 64 * SS, by0 + 110 * SS), "DOWNLOAD", font=F_TINY, fill=DIM)

    # Up Arrow and Rate
    d.text((nx0 + 24 * SS, by0 + 148 * SS), "↑", font=F_BIG, fill=ORANGE)
    d.text((nx0 + 64 * SS, by0 + 150 * SS), fmt_rate(state["tx"]), font=F_MED, fill=INK)
    d.text((nx0 + 64 * SS, by0 + 186 * SS), "UPLOAD", font=F_TINY, fill=DIM)

    # Panel 2: CPU History Sparkline (Right)
    sx0, sx1 = 496 * SS, 928 * SS
    d.rounded_rectangle([sx0, by0, sx1, by1], radius=12 * SS, fill=PANEL)
    d.text((sx0 + 16 * SS, by0 + 16 * SS), "CPU LOAD HISTORY", font=F_TINY, fill=DIM)

    hist = state["cpu_hist"]
    if len(hist) >= 2:
        hx0 = sx0 + 16 * SS
        hy0 = by0 + 64 * SS
        hx1 = sx1 - 16 * SS
        hy1 = by1 - 16 * SS
        plot_w = hx1 - hx0
        plot_h = hy1 - hy0
        n = len(hist)

        pts = []
        for i, val in enumerate(hist):
            x = hx0 + plot_w * i / (n - 1)
            y = hy1 - plot_h * (val / 100)
            pts.append((x, y))

        # Filled area below sparkline
        poly = pts + [(pts[-1][0], hy1), (pts[0][0], hy1)]
        d.polygon(poly, fill=SPARK_FILL)
        # Line plot
        d.line(pts, fill=CYAN, width=2 * SS, joint="curve")

    # Downsample using Lanczos for clean antialiasing
    return img.resize((W, H), Image.Resampling.LANCZOS)


# ---- Core Cycle ----

def collect_state(prev_net, dt: float, cpu_hist: collections.deque) -> tuple[dict, any]:
    cpu = psutil.cpu_percent()
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()

    rx = (net.bytes_recv - prev_net.bytes_recv) / dt if dt > 0 else 0
    tx = (net.bytes_sent - prev_net.bytes_sent) / dt if dt > 0 else 0
    cpu_hist.append(cpu)

    return {
        "cpu": cpu,
        "ram": vm.percent,
        "ram_used": vm.used / 1e9,
        "ram_total": vm.total / 1e9,
        "disk": disk.percent,
        "disk_used": disk.used / 1e9,
        "disk_total": disk.total / 1e9,
        "rx": rx,
        "tx": tx,
        "temp": get_cpu_temp(),
        "cpu_hist": list(cpu_hist),
    }, net


def main() -> int:
    ap = argparse.ArgumentParser(description="Redesigned AX206 System Monitor Dashboard")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between screen updates")
    ap.add_argument("--frames", type=int, default=0, help="exit after N frames (0=run forever)")
    args = ap.parse_args()

    cpu_hist = collections.deque(maxlen=60)  # Fits the plot width perfectly
    psutil.cpu_percent()  # Prime CPU metric
    prev_net = psutil.net_io_counters()
    last = time.time()

    with AX206Display() as s:
        print(f"sysdash running ({s.width}x{s.height}) in modern dark layout, Ctrl-C to stop")
        count = 0
        glitches = 0
        consec = 0
        MAX_CONSEC = 6

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

                if consec <= 2:
                    s.recover()
                else:
                    print("  attempting full reopen…", flush=True)
                    ok = s.reopen()
                    print(f"  reopen {'OK' if ok else 'FAILED'}", flush=True)

                if consec >= MAX_CONSEC:
                    print("Too many consecutive failures — display needs physical replug. Exiting.", flush=True)
                    return 1
                continue

            consec = 0
            push_ms = (time.time() - t0) * 1000
            count += 1

            if count == 1 or count % 10 == 0:
                print(f"frame {count}: cpu {state['cpu']:.0f}% ram {state['ram']:.0f}% disk {state['disk']:.0f}% "
                      f"render+push {push_ms:.0f}ms (glitches {glitches})", flush=True)

            if args.frames and count >= args.frames:
                break
    return 0


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
