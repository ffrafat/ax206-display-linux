# ax206-usb-display-macos

Drives the small 3.5" "QDtech USB-Display" (USB id `1908:0102`, sold as an
AIDA64 secondary screen) from **macOS** over libusb — no Windows-only vendor
software needed. Includes a clean driver, an image viewer, and a live system
monitor dashboard.

The device is an **AX206 digital-photo-frame clone** (the "SmartCool" firmware
variant). Its protocol was reverse-engineered from
[dreamlayers/dpf-ax](https://github.com/dreamlayers/dpf-ax) and confirmed on
real hardware. Pure-Python, works on Apple Silicon.

Panel: **480×320 landscape, RGB565, USB 1.1** (~1.4 full-screen fps).

## Files
- `ax206.py` — the driver (`AX206Display`: `open`, `blit`, `fill`, `draw_image`, `recover`, `reopen`)
- `show_image.py` — CLI to show an image / solid color / test pattern
- `dashboard.py` — live system-monitor dashboard (CPU/RAM rings, net, CPU history)

## Setup
```bash
python3 -m venv .venv
.venv/bin/pip install pyusb pillow numpy psutil
brew install libusb
```

## Usage
```bash
.venv/bin/python show_image.py photo.jpg            # show an image (letterboxed)
.venv/bin/python show_image.py wall.png --fit stretch
.venv/bin/python show_image.py --color ff8800       # solid color
.venv/bin/python dashboard.py                        # live monitor (Ctrl-C to stop)
```

## Protocol notes (important)
- Geometry is fixed **480×320 landscape**. Pixels are **RGB565 big-endian**.
- Transport is USB Mass-Storage Bulk-Only: 31-byte CBW (`USBC`…) + data + 13-byte CSW (`USBS`…).
- **BLIT (CDB op `0x12`) is the ONLY command this firmware implements.** Any other
  vendor command — SCSI INQUIRY, GETLCD, SETPROPERTY/brightness — times out and
  **wedges the USB endpoint**, requiring a physical unplug/replug to recover. The
  driver therefore only ever sends BLIT.
- The device is single-owner: only one program may hold it at a time.
- Occasional CSW glitches self-heal via `recover()` (MSC reset + clear_halt) / `reopen()`.

## Credits
Protocol knowledge and prior art from these projects (please support them):
- [dreamlayers/dpf-ax](https://github.com/dreamlayers/dpf-ax) — the AX206 DPF
  tools and firmware; the authoritative source for the CBW/BLIT command set.
- [mathoudebine/turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)
  — broad reference for this family of USB info-displays.
- The AIDA64 community forums, for identifying `1908:0102` as an AX206/SmartCool unit.

This is an independent, clean-room Python reimplementation for macOS.

## License
GPL-3.0. See [LICENSE](LICENSE).
