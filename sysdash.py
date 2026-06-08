#!/usr/bin/env python3
"""Cross-platform system-monitor dashboard for the AX206 SmartCool USB screen.

Renders a compact, dark card-based dashboard:
  - Header: OS-aware logo, hostname, IP + OS, clock.
  - CPU Card: temp or load arc gauge, load %, and frequency bars.
  - RAM Card: usage arc gauge, used/free bars.
  - Storage & Network Cards: unified bar layout with threshold colors.
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

from ax206 import AX206Display

W, H = 480, 320
SS = 2  # Supersample factor
TEMP_MAX = 100.0  # °C mapped to a full gauge arc
NET_BAR_MAX = 100 * 1e6  # bytes/s for a full network bar (100 MB/s)
BAR_LABEL_GAP = 14 * SS

# ---- Design Palette ----
BG        = (0, 0, 0)
PANEL     = (16, 16, 16)
INK       = (255, 255, 255)
DIM       = (150, 150, 150)
TRACK     = (40, 40, 40)
CYAN      = (0, 229, 255)
GREEN     = (0, 230, 118)
ORANGE    = (255, 145, 0)
RED       = (215, 25, 65)
PURPLE    = (188, 0, 255)

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


F_CLOCK = _font(34 * SS, light=True)
F_BIG   = _font(28 * SS, bold=True)
F_MED   = _font(16 * SS, bold=True)
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
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
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


def is_debian() -> bool:
    try:
        info = platform.freedesktop_os_release()
        os_id = info.get("ID", "").lower()
        id_like = info.get("ID_LIKE", "").lower()
        return os_id == "debian" or "debian" in id_like.split()
    except Exception:
        return False


def fmt_rate(bytes_per_s: float) -> str:
    if bytes_per_s >= 1e6:
        return f"{bytes_per_s / 1e6:.1f} MB/s"
    if bytes_per_s >= 1e3:
        return f"{bytes_per_s / 1e3:.0f} KB/s"
    return f"{bytes_per_s:.0f} B/s"


def truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def usage_color(pct: float) -> tuple[int, int, int]:
    if pct >= 85:
        return RED
    if pct >= 70:
        return ORANGE
    return CYAN


def text_centered(d: ImageDraw.ImageDraw, cx: float, cy: float, text: str,
                  font: ImageFont.FreeTypeFont, fill: tuple[int, int, int]) -> None:
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
            for entries in temps.values():
                if entries:
                    return entries[0].current
    except Exception:
        pass
    try:
        if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                return float(f.read().strip()) / 1000.0
    except Exception:
        pass
    return None


def get_secondary_storage() -> tuple[Optional[str], Optional[float]]:
    if platform.system() == "Darwin":
        try:
            du = psutil.disk_usage("/System/Volumes/Data")
            return "Data", du.percent
        except Exception:
            return None, None
    try:
        du = psutil.disk_usage("/boot")
        return "/boot", du.percent
    except Exception:
        return None, None


def draw_debian_logo(d: ImageDraw.ImageDraw, x: float, y: float) -> None:
    d.arc([x, y, x + 24 * SS, y + 24 * SS], 0, 270, fill=RED, width=int(3 * SS))
    d.arc([x + 5 * SS, y + 5 * SS, x + 19 * SS, y + 19 * SS], 90, 360, fill=RED, width=int(2 * SS))


def draw_mac_logo(d: ImageDraw.ImageDraw, x: float, y: float) -> None:
    s = 24 * SS
    d.rounded_rectangle([x, y, x + s, y + s], radius=int(5 * SS), fill=(190, 190, 190))
    bite = int(7 * SS)
    d.ellipse([x + s - bite, y - int(2 * SS), x + s + bite, y + bite], fill=BG)


def draw_generic_logo(d: ImageDraw.ImageDraw, x: float, y: float) -> None:
    s = 24 * SS
    d.rounded_rectangle([x + 2 * SS, y + 2 * SS, x + s - 2 * SS, y + int(16 * SS)],
                        radius=int(2 * SS), outline=CYAN, width=int(2 * SS))
    d.line([x + int(8 * SS), y + int(18 * SS), x + int(8 * SS), y + s - 2 * SS], fill=CYAN, width=int(2 * SS))
    d.line([x + int(16 * SS), y + int(18 * SS), x + int(16 * SS), y + s - 2 * SS], fill=CYAN, width=int(2 * SS))


def draw_header_logo(d: ImageDraw.ImageDraw, x: float, y: float) -> None:
    if platform.system() == "Darwin":
        draw_mac_logo(d, x, y)
    elif is_debian():
        draw_debian_logo(d, x, y)
    else:
        draw_generic_logo(d, x, y)


def gauge_arc(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, width: float,
              pct: float, color: tuple[int, int, int], value_text: str) -> None:
    box = [cx - r, cy - r, cx + r, cy + r]
    d.arc(box, -225, 45, fill=TRACK, width=width)
    if pct > 0:
        sweep_end = -225 + 270 * min(1.0, pct / 100)
        d.arc(box, -225, sweep_end, fill=color, width=width)
    text_centered(d, cx, cy, value_text, F_BIG, INK)


def progress_bar(d: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float,
                 pct: float, fill_color: tuple[int, int, int]) -> None:
    d.rectangle([x0, y0, x1, y1], fill=TRACK)
    if pct > 0:
        fill_w = (x1 - x0) * min(1.0, pct / 100)
        d.rectangle([x0, y0, x0 + fill_w, y1], fill=fill_color)


def draw_metric_bar(d: ImageDraw.ImageDraw, x0: float, x1: float, y: float,
                    label: str, value: str, pct: float,
                    fill_color: tuple[int, int, int]) -> float:
    """Draw label + value on one row, bar below. Returns y after the bar."""
    value_bbox = d.textbbox((0, 0), value, font=F_TINY)
    value_w = value_bbox[2] - value_bbox[0]
    max_label_w = (x1 - x0) - value_w - 8 * SS
    while label:
        label_bbox = d.textbbox((0, 0), label, font=F_TINY)
        if label_bbox[2] - label_bbox[0] <= max_label_w:
            break
        label = label[:-2] + "…" if len(label) > 2 else label[:-1]
    d.text((x0, y), label, font=F_TINY, fill=DIM)
    d.text((x1, y), value, font=F_TINY, fill=INK, anchor="ra")
    bar_y0 = y + BAR_LABEL_GAP
    bar_y1 = bar_y0 + 10 * SS
    progress_bar(d, x0, bar_y0, x1, bar_y1, pct, fill_color)
    return bar_y1


# ---- Renderer ----

def render(state: dict) -> Image.Image:
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d = ImageDraw.Draw(img)

    # ---- Header ----
    draw_header_logo(d, 20 * SS, 12 * SS)
    hostname = truncate(socket.gethostname().split(".")[0].upper(), 22)
    d.text((54 * SS, 10 * SS), hostname, font=F_MED, fill=INK)
    meta = truncate(f"{state['ip']} · {get_os_string()}", 36)
    d.text((54 * SS, 30 * SS), meta, font=F_TINY, fill=DIM)
    clock_str = time.strftime("%I:%M %p").lstrip("0")
    d.text((W * SS - 20 * SS, 16 * SS), clock_str, font=F_CLOCK, fill=INK, anchor="ra")
    d.line([(20 * SS, 50 * SS), ((W - 20) * SS, 50 * SS)], fill=TRACK, width=2 * SS)

    # ---- Middle Panels ----
    my0, my1 = 55 * SS, 195 * SS

    # CPU Panel
    cx0, cx1 = 20 * SS, 230 * SS
    d.rounded_rectangle([cx0, my0, cx1, my1], radius=6 * SS, fill=PANEL)

    cpu_pct = state["cpu"]
    temp_val = state["temp"]
    cpu_title = "CPU TEMP / SPEED" if temp_val is not None else "CPU LOAD / SPEED"
    d.text((cx0 + 12 * SS, my0 + 10 * SS), cpu_title, font=F_TINY, fill=DIM)

    cpu_model = get_cpu_model().replace("(R)", "").replace("(TM)", "").replace("CPU", "").strip()
    d.text((cx0 + 12 * SS, my0 + 25 * SS), truncate(cpu_model, 20), font=F_TINY, fill=INK)

    gauge_cx = cx0 + 52 * SS
    gauge_cy = my0 + 78 * SS
    if temp_val is not None:
        gauge_text = f"{temp_val:.0f}°C"
        gauge_pct = min(100.0, temp_val / TEMP_MAX * 100)
        gauge_color = RED if temp_val > 70 else CYAN
    else:
        gauge_text = f"{cpu_pct:.0f}%"
        gauge_pct = cpu_pct
        gauge_color = CYAN
    gauge_arc(d, gauge_cx, gauge_cy, 38 * SS, 5 * SS, gauge_pct, gauge_color, gauge_text)

    bx0, bx1 = cx0 + 104 * SS, cx1 - 12 * SS
    bar_y = my0 + 52 * SS
    bar_y = draw_metric_bar(d, bx0, bx1, bar_y, "CPU Load", f"{cpu_pct:.0f}%", cpu_pct, CYAN) + 14 * SS
    freq_pct = (state["cpu_freq"] / state["cpu_max_freq"] * 100) if state["cpu_max_freq"] > 0 else 50
    draw_metric_bar(d, bx0, bx1, bar_y, "Freq", f"{state['cpu_freq']:.1f} GHz", freq_pct, CYAN)

    # RAM Panel
    cx0, cx1 = 250 * SS, 460 * SS
    d.rounded_rectangle([cx0, my0, cx1, my1], radius=6 * SS, fill=PANEL)
    d.text((cx0 + 12 * SS, my0 + 10 * SS), "RAM UTILIZATION", font=F_TINY, fill=DIM)
    d.text((cx0 + 12 * SS, my0 + 25 * SS), f"{state['ram_total']:.1f} GB Total", font=F_TINY, fill=INK)

    gauge_cx = cx0 + 52 * SS
    ram_pct = state["ram"]
    gauge_arc(d, gauge_cx, gauge_cy, 38 * SS, 5 * SS, ram_pct, PURPLE, f"{ram_pct:.0f}%")

    bx0, bx1 = cx0 + 104 * SS, cx1 - 12 * SS
    bar_y = my0 + 52 * SS
    bar_y = draw_metric_bar(d, bx0, bx1, bar_y, "Used", f"{state['ram_used']:.1f} GB", ram_pct, PURPLE) + 14 * SS
    draw_metric_bar(d, bx0, bx1, bar_y, "Free", f"{state['ram_free']:.1f} GB", 100 - ram_pct, PURPLE)

    # ---- Bottom Panels ----
    by0, by1 = 205 * SS, 305 * SS

    bx0, bx1 = 20 * SS, 230 * SS
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=6 * SS, fill=PANEL)
    d.text((bx0 + 12 * SS, by0 + 10 * SS), "STORAGE", font=F_TINY, fill=DIM)

    bar_x0 = bx0 + 12 * SS
    bar_x1 = bx1 - 12 * SS
    root_pct = state["disk_root_pct"]
    bar_y = by0 + 28 * SS
    bar_y = draw_metric_bar(d, bar_x0, bar_x1, bar_y, "/", f"{root_pct:.0f}%", root_pct, usage_color(root_pct))

    sec_label = state.get("disk_sec_label")
    sec_pct = state.get("disk_sec_pct")
    if sec_label and sec_pct is not None and abs(sec_pct - root_pct) > 1:
        draw_metric_bar(d, bar_x0, bar_x1, bar_y + 14 * SS, sec_label,
                        f"{sec_pct:.0f}%", sec_pct, usage_color(sec_pct))

    bx0, bx1 = 250 * SS, 460 * SS
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=6 * SS, fill=PANEL)
    d.text((bx0 + 12 * SS, by0 + 10 * SS), "NETWORK", font=F_TINY, fill=DIM)

    bar_x0 = bx0 + 12 * SS
    bar_x1 = bx1 - 12 * SS
    rx_pct = min(100.0, state["rx"] / NET_BAR_MAX * 100)
    tx_pct = min(100.0, state["tx"] / NET_BAR_MAX * 100)
    bar_y = by0 + 28 * SS
    bar_y = draw_metric_bar(d, bar_x0, bar_x1, bar_y, "Download", fmt_rate(state["rx"]), rx_pct, GREEN) + 14 * SS
    draw_metric_bar(d, bar_x0, bar_x1, bar_y, "Upload", fmt_rate(state["tx"]), tx_pct, ORANGE)

    return img.resize((W, H), Image.Resampling.LANCZOS)


# ---- Core Cycle ----

def collect_state(prev_net, dt: float) -> tuple[dict, any]:
    cpu = psutil.cpu_percent()
    vm = psutil.virtual_memory()
    net = psutil.net_io_counters()

    try:
        freq = psutil.cpu_freq()
        cpu_freq = freq.current / 1000.0 if freq else 2.5
        cpu_max_freq = freq.max / 1000.0 if freq else 4.0
    except Exception:
        cpu_freq, cpu_max_freq = 2.5, 4.0

    try:
        disk_root_pct = psutil.disk_usage("/").percent
    except Exception:
        disk_root_pct = 0.0

    sec_label, sec_pct = get_secondary_storage()

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
        "disk_sec_label": sec_label,
        "disk_sec_pct": sec_pct,
        "rx": rx,
        "tx": tx,
        "temp": get_cpu_temp(),
        "ip": get_ip_address(),
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
                print(f"frame {count}: cpu {state['cpu']:.0f}% ram {state['ram']:.0f}% "
                      f"disk {state['disk_root_pct']:.0f}% render+push {push_ms:.0f}ms "
                      f"(glitches {glitches})", flush=True)

            if args.frames and count >= args.frames:
                break
    return 0


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
