#!/usr/bin/env python3
"""Linux-focused system-monitor dashboard in AIDA64 style for the AX206 SmartCool USB screen.

Renders a 480x320 black dashboard matching the user's design:
  - Header: Procedural gears logo, system load, and thin clock.
  - Two Main Panels: CPU (with temp gauge, load, speed, processes) and MEM (with usage gauge, used, free, swap).
  - Three Bottom Panels: RAM (used/free), Disk Storage (/ and /boot), and Network I/O.
"""
from __future__ import annotations

import argparse
import collections
import math
import os
import platform
import socket
import time
from typing import Optional

import psutil
from PIL import Image, ImageDraw, ImageFont

from ax206 import AX206Display, to_rgb565_be

W, H = 480, 320
SS = 2  # Supersample factor for crisp rendering

# ---- AIDA64 Design Palette ----
BG        = (0, 0, 0)         # Pitch Black background
PANEL     = (16, 16, 16)      # Slate Dark panels
INK       = (255, 255, 255)    # Solid White text
DIM       = (150, 150, 150)    # Medium Gray text
TRACK     = (40, 40, 40)       # Dark Gray track/empty bars
CYAN      = (0, 229, 255)      # Cyberpunk Accent
GREEN     = (0, 230, 118)      # Down speed Green
ORANGE    = (255, 145, 0)      # Up speed Orange
RED       = (255, 23, 68)      # Hot alert Red

# ---- Font Loading ----
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_LIGHT_PATH = os.path.join(SCRIPT_DIR, "assets", "fonts", "Inter-Light.ttf")
FONT_REGULAR_PATH = os.path.join(SCRIPT_DIR, "assets", "fonts", "Inter-Regular.ttf")
FONT_SEMIBOLD_PATH = os.path.join(SCRIPT_DIR, "assets", "fonts", "Inter-SemiBold.ttf")


def _font(size: int, bold: bool = False, light: bool = False) -> ImageFont.FreeTypeFont:
    if light:
        path = FONT_LIGHT_PATH
    elif bold:
        path = FONT_SEMIBOLD_PATH
    else:
        path = FONT_REGULAR_PATH
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


# Pre-load fonts
F_CLOCK = _font(34 * SS, light=True)
F_BIG   = _font(28 * SS, bold=True)
F_MED   = _font(15 * SS, bold=True)
F_SMALL = _font(12 * SS, bold=True)
F_TINY  = _font(10 * SS)


# ---- Helper Functions ----

def get_cpu_model() -> str:
    """Read CPU model name on Linux/macOS."""
    try:
        if platform.system() == "Linux":
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":")[1].strip()
        elif platform.system() == "Darwin":
            import subprocess
            return subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"]).decode().strip()
    except Exception:
        pass
    return platform.processor() or "CPU"


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


def get_cpu_temp() -> Optional[float]:
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
    try:
        if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                val = float(f.read().strip())
                return val / 1000.0
    except Exception:
        pass
    return None


def draw_gears(d: ImageDraw.ImageDraw, x: float, y: float):
    """Draw two overlapping procedural gear outlines in the logo header."""
    # Gear 1 (large)
    cx1, cy1 = x, y
    r1 = 15 * SS
    d.ellipse([cx1 - r1, cy1 - r1, cx1 + r1, cy1 + r1], outline=INK, width=2 * SS)
    d.ellipse([cx1 - 6 * SS, cy1 - 6 * SS, cx1 + 6 * SS, cy1 + 6 * SS], outline=INK, width=2 * SS)
    for i in range(8):
        angle = i * (math.pi / 4)
        x0 = cx1 + (r1 - 1 * SS) * math.cos(angle)
        y0 = cy1 + (r1 - 1 * SS) * math.sin(angle)
        x1 = cx1 + (r1 + 3 * SS) * math.cos(angle)
        y1 = cy1 + (r1 + 3 * SS) * math.sin(angle)
        d.line([x0, y0, x1, y1], fill=INK, width=3 * SS)

    # Gear 2 (small)
    cx2, cy2 = x + 23 * SS, y + 10 * SS
    r2 = 10 * SS
    d.ellipse([cx2 - r2, cy2 - r2, cx2 + r2, cy2 + r2], outline=INK, width=2 * SS)
    d.ellipse([cx2 - 4 * SS, cy2 - 4 * SS, cx2 + 4 * SS, cy2 + 4 * SS], outline=INK, width=2 * SS)
    for i in range(6):
        angle = i * (math.pi / 3) + 0.3
        x0 = cx2 + (r2 - 1 * SS) * math.cos(angle)
        y0 = cy2 + (r2 - 1 * SS) * math.sin(angle)
        x1 = cx2 + (r2 + 2 * SS) * math.cos(angle)
        y1 = cy2 + (r2 + 2 * SS) * math.sin(angle)
        d.line([x0, y0, x1, y1], fill=INK, width=int(2.5 * SS))


