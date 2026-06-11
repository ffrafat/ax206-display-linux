#!/usr/bin/env python3
"""Cross-platform system-monitor dashboard for the AX206 SmartCool USB screen.

Alternates two screens every 3 seconds:
  - Stats: 2×2 cards (CPU, RAM, Disk, Net) — icon + value + arc gauge
  - Clock: full-screen HH:MM with blinking colon, date line, seconds progress bar
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

M  = 16
G  = 12
HDR = 36

ROTATE_CLOCK = 10.0
ROTATE_STATS = 10.0

# ---- Palette ----
BG     = (10, 12, 16)
PANEL  = (20, 24, 32)
INK    = (240, 244, 252)
MUTED  = (100, 112, 135)
TRACK  = (36, 42, 56)
CPU    = (96, 165, 250)
RAM    = (192, 132, 252)
DISK   = (52, 211, 153)
NET_DN = (74, 222, 128)
NET_UP = (250, 204, 21)
HOT    = (248, 113, 113)
WARN   = (251, 146, 60)

# ---- Fonts ----
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_INTER_LIGHT    = os.path.join(SCRIPT_DIR, "assets", "fonts", "Inter-Light.ttf")
_INTER_REGULAR  = os.path.join(SCRIPT_DIR, "assets", "fonts", "Inter-Regular.ttf")
_INTER_SEMIBOLD = os.path.join(SCRIPT_DIR, "assets", "fonts", "Inter-SemiBold.ttf")
_UBUNTU_DIR = "/usr/share/fonts/truetype/ubuntu"
_UBUNTU_R   = os.path.join(_UBUNTU_DIR, "Ubuntu-R.ttf")
_UBUNTU_B   = os.path.join(_UBUNTU_DIR, "Ubuntu-B.ttf")
_UBUNTU_L   = os.path.join(_UBUNTU_DIR, "Ubuntu-L.ttf")


def _font(size: int, bold: bool = False, light: bool = False) -> ImageFont.FreeTypeFont:
    """Ubuntu → bundled Inter → system fallback."""
    if light:
        cands = [_UBUNTU_L, _INTER_LIGHT,
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                 "/System/Library/Fonts/HelveticaNeue.ttc"]
    elif bold:
        cands = [_UBUNTU_B, _INTER_SEMIBOLD,
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]
    else:
        cands = [_UBUNTU_R, _INTER_REGULAR,
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                 "/System/Library/Fonts/Supplemental/Arial.ttf"]
    for p in cands:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


F_HEADER  = _font(11 * SS, bold=True)
F_IP      = _font(11 * SS)
F_VALUE   = _font(22 * SS, bold=True)   # main metric — intentionally compact
F_NET_VAL = _font(20 * SS, bold=True)   # NET combined line (fits two values)
F_SUB     = _font(10 * SS)
F_NET_SUB = _font(10 * SS)
F_DATE    = _font(14 * SS)


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


def get_hostname() -> str:
    try:
        return socket.gethostname().upper()
    except Exception:
        return "LOCALHOST"


def fmt_rate(bytes_per_s: float) -> str:
    if bytes_per_s >= 1e6:
        return f"{bytes_per_s / 1e6:.1f} MB/s"
    if bytes_per_s >= 1e3:
        return f"{bytes_per_s / 1e3:.0f} KB/s"
    return f"{bytes_per_s:.0f} B/s"


def fmt_rate_short(bytes_per_s: float) -> str:
    """Compact: '2.4M', '340K', '512B'."""
    if bytes_per_s >= 1e6:
        return f"{bytes_per_s / 1e6:.1f}M"
    if bytes_per_s >= 1e3:
        return f"{bytes_per_s / 1e3:.0f}K"
    return f"{bytes_per_s:.0f}B"


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


# ---- Icons (drawn in PIL at 2× render coords) ----
# All icons take (d, cx, cy, sz, color) where cx/cy is the icon centre.

def _icon_cpu(d: ImageDraw.ImageDraw, cx: float, cy: float,
              sz: int, color: tuple[int, int, int]) -> None:
    """Microchip: rounded square body, inner die square, 2 pins per side."""
    k  = sz / 32
    b  = round(11 * k)   # half body size
    lw = max(1, round(2 * k))
    r  = max(1, round(3 * k))
    # Body outline
    d.rounded_rectangle([cx-b, cy-b, cx+b, cy+b], radius=r, outline=color, width=lw)
    # Inner die (filled)
    d2 = round(4 * k)
    d.rectangle([cx-d2, cy-d2, cx+d2, cy+d2], fill=color)
    # Pins: two per side, offset ±4k from centre
    pl = round(5 * k)   # pin length
    pw = max(1, round(1.5 * k))  # pin half-width
    for off in (-round(4 * k), round(4 * k)):
        d.rectangle([cx-b-pl, cy+off-pw, cx-b,    cy+off+pw], fill=color)  # left
        d.rectangle([cx+b,    cy+off-pw, cx+b+pl, cy+off+pw], fill=color)  # right
        d.rectangle([cx+off-pw, cy-b-pl, cx+off+pw, cy-b],    fill=color)  # top
        d.rectangle([cx+off-pw, cy+b,    cx+off+pw, cy+b+pl], fill=color)  # bottom


def _icon_ram(d: ImageDraw.ImageDraw, cx: float, cy: float,
              sz: int, color: tuple[int, int, int]) -> None:
    """RAM stick: horizontal bar with 5 contact fingers below."""
    k   = sz / 32
    hw  = round(13 * k)   # half-width of stick
    hh  = round(5 * k)    # half-height of stick
    r   = max(1, round(2 * k))
    # Stick body (shifted slightly up so fingers sit below centre)
    sy  = cy - round(3 * k)
    d.rounded_rectangle([cx-hw, sy-hh, cx+hw, sy+hh], radius=r, fill=color)
    # 5 contact fingers
    fw = max(1, round(2.5 * k))
    fh = round(5 * k)
    spacing = round(5 * k)
    for i in range(-2, 3):
        fx = cx + i * spacing
        d.rectangle([fx-fw, sy+hh, fx+fw, sy+hh+fh], fill=color)


def _icon_disk(d: ImageDraw.ImageDraw, cx: float, cy: float,
               sz: int, color: tuple[int, int, int]) -> None:
    """Cylinder (storage): filled top ellipse, side lines, outline bottom ellipse."""
    k   = sz / 32
    ew  = round(12 * k)   # half-width of ellipse
    eh  = round(4 * k)    # half-height of ellipse (vertical squish)
    body_h = round(10 * k)
    lw  = max(1, round(2 * k))
    top_cy  = cy - body_h // 2
    bot_cy  = cy + body_h // 2
    # Side lines connecting top and bottom ellipses
    d.rectangle([cx-ew, top_cy, cx-ew+lw, bot_cy], fill=color)
    d.rectangle([cx+ew-lw, top_cy, cx+ew, bot_cy], fill=color)
    # Bottom ellipse (outline only — gives depth)
    d.ellipse([cx-ew, bot_cy-eh, cx+ew, bot_cy+eh], outline=color, width=lw)
    # Top ellipse (filled — front face)
    d.ellipse([cx-ew, top_cy-eh, cx+ew, top_cy+eh], fill=color)


def _icon_wifi(d: ImageDraw.ImageDraw, cx: float, cy: float,
               sz: int, color: tuple[int, int, int]) -> None:
    """WiFi signal: 3 concentric arcs opening upward + centre dot."""
    k   = sz / 32
    lw  = max(2, round(2.5 * k))
    # Arc anchor sits near the bottom of the icon bounding box
    ay  = cy + round(8 * k)
    dot = max(2, round(2.5 * k))
    d.ellipse([cx-dot, ay-dot, cx+dot, ay+dot], fill=color)
    for r in (round(5*k), round(9*k), round(14*k)):
        d.arc([cx-r, ay-r, cx+r, ay+r], start=210, end=330, fill=color, width=lw)


_ICONS = {
    "cpu":  _icon_cpu,
    "ram":  _icon_ram,
    "disk": _icon_disk,
    "wifi": _icon_wifi,
}

ICON_SZ = 16 * SS   # 16 display px, drawn at 32 render px


# ---- Arc Gauge ----
# gauge_cy = y1 - 42 render px from card bottom.
# r_out=62, r_in=40 → ring 22 render-px thick (11 display-px).
# Inner circle bottom = y1-42+40 = y1-2 (within card).
# Arc top             = y1-42-62 = y1-104 → content zone y0..y0+152.

_GAUGE_OFFSET = 42
_R_OUT        = 62
_R_IN         = 40
_SWEEP_START  = 175
_SWEEP        = 190


def arc_gauge(d: ImageDraw.ImageDraw, cx: float, cy: float, pct: float,
              color: tuple[int, int, int]) -> None:
    bo = [cx - _R_OUT, cy - _R_OUT, cx + _R_OUT, cy + _R_OUT]
    bi = [cx - _R_IN,  cy - _R_IN,  cx + _R_IN,  cy + _R_IN]
    d.pieslice(bo, start=_SWEEP_START, end=_SWEEP_START + _SWEEP, fill=TRACK)
    d.ellipse(bi, fill=PANEL)
    if pct > 0:
        d.pieslice(bo, start=_SWEEP_START,
                   end=_SWEEP_START + _SWEEP * min(1.0, pct / 100), fill=color)
        d.ellipse(bi, fill=PANEL)


# ---- Card Renderers ----

def _draw_card(d: ImageDraw.ImageDraw, rect: tuple[float, float, float, float],
               icon: str, icon_color: tuple[int, int, int],
               value: str, value_color: tuple[int, int, int],
               pct: float, arc_color: tuple[int, int, int],
               sub: str = "") -> None:
    """
    Generic card: rounded bg → centred icon → large value → subtitle → arc gauge.
    No title text or dot — the icon identifies the metric.
    """
    x0, y0, x1, y1 = rect
    pad     = 14 * SS
    inner_w = x1 - x0 - 2 * pad
    cx      = (x0 + x1) / 2

    d.rounded_rectangle(rect, radius=10 * SS, fill=PANEL)

    # Icon centred horizontally, top-padded vertically
    icon_cx = cx
    icon_cy = y0 + pad + ICON_SZ // 2      # icon centre
    _ICONS[icon](d, icon_cx, icon_cy, ICON_SZ, icon_color)

    # Main value
    val = fit_text(value, F_VALUE, inner_w)
    text_centered(d, cx, y0 + 94, val, F_VALUE, value_color)

    # Subtitle
    if sub:
        text_centered(d, cx, y0 + 136, fit_text(sub, F_SUB, inner_w), F_SUB, MUTED)

    # Arc gauge
    arc_gauge(d, cx, y1 - _GAUGE_OFFSET, pct, arc_color)


def draw_stat_card(d: ImageDraw.ImageDraw, rect: tuple[float, float, float, float],
                   icon: str, value: str, pct: float,
                   accent: tuple[int, int, int],
                   value_color: Optional[tuple[int, int, int]] = None,
                   sub: str = "") -> None:
    _draw_card(d, rect, icon, accent,
               value, value_color or INK,
               pct, usage_color(pct, accent), sub)


def draw_net_card(d: ImageDraw.ImageDraw, rect: tuple[float, float, float, float],
                  rx: float, tx: float,
                  rx_total: float, tx_total: float) -> None:
    x0, y0, x1, y1 = rect
    pad     = 14 * SS
    inner_w = x1 - x0 - 2 * pad
    cx      = (x0 + x1) / 2

    d.rounded_rectangle(rect, radius=10 * SS, fill=PANEL)

    # WiFi icon in NET_DN colour
    _icon_wifi(d, cx, y0 + pad + ICON_SZ // 2, ICON_SZ, NET_DN)

    # "↓ 2.4M  ·  ↑ 340K" — each segment in its own card colour
    SEP   = "  ·  "
    dn_s  = f"↓ {fmt_rate_short(rx)}"
    up_s  = f"↑ {fmt_rate_short(tx)}"
    parts = [dn_s, SEP, up_s]
    cols  = [NET_DN, MUTED, NET_UP]
    ws    = [text_width(p, F_NET_VAL) for p in parts]
    x     = cx - sum(ws) / 2
    val_cy = y0 + 94
    for part, color, w in zip(parts, cols, ws):
        bbox = F_NET_VAL.getbbox(part)
        th   = bbox[3] - bbox[1]
        d.text((x - bbox[0], val_cy - th / 2 - bbox[1]),
               part, font=F_NET_VAL, fill=color)
        x += w

    # Session totals
    sub = fit_text(f"↑ {fmt_gb(tx_total)}  ·  ↓ {fmt_gb(rx_total)}", F_NET_SUB, inner_w)
    text_centered(d, cx, y0 + 136, sub, F_NET_SUB, MUTED)

    # Arc — % of 100 Mbps link
    net_pct = min(100.0, (rx + tx) / 12.5e6 * 100)
    arc_gauge(d, cx, y1 - _GAUGE_OFFSET, net_pct, usage_color(net_pct, NET_DN))


def _clock_fonts(max_w: float) -> tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    for pt in range(118, 48, -2):
        f_h = _font(pt * SS, bold=True)
        f_s = _font(pt * SS, bold=True)
        f_m = _font(pt * SS, light=True)
        if (text_width("88", f_h) + text_width(":", f_s) + text_width("88", f_m)) <= max_w:
            return f_h, f_s, f_m
    f = _font(48 * SS, bold=True)
    return f, f, _font(48 * SS, light=True)


# ---- Renderers ----

def render_stats(state: dict) -> Image.Image:
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d   = ImageDraw.Draw(img)

    # Header: hostname left, IP right
    hostname = fit_text(state["hostname"], F_HEADER, (W // 2 - M - 4) * SS)
    ip_text  = fit_text(state["ip"],       F_IP,     (W // 2 - M - 4) * SS)
    hdr_cy   = 18 * SS
    bh = F_HEADER.getbbox(hostname)
    bi = F_IP.getbbox(ip_text)
    d.text((M * SS, hdr_cy - (bh[3]-bh[1])//2 - bh[1]), hostname, font=F_HEADER, fill=INK)
    tw_i = bi[2] - bi[0]
    d.text(((W-M)*SS - tw_i - bi[0], hdr_cy - (bi[3]-bi[1])//2 - bi[1]),
           ip_text, font=F_IP, fill=MUTED)
    d.line([(M*SS, (HDR-6)*SS), ((W-M)*SS, (HDR-6)*SS)], fill=TRACK, width=2*SS)

    # Cards
    cpu_pct = state["cpu"]
    temp    = state["temp"]
    if temp is not None:
        cpu_value     = f"{temp:.0f}°C"
        cpu_val_color = temp_color(temp)
        cpu_sub       = f"{cpu_pct:.0f}%  ·  {state['cpu_freq']:.1f} GHz"
    else:
        cpu_value     = f"{cpu_pct:.0f}%"
        cpu_val_color = usage_color(cpu_pct, CPU)
        cpu_sub       = f"{state['cpu_freq']:.1f} GHz"

    draw_stat_card(d, card_rect(0, 0), "cpu", cpu_value,
                   cpu_pct, CPU, cpu_val_color, cpu_sub)

    ram_pct = state["ram"]
    draw_stat_card(d, card_rect(1, 0), "ram", f"{ram_pct:.0f}%",
                   ram_pct, RAM, usage_color(ram_pct, RAM),
                   fmt_used_total(state["ram_used"], state["ram_total"]))

    disk_pct = state["disk_root_pct"]
    draw_stat_card(d, card_rect(0, 1), "disk", f"{disk_pct:.0f}%",
                   disk_pct, DISK, usage_color(disk_pct, DISK),
                   fmt_used_total(state["disk_used"], state["disk_total"]))

    draw_net_card(d, card_rect(1, 1),
                  state["rx"], state["tx"],
                  state["net_recv_total"], state["net_sent_total"])

    return img.resize((W, H), Image.Resampling.LANCZOS)


def render_clock(show_sep: bool, now: Optional[time.struct_time] = None) -> Image.Image:
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d   = ImageDraw.Draw(img)

    t  = now or time.localtime()
    hh = f"{t.tm_hour:02d}"
    mm = f"{t.tm_min:02d}"

    clock_cy = int(H * SS * 0.43)
    max_w    = (W - 32) * SS
    f_hour, f_sep, f_min = _clock_fonts(max_w)
    sep_color = INK if show_sep else BG

    parts  = (hh, ":", mm)
    fonts  = (f_hour, f_sep, f_min)
    widths = [text_width(p, f) for p, f in zip(parts, fonts)]
    x      = (W * SS - sum(widths)) / 2

    for part, font, w in zip(parts, fonts, widths):
        fill = sep_color if part == ":" else INK
        bbox = font.getbbox(part)
        th   = bbox[3] - bbox[1]
        d.text((x - bbox[0], clock_cy - th/2 - bbox[1]), part, font=font, fill=fill)
        x += w

    # Date line
    days   = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
    months = ("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC")
    date_str = f"{days[t.tm_wday]}  ·  {t.tm_mday} {months[t.tm_mon-1]}"
    text_centered(d, W*SS/2, int(H*SS*0.79), date_str, F_DATE, MUTED)

    # Seconds bar
    bar_h  = 5 * SS
    bar_y0 = H * SS - bar_h
    bar_x0 = M * SS
    bar_x1 = (W - M) * SS
    fill_w = (bar_x1 - bar_x0) * (t.tm_sec / 59)
    d.rounded_rectangle([bar_x0, bar_y0, bar_x1, H*SS], radius=bar_h//2, fill=TRACK)
    if fill_w > bar_h:
        d.rounded_rectangle([bar_x0, bar_y0, bar_x0+fill_w, H*SS],
                            radius=bar_h//2, fill=CPU)

    return img.resize((W, H), Image.Resampling.LANCZOS)


# ---- Core Cycle ----

def collect_state(prev_net, dt: float) -> tuple[dict, object]:
    cpu = psutil.cpu_percent()
    vm  = psutil.virtual_memory()
    net = psutil.net_io_counters()

    try:
        freq     = psutil.cpu_freq()
        cpu_freq = freq.current / 1000.0 if freq and freq.current else 0.0
    except Exception:
        cpu_freq = 0.0

    try:
        disk = psutil.disk_usage("/")
        disk_pct, disk_used, disk_total = disk.percent, disk.used, disk.total
    except Exception:
        disk_pct = disk_used = disk_total = 0.0

    rx = (net.bytes_recv - prev_net.bytes_recv) / dt if dt > 0 else 0
    tx = (net.bytes_sent - prev_net.bytes_sent) / dt if dt > 0 else 0

    return {
        "cpu":            cpu,
        "cpu_freq":       cpu_freq,
        "ram":            vm.percent,
        "ram_used":       vm.used,
        "ram_total":      vm.total,
        "disk_root_pct":  disk_pct,
        "disk_used":      disk_used,
        "disk_total":     disk_total,
        "rx":             rx,
        "tx":             tx,
        "net_recv_total": net.bytes_recv,
        "net_sent_total": net.bytes_sent,
        "temp":           get_cpu_temp(),
        "ip":             get_ip_address(),
        "hostname":       get_hostname(),
    }, net


def main() -> int:
    ap = argparse.ArgumentParser(description="System monitor dashboard for AX206 USB display")
    ap.add_argument("--interval",   type=float, default=1.0)
    ap.add_argument("--clock-secs", type=float, default=ROTATE_CLOCK)
    ap.add_argument("--stats-secs", type=float, default=ROTATE_STATS)
    ap.add_argument("--frames",     type=int,   default=0)
    args = ap.parse_args()

    psutil.cpu_percent()
    prev_net = psutil.net_io_counters()
    last     = time.time()
    screen   = "clock"
    screen_started = time.time()

    with AX206Display() as s:
        print(f"sysdash running ({s.width}x{s.height}), "
              f"clock {args.clock_secs}s / stats {args.stats_secs}s, Ctrl-C to stop")
        count = glitches = consec = 0

        while True:
            now     = time.time()
            elapsed = now - screen_started
            if screen == "clock" and elapsed >= args.clock_secs:
                screen = "stats";  screen_started = now
            elif screen == "stats" and elapsed >= args.stats_secs:
                screen = "clock";  screen_started = now

            dt   = now - last
            last = now

            if screen == "clock":
                frame = render_clock(int(now) % 2 == 0)
            else:
                state, prev_net = collect_state(prev_net, dt)
                frame = render_stats(state)

            t0 = time.time()
            try:
                s.draw_image(frame, fit="stretch")
            except Exception as e:
                glitches += 1;  consec += 1
                print(f"frame {count+1}: glitch ({e}) consec={consec}", flush=True)
                if consec <= 2:
                    s.recover()
                else:
                    print("  attempting full reopen…", flush=True)
                    ok = s.reopen()
                    print(f"  reopen {'OK' if ok else 'FAILED'}", flush=True)
                if consec >= 6:
                    print("Too many consecutive failures — display needs physical replug.",
                          flush=True)
                    return 1
                continue

            consec = 0;  count += 1
            if count == 1 or count % 10 == 0:
                ms = (time.time() - t0) * 1000
                print(f"frame {count} [{'clock' if screen=='clock' else 'stats'}]: "
                      f"push {ms:.0f}ms (glitches {glitches})", flush=True)

            if args.frames and count >= args.frames:
                break

            time.sleep(max(0.05, 1.0 - (time.time() % 1.0)) if screen == "clock"
                       else args.interval)

    return 0


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
