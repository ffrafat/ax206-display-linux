#!/usr/bin/env python3
"""Display an image on the QDtech / AX206 USB screen.

Usage:
  show_image.py PATH [--fit stretch|contain|none] [--brightness 0-7]
  show_image.py --color RRGGBB        # fill solid color (hex)
  show_image.py --test                # color bars + label

Examples:
  show_image.py photo.jpg
  show_image.py wallpaper.png --fit contain
  show_image.py --color ff8800
"""
import argparse
import sys

from PIL import Image, ImageDraw, ImageFont

from ax206 import AX206Display, NATIVE_HEIGHT, NATIVE_WIDTH


def make_test_image(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h))
    px = img.load()
    bars = [(255, 0, 0), (0, 255, 0), (0, 0, 255),
            (255, 255, 0), (0, 255, 255), (255, 0, 255),
            (255, 255, 255), (40, 40, 40)]
    bw = max(1, w // len(bars))
    for x in range(w):
        idx = min(x // bw, len(bars) - 1)
        for y in range(h):
            px[x, y] = bars[idx]
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf", 40)
    except Exception:
        font = ImageFont.load_default()
    label = f"{w}x{h}"
    bbox = d.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.rectangle([(w - tw) // 2 - 12, (h - th) // 2 - 12,
                 (w + tw) // 2 + 12, (h + th) // 2 + 12], fill=(0, 0, 0))
    d.text(((w - tw) // 2, (h - th) // 2 - 8), label, fill=(255, 255, 255), font=font)
    return img


def main() -> int:
    ap = argparse.ArgumentParser(description="Show an image on the AX206 USB screen")
    ap.add_argument("path", nargs="?", help="image file to display")
    ap.add_argument("--fit", choices=["stretch", "contain", "none"], default="contain")
    ap.add_argument("--color", help="fill a solid hex color, e.g. ff8800")
    ap.add_argument("--brightness", type=int, default=None, help="0..7")
    ap.add_argument("--test", action="store_true", help="show test pattern")
    args = ap.parse_args()

    with AX206Display() as s:
        if args.brightness is not None:
            try:
                s.set_brightness(args.brightness)
            except Exception as e:
                print(f"(brightness not set: {e})")

        if args.color:
            h = args.color.lstrip("#")
            rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
            s.fill(rgb)
            print(f"filled {rgb}")
            return 0

        if args.test or not args.path:
            s.draw_image(make_test_image(s.width, s.height), fit="stretch")
            print("test pattern shown")
            return 0

        img = Image.open(args.path)
        s.draw_image(img, fit=args.fit)
        print(f"displayed {args.path} ({args.fit})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