def gauge_arc(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, width: float, pct: float, color: tuple[int, int, int], value_text: str):
    """Draws a 270-degree tachometer-style arc from -225 deg (bottom-left) to 45 deg (bottom-right)."""
    box = [cx - r, cy - r, cx + r, cy + r]
    # Draw track
    d.arc(box, -225, 45, fill=TRACK, width=width)
    # Draw active fill
    if pct > 0:
        sweep_end = -225 + 270 * min(1.0, pct / 100)
        d.arc(box, -225, sweep_end, fill=color, width=width)
    # Value in center
    text_centered(d, cx, cy, value_text, F_BIG, INK)


def progress_bar(d: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, pct: float, fill_color: tuple[int, int, int]):
    """Draws a horizontal progress bar."""
    d.rectangle([x0, y0, x1, y1], fill=TRACK)
    if pct > 0:
        fill_w = (x1 - x0) * min(1.0, pct / 100)
        d.rectangle([x0, y0, x0 + fill_w, y1], fill=fill_color)


# ---- Renderer ----

def render(state: dict) -> Image.Image:
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d = ImageDraw.Draw(img)

    # ---- 1. Top Header ----
    # Gears Logo (Left)
    draw_gears(d, 52 * SS, 36 * SS)
    d.text((86 * SS, 20 * SS), "AIDA64", font=F_MED, fill=INK)
    d.text((86 * SS, 46 * SS), "by finalwire", font=F_TINY, fill=DIM)

    # Middle Stat: CPU Load Average
    load_str = f"{state['load'][0]:.2f} LOAD"
    text_centered(d, 480 * SS, 38 * SS, load_str, F_MED, INK)

    # Clock (Right)
    clock_str = time.strftime("%I:%M %p").lstrip("0")
    d.text((W * SS - 40 * SS, 20 * SS), clock_str, font=F_CLOCK, fill=INK, anchor="ra")

    # ---- 2. Middle Panels (Two Cards) ----
    y0, y1 = 100 * SS, 440 * SS

    # CPU Panel (Left)
    cx0, cx1 = 40 * SS, 480 * SS
    d.rectangle([cx0, y0, cx1, y1], fill=PANEL)
    # CPU Model title
    cpu_model = get_cpu_model()
    # Truncate if too long to fit
    if len(cpu_model) > 28:
        cpu_model = cpu_model[:25] + "..."
    d.text((cx0 + 24 * SS, y0 + 20 * SS), f"CPU  {cpu_model}", font=F_MED, fill=INK)

    # CPU Temp gauge
    gauge_cx = cx0 + 110 * SS
    gauge_cy = y0 + 180 * SS
    cpu_pct = state["cpu"]
    temp_val = state["temp"]
    temp_str = f"{temp_val:.0f}°C" if temp_val else f"{cpu_pct:.0f}%"
    temp_color = RED if (temp_val and temp_val > 70) else INK
    gauge_arc(d, gauge_cx, gauge_cy, 65 * SS, 6 * SS, temp_val if temp_val else cpu_pct, temp_color, temp_str)

    # CPU side progress bars
    bx0, bx1 = cx0 + 220 * SS, cx1 - 24 * SS
    # CPU Load Bar
    by = y0 + 110 * SS
    d.text((bx0, by - 12 * SS), "CPU Load", font=F_TINY, fill=DIM)
    d.text((bx1, by - 12 * SS), f"{cpu_pct:.0f}%", font=F_TINY, fill=INK, anchor="ra")
    progress_bar(d, bx0, by, bx1, by + 6 * SS, cpu_pct, INK)

    # CPU Frequency Bar
    by = y0 + 180 * SS
    d.text((bx0, by - 12 * SS), "Frequency", font=F_TINY, fill=DIM)
    d.text((bx1, by - 12 * SS), f"{state['cpu_freq']:.2f} GHz", font=F_TINY, fill=INK, anchor="ra")
    freq_pct = (state["cpu_freq"] / state["cpu_max_freq"] * 100) if state["cpu_max_freq"] > 0 else 50
    progress_bar(d, bx0, by, bx1, by + 6 * SS, freq_pct, INK)

    # Process Count Bar
    by = y0 + 250 * SS
    d.text((bx0, by - 12 * SS), "Processes", font=F_TINY, fill=DIM)
    d.text((bx1, by - 12 * SS), f"{state['procs']} PROCS", font=F_TINY, fill=INK, anchor="ra")
    procs_pct = (state["procs"] / 500 * 100)
    progress_bar(d, bx0, by, bx1, by + 6 * SS, procs_pct, INK)


    # Memory/Swap Panel (Right)
    cx0, cx1 = 520 * SS, 960 * SS
    d.rectangle([cx0, y0, cx1, y1], fill=PANEL)
    # Title
    d.text((cx0 + 24 * SS, y0 + 20 * SS), f"MEM  DDR SYSTEM MEMORY", font=F_MED, fill=INK)

    # RAM gauge
    gauge_cx = cx0 + 110 * SS
    ram_pct = state["ram"]
    gauge_arc(d, gauge_cx, gauge_cy, 65 * SS, 6 * SS, ram_pct, INK, f"{ram_pct:.0f}%")

    # RAM side progress bars
    bx0, bx1 = cx0 + 220 * SS, cx1 - 24 * SS
    # Used RAM Bar
    by = y0 + 110 * SS
    d.text((bx0, by - 12 * SS), "Used Memory", font=F_TINY, fill=DIM)
    d.text((bx1, by - 12 * SS), f"{state['ram_used']:.1f} GB", font=F_TINY, fill=INK, anchor="ra")
    progress_bar(d, bx0, by, bx1, by + 6 * SS, ram_pct, INK)

    # Free RAM Bar
    by = y0 + 180 * SS
    d.text((bx0, by - 12 * SS), "Free Memory", font=F_TINY, fill=DIM)
    d.text((bx1, by - 12 * SS), f"{state['ram_free']:.1f} GB", font=F_TINY, fill=INK, anchor="ra")
    progress_bar(d, bx0, by, bx1, by + 6 * SS, 100 - ram_pct, INK)

    # Swap memory bar
    by = y0 + 250 * SS
    d.text((bx0, by - 12 * SS), "Swap Space", font=F_TINY, fill=DIM)
    d.text((bx1, by - 12 * SS), f"{state['swap_used']:.1f} GB", font=F_TINY, fill=INK, anchor="ra")
    swap_pct = state["swap_pct"]
    progress_bar(d, bx0, by, bx1, by + 6 * SS, swap_pct, INK)

    # ---- 3. Bottom Panels (Three Cards) ----
    by0, by1 = 460 * SS, 620 * SS

    # Bottom Left: RAM Used details
    bx0, bx1 = 40 * SS, 320 * SS
    d.rectangle([bx0, by0, bx1, by1], fill=PANEL)
    d.text((bx0 + 16 * SS, by0 + 14 * SS), "RAM SYSTEM MEMORY", font=F_SMALL, fill=INK)
    progress_bar(d, bx0 + 16 * SS, by0 + 52 * SS, bx1 - 16 * SS, by0 + 58 * SS, ram_pct, INK)
    d.text((bx0 + 16 * SS, by0 + 78 * SS), f"U: {state['ram_used']:.1f} GB", font=F_TINY, fill=DIM)
    d.text((bx0 + 16 * SS, by0 + 108 * SS), f"F: {state['ram_free']:.1f} GB", font=F_TINY, fill=DIM)
    # Large percentage value
    d.text((bx1 - 16 * SS, by0 + 94 * SS), f"{ram_pct:.0f}%", font=F_BIG, fill=INK, anchor="ra")

    # Bottom Center: SSD Storage details
    bx0, bx1 = 360 * SS, 640 * SS
    d.rectangle([bx0, by0, bx1, by1], fill=PANEL)
    d.text((bx0 + 16 * SS, by0 + 14 * SS), "SSD STORAGE SENSORS", font=F_SMALL, fill=INK)
    
    # Root partition
    d.text((bx0 + 16 * SS, by0 + 50 * SS), "/:", font=F_TINY, fill=DIM)
    progress_bar(d, bx0 + 48 * SS, by0 + 50 * SS, bx1 - 64 * SS, by0 + 56 * SS, state["disk_root_pct"], INK)
    d.text((bx1 - 16 * SS, by0 + 50 * SS), f"{state['disk_root_pct']:.0f}%", font=F_TINY, fill=INK, anchor="ra")

    # Boot/Swap/Var partition
    d.text((bx0 + 16 * SS, by0 + 95 * SS), "boot:", font=F_TINY, fill=DIM)
    progress_bar(d, bx0 + 48 * SS, by0 + 95 * SS, bx1 - 64 * SS, by0 + 101 * SS, state["disk_boot_pct"], INK)
    d.text((bx1 - 16 * SS, by0 + 95 * SS), f"{state['disk_boot_pct']:.0f}%", font=F_TINY, fill=INK, anchor="ra")

    # Bottom Right: Network rates
    bx0, bx1 = 680 * SS, 960 * SS
    d.rectangle([bx0, by0, bx1, by1], fill=PANEL)
    d.text((bx0 + 16 * SS, by0 + 14 * SS), "NET Gigabit Ethernet", font=F_SMALL, fill=INK)
    # Draw activity load bar (scaled relative to 10 MB/s limit)
    net_load = min(100.0, (state["rx"] + state["tx"]) / 10e6 * 100.0)
    progress_bar(d, bx0 + 16 * SS, by0 + 52 * SS, bx1 - 16 * SS, by0 + 58 * SS, net_load, INK)
    d.text((bx0 + 16 * SS, by0 + 78 * SS), f"D: {fmt_rate(state['rx'])}", font=F_TINY, fill=GREEN)
    d.text((bx0 + 16 * SS, by0 + 108 * SS), f"U: {fmt_rate(state['tx'])}", font=F_TINY, fill=ORANGE)
    # Display speed label dynamically on the right
    net_val = fmt_rate(state["rx"] + state["tx"])
    # Simplify label to fit (e.g. 2.4M or 124K)
    simple_net = net_val.replace(" B/s", "B").replace(" KB/s", "K").replace(" MB/s", "M")
    d.text((bx1 - 16 * SS, by0 + 94 * SS), simple_net, font=F_BIG, fill=INK, anchor="ra")

    # Downsample using Lanczos for clean antialiasing
    return img.resize((W, H), Image.Resampling.LANCZOS)


