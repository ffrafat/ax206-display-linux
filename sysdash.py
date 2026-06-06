#!/usr/bin/env python3
"""Linux-focused system-monitor dashboard for the AX206 SmartCool USB screen.

Renders a highly readable, compact, dark card-based dashboard:
  - Header: Procedural Debian swirl logo, hostname, local IP, OS distro info, and thin clock.
  - CPU Card (Middle-Left): CPU Temp gauge, Load (%), and Frequency.
  - RAM Card (Middle-Right): RAM Usage gauge, Used, and Free details.
  - Storage & Network Cards (Bottom): Disk partition usage (/ and /boot) and Network throughput speeds.
"""
from __future__ import annotations

import argparse
import os
import platform
import socket
import time
from typing import Optional

import psutil
from PIL import Image, ImageDraw, ImageFont

from ax206 import AX206Display, to_rgb565_be

W, H = 480, 320
SS = 2  # Supersample factor

# ---- Design Palette ----
BG        = (0, 0, 0)         # Pitch Black background
PANEL     = (16, 16, 16)      # Slate Dark panels
INK       = (255, 255, 255)    # Solid White text
DIM       = (150, 150, 150)    # Medium Gray text
TRACK     = (40, 40, 40)       # Dark Gray progress tracks
CYAN      = (0, 229, 255)      # Cyberpunk Cyan
GREEN     = (0, 230, 118)      # Down speed Green
ORANGE    = (255, 145, 0)      # Up speed Orange
RED       = (215, 25, 65)      # Debian Red
PURPLE    = (188, 0, 255)      # RAM Purple

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


# Pre-load scaled fonts
F_CLOCK = _font(34 * SS, light=True)
F_BIG   = _font(28 * SS, bold=True)
F_MED   = _font(16 * SS, bold=True)
F_SMALL = _font(13 * SS, bold=True)
F_TINY  = _font(11 * SS)


# ---- Helper Functions ----

def get_cpu_model() -> str:
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