# ---- Core Cycle ----

def collect_state(prev_net, dt: float) -> tuple[dict, any]:
    cpu = psutil.cpu_percent()
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    net = psutil.net_io_counters()

    # CPU Freq
    try:
        freq = psutil.cpu_freq()
        cpu_freq = freq.current / 1000.0 if freq else 2.5
        cpu_max_freq = freq.max / 1000.0 if freq else 4.0
    except Exception:
        cpu_freq, cpu_max_freq = 2.5, 4.0

    # Processes count
    try:
        procs = len(psutil.pids())
    except Exception:
        procs = 120

    # Disk partitions
    try:
        disk_root = psutil.disk_usage("/")
        disk_root_pct = disk_root.percent
    except Exception:
        disk_root_pct = 50.0

    try:
        disk_boot = psutil.disk_usage("/boot")
        disk_boot_pct = disk_boot.percent
    except Exception:
        disk_boot_pct = disk_root_pct

    rx = (net.bytes_recv - prev_net.bytes_recv) / dt if dt > 0 else 0
    tx = (net.bytes_sent - prev_net.bytes_sent) / dt if dt > 0 else 0

    try:
        load = psutil.getloadavg()
    except Exception:
        load = (0.0, 0.0, 0.0)

    return {
        "cpu": cpu,
        "cpu_freq": cpu_freq,
        "cpu_max_freq": cpu_max_freq,
        "procs": procs,
        "ram": vm.percent,
        "ram_used": vm.used / 1e9,
        "ram_free": vm.available / 1e9,
        "ram_total": vm.total / 1e9,
        "swap_used": swap.used / 1e9,
        "swap_pct": swap.percent,
        "disk_root_pct": disk_root_pct,
        "disk_boot_pct": disk_boot_pct,
        "rx": rx,
        "tx": tx,
        "temp": get_cpu_temp(),
        "load": load,
    }, net


def main() -> int:
    ap = argparse.ArgumentParser(description="AIDA64 styling layout")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between screen updates")
    ap.add_argument("--frames", type=int, default=0, help="exit after N frames (0=run forever)")
    args = ap.parse_args()

    psutil.cpu_percent()  # Prime CPU metric
    prev_net = psutil.net_io_counters()
    last = time.time()

    with AX206Display() as s:
        print(f"sysdash running ({s.width}x{s.height}) in AIDA64 style, Ctrl-C to stop")
        count = 0
        glitches = 0
        consec = 0
        MAX_CONSEC = 6

        while True:
            time.sleep(args.interval)
            now = time.time()
            dt = now - last
            last = now

            state, prev_net = collect_state(prev_net, dt)
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
                print(f"frame {count}: cpu {state['cpu']:.0f}% ram {state['ram']:.0f}% root {state['disk_root_pct']:.0f}% "
                      f"render+push {push_ms:.0f}ms (glitches {glitches})", flush=True)

            if args.frames and count >= args.frames:
                break
    return 0


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