def get_ip_address() -> str:
    """Find the primary local IP address of the system."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Connect to an external host (does not send actual packets)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_os_string() -> str:
    try:
        info = platform.freedesktop_os_release()
        return f"{info.get('NAME', 'Linux')} {info.get('VERSION_ID', '')}".strip()
    except Exception:
        sys_name = platform.system()
        if sys_name == "Darwin":
            return f"macOS {platform.mac_ver()[0]}"
        return f"{sys_name} {platform.release()}"


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


def draw_debian_logo(d: ImageDraw.ImageDraw, x: float, y: float):
    """Draws a stylized Debian Red spiral swirl icon."""
    # Outer swirl loop
    d.arc([x, y, x + 24 * SS, y + 24 * SS], 0, 270, fill=RED, width=int(3 * SS))
    # Inner swirl loop
    d.arc([x + 5 * SS, y + 5 * SS, x + 19 * SS, y + 19 * SS], 90, 360, fill=RED, width=int(2 * SS))


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
    """Draws a bold horizontal progress bar."""
    d.rectangle([x0, y0, x1, y1], fill=TRACK)
    if pct > 0:
        fill_w = (x1 - x0) * min(1.0, pct / 100)
        d.rectangle([x0, y0, x0 + fill_w, y1], fill=fill_color)


# ---- Renderer ----

def render(state: dict) -> Image.Image:
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d = ImageDraw.Draw(img)

    # ---- 1. Top Header ----
    # Debian Logo & Host / IP Info (Left)
    draw_debian_logo(d, 20 * SS, 12 * SS)
    hostname = socket.gethostname().split(".")[0].upper()
    d.text((54 * SS, 10 * SS), hostname, font=F_MED, fill=INK)
    d.text((54 * SS, 30 * SS), f"IP: {state['ip']}", font=F_TINY, fill=DIM)

    # OS Info (Center-aligned to prevent overlap)
    os_info = get_os_string()
    text_centered(d, 480 * SS, 26 * SS, os_info, F_TINY, DIM)

    # Clock (Right - ONLY Clock, Date & Load removed to clean area)
    clock_str = time.strftime("%I:%M %p").lstrip("0")
    d.text((W * SS - 20 * SS, 16 * SS), clock_str, font=F_CLOCK, fill=INK, anchor="ra")

    # Separator Line
    d.line([(20 * SS, 50 * SS), ((W - 20) * SS, 50 * SS)], fill=TRACK, width=2 * SS)

    # ---- 2. Middle Panels (CPU and RAM) ----
    my0, my1 = 55 * SS, 195 * SS

    # CPU Panel (Left)
    cx0, cx1 = 20 * SS, 230 * SS
    d.rounded_rectangle([cx0, my0, cx1, my1], radius=6 * SS, fill=PANEL)
    
    # Title & CPU model
    d.text((cx0 + 12 * SS, my0 + 10 * SS), "CPU TEMP / SPEED", font=F_TINY, fill=DIM)
    cpu_model = get_cpu_model()
    clean_cpu = cpu_model.replace("(R)", "").replace("(TM)", "").replace("CPU", "").strip()
    if len(clean_cpu) > 20:
        clean_cpu = clean_cpu[:18] + "..."
    d.text((cx0 + 12 * SS, my0 + 25 * SS), clean_cpu, font=F_TINY, fill=INK)

    # CPU Arc Gauge (Displays Temperature inside)
    gauge_cx = cx0 + 52 * SS
    gauge_cy = my0 + 83 * SS
    cpu_pct = state["cpu"]
    temp_val = state["temp"]
    temp_str = f"{temp_val:.0f}°C" if temp_val else f"{cpu_pct:.0f}%"
    temp_color = RED if (temp_val and temp_val > 70) else CYAN
    gauge_arc(d, gauge_cx, gauge_cy, 42 * SS, 5 * SS, temp_val if temp_val else cpu_pct, temp_color, temp_str)
    # Gauge label underneathcircle
    text_centered(d, gauge_cx, gauge_cy + 42 * SS + 16 * SS, "CPU TEMP", F_TINY, DIM)

    # CPU side progress bars (LOAD and FREQ)
    bx0, bx1 = cx0 + 104 * SS, cx1 - 12 * SS
    
    # CPU Load Progress (Thick)
    by = my0 + 55 * SS
    d.text((bx0, by - 12 * SS), "CPU Load", font=F_TINY, fill=DIM)
    d.text((bx1, by - 12 * SS), f"{cpu_pct:.0f}%", font=F_TINY, fill=INK, anchor="ra")
    progress_bar(d, bx0, by, bx1, by + 10 * SS, cpu_pct, CYAN)

    # CPU Freq Progress (Thick)
    by = my0 + 100 * SS
    d.text((bx0, by - 12 * SS), "Frequency", font=F_TINY, fill=DIM)
    d.text((bx1, by - 12 * SS), f"{state['cpu_freq']:.1f} GHz", font=F_TINY, fill=INK, anchor="ra")
    freq_pct = (state["cpu_freq"] / state["cpu_max_freq"] * 100) if state["cpu_max_freq"] > 0 else 50
    progress_bar(d, bx0, by, bx1, by + 10 * SS, freq_pct, CYAN)


    # RAM Panel (Right)
    cx0, cx1 = 250 * SS, 460 * SS
    d.rounded_rectangle([cx0, my0, cx1, my1], radius=6 * SS, fill=PANEL)

    # Title & RAM Details
    d.text((cx0 + 12 * SS, my0 + 10 * SS), "RAM UTILIZATION", font=F_TINY, fill=DIM)
    d.text((cx0 + 12 * SS, my0 + 25 * SS), f"{state['ram_total']:.1f} GB Total", font=F_TINY, fill=INK)

    # RAM Arc Gauge
    gauge_cx = cx0 + 52 * SS
    ram_pct = state["ram"]
    gauge_arc(d, gauge_cx, gauge_cy, 42 * SS, 5 * SS, ram_pct, PURPLE, f"{ram_pct:.0f}%")
    text_centered(d, gauge_cx, gauge_cy + 42 * SS + 16 * SS, "RAM", F_TINY, DIM)

    # RAM side progress bars (USED and FREE)
    bx0, bx1 = cx0 + 104 * SS, cx1 - 12 * SS
    
    # Used RAM Progress (Thick)
    by = my0 + 55 * SS
    d.text((bx0, by - 12 * SS), "Used Mem", font=F_TINY, fill=DIM)
    d.text((bx1, by - 12 * SS), f"{state['ram_used']:.1f} GB", font=F_TINY, fill=INK, anchor="ra")
    progress_bar(d, bx0, by, bx1, by + 10 * SS, ram_pct, PURPLE)

    # Free RAM Progress (Thick)
    by = my0 + 100 * SS
    d.text((bx0, by - 12 * SS), "Free Mem", font=F_TINY, fill=DIM)
    d.text((bx1, by - 12 * SS), f"{state['ram_free']:.1f} GB", font=F_TINY, fill=INK, anchor="ra")
    progress_bar(d, bx0, by, bx1, by + 10 * SS, 100 - ram_pct, PURPLE)


    # ---- 3. Bottom Panels (Storage & Network) ----
    by0, by1 = 205 * SS, 305 * SS

    # Bottom Left: Storage (Root & Boot)
    bx0, bx1 = 20 * SS, 230 * SS
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=6 * SS, fill=PANEL)
    d.text((bx0 + 12 * SS, by0 + 10 * SS), "STORAGE", font=F_TINY, fill=DIM)
    
    # Root /
    root_pct = state["disk_root_pct"]
    d.text((bx0 + 12 * SS, by0 + 25 * SS), f"/: {root_pct:.0f}%", font=F_TINY, fill=INK)
    progress_bar(d, bx0 + 12 * SS, by0 + 40 * SS, bx1 - 12 * SS, by0 + 48 * SS, root_pct, INK)

    # Boot /boot
    boot_pct = state["disk_boot_pct"]
    d.text((bx0 + 12 * SS, by0 + 57 * SS), f"boot: {boot_pct:.0f}%", font=F_TINY, fill=INK)
    progress_bar(d, bx0 + 12 * SS, by0 + 72 * SS, bx1 - 12 * SS, by0 + 80 * SS, boot_pct, INK)


    # Bottom Right: Network rates
    bx0, bx1 = 250 * SS, 460 * SS
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=6 * SS, fill=PANEL)
    d.text((bx0 + 12 * SS, by0 + 10 * SS), "NETWORK THROUGHPUT", font=F_TINY, fill=DIM)
    
    # Download Rate
    d.text((bx0 + 12 * SS, by0 + 30 * SS), f"↓ {fmt_rate(state['rx'])}", font=F_MED, fill=GREEN)
    
    # Upload Rate
    d.text((bx0 + 12 * SS, by0 + 60 * SS), f"↑ {fmt_rate(state['tx'])}", font=F_MED, fill=ORANGE)

    # Downsample using Lanczos for clean antialiasing
    return img.resize((W, H), Image.Resampling.LANCZOS)


# ---- Core Cycle ----

def collect_state(prev_net, dt: float) -> tuple[dict, any]:
    cpu = psutil.cpu_percent()
    vm = psutil.virtual_memory()
    net = psutil.net_io_counters()

    # CPU Freq
    try:
        freq = psutil.cpu_freq()
        cpu_freq = freq.current / 1000.0 if freq else 2.5
        cpu_max_freq = freq.max / 1000.0 if freq else 4.0
    except Exception:
        cpu_freq, cpu_max_freq = 2.5, 4.0

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

    return {
        "cpu": cpu,
        "cpu_freq": cpu_freq,
        "cpu_max_freq": cpu_max_freq,
        "ram": vm.percent,
        "ram_used": vm.used / 1e9,
        "ram_free": vm.available / 1e9,
        "ram_total": vm.total / 1e9,
        "disk_root_pct": disk_root_pct,
        "disk_boot_pct": disk_boot_pct,
        "rx": rx,
        "tx": tx,
        "temp": get_cpu_temp(),
        "ip": get_ip_address(),
    }, net


def main() -> int:
    ap = argparse.ArgumentParser(description="Descriptive highly readable Linux Dashboard")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between screen updates")
    ap.add_argument("--frames", type=int, default=0, help="exit after N frames (0=run forever)")
    args = ap.parse_args()

    psutil.cpu_percent()  # Prime CPU metric
    prev_net = psutil.net_io_counters()
    last = time.time()

    with AX206Display() as s:
        print(f"sysdash running ({s.width}x{s.height}) in compact AIDA64 style, Ctrl-C to stop")
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
