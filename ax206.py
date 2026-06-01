"""Clean driver for the QDtech 'USB-Display' (VID 1908:0102) — an AX206 DPF.

This is the small 3.5" USB screen sold as an AIDA64 secondary display. The
protocol was reverse-engineered from dreamlayers/dpf-ax and confirmed on
hardware: it is USB Mass-Storage Bulk-Only carrying a vendor SCSI command set.

CRITICAL LESSON (the thing that took us four tries to learn):
  Do NOT send a standard SCSI INQUIRY. This firmware doesn't implement it —
  it returns garbage and wedges the OUT endpoint (needs a physical replug to
  recover). Only ever send the vendor commands below (CDB byte 0 = 0xCD).

Wire protocol
-------------
Transport: USB Mass Storage Bulk-Only Transport (BOT).
  EP 0x01 BULK OUT, EP 0x81 BULK IN, both wMaxPacketSize 64, USB 1.1.

CBW (Command Block Wrapper, 31 bytes):
  'USBC'                       dCBWSignature (55 53 42 43)
  de ad be ef                  dCBWTag
  <LE32 dCBWDataTransferLength> number of data-phase bytes
  <u8 flags>                   0x00 = data OUT (host->device), 0x80 = data IN
  00                           bCBWLUN
  10                           bCBWCBLength = 16
  <16-byte CDB>

Vendor CDBs (byte 0 always 0xCD):
  BLIT:   cd 00 00 00 00 06 12  x0_LE16 y0_LE16 (x1-1)_LE16 (y1-1)_LE16 00
  GETLCD: cd 00 00 00 00 02 00 ...   -> data-IN 5 bytes: w_LE16, h_LE16, status
  SETPROP:cd 00 00 00 00 06 01  prop_LE16 value_LE16 ...   (e.g. brightness)

Pixel data (BLIT data phase): width*height pixels, RGB565 BIG-ENDIAN:
  byte0 = (R & 0xf8) | ((G & 0xe0) >> 5)
  byte1 = ((G & 0x1c) << 3) | ((B & 0xf8) >> 3)
  red=0xf800  green=0x07e0  blue=0x001f

CSW (Command Status Wrapper, 13 bytes): 'USBS' + tag + LE32 residue + status(0=OK).
"""
from __future__ import annotations

import struct
import time
from typing import Optional

import numpy as np
import usb.core
import usb.util
from PIL import Image

VID = 0x1908
PID = 0x0102
EP_OUT = 0x01
EP_IN = 0x81

# Native panel geometry (landscape). The 3.5" AX206 is 480x320.
NATIVE_WIDTH = 480
NATIVE_HEIGHT = 320

# Vendor command opcodes (CDB[6] when CDB[5]==0x06)
USBCMD_SETPROPERTY = 0x01
USBCMD_BLIT        = 0x12
PROPERTY_BRIGHTNESS = 0x01

DIR_OUT = 0x00
DIR_IN  = 0x80


def to_rgb565_be(img: Image.Image) -> bytes:
    """Convert a PIL image to RGB565 big-endian bytes (vectorized via numpy)."""
    arr = np.asarray(img.convert("RGB"), dtype=np.uint16)  # (h, w, 3)
    r = arr[:, :, 0]
    g = arr[:, :, 1]
    b = arr[:, :, 2]
    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)  # (h, w) uint16
    # Big-endian on the wire: high byte first.
    return rgb565.astype(">u2").tobytes()


class AX206Display:
    def __init__(self, width: int = NATIVE_WIDTH, height: int = NATIVE_HEIGHT) -> None:
        self.dev: Optional[usb.core.Device] = None
        self.width = width
        self.height = height

    # ---- lifecycle ----

    def open(self) -> "AX206Display":
        dev = usb.core.find(idVendor=VID, idProduct=PID)
        if dev is None:
            raise RuntimeError(f"AX206 display {VID:#06x}:{PID:#06x} not found")
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except (NotImplementedError, usb.core.USBError):
            pass
        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass
        try:
            usb.util.claim_interface(dev, 0)
        except usb.core.USBError:
            pass
        for ep in (EP_OUT, EP_IN):
            try:
                dev.clear_halt(ep)
            except usb.core.USBError:
                pass
        self.dev = dev
        return self

    def close(self) -> None:
        if self.dev is not None:
            try:
                usb.util.release_interface(self.dev, 0)
            except usb.core.USBError:
                pass
            usb.util.dispose_resources(self.dev)
            self.dev = None

    def __enter__(self) -> "AX206Display":
        return self.open()

    def __exit__(self, *_exc) -> None:
        self.close()

    # ---- low-level transport ----

    def _bulk_out(self, data: bytes, timeout: int = 8000, retries: int = 2) -> int:
        assert self.dev is not None
        last = None
        for _ in range(retries + 1):
            try:
                return self.dev.write(EP_OUT, data, timeout=timeout)
            except usb.core.USBError as e:
                last = e
                if e.errno in (5, 32):  # EIO / EPIPE (stall)
                    try:
                        self.dev.clear_halt(EP_OUT)
                    except usb.core.USBError:
                        pass
                    time.sleep(0.05)
                    continue
                raise
        raise last

    def _bulk_in(self, length: int, timeout: int = 4000) -> bytes:
        assert self.dev is not None
        return bytes(self.dev.read(EP_IN, length, timeout=timeout))

    def recover(self) -> None:
        """Mass-Storage reset + clear stalls + drain IN, after a glitch."""
        if self.dev is None:
            return
        # Bulk-Only Mass Storage Reset (class request 0xFF).
        try:
            self.dev.ctrl_transfer(0x21, 0xFF, 0x0000, 0x0000, None, timeout=500)
        except usb.core.USBError:
            pass
        for ep in (EP_OUT, EP_IN):
            try:
                self.dev.clear_halt(ep)
            except usb.core.USBError:
                pass
        for _ in range(3):
            try:
                self.dev.read(EP_IN, 64, timeout=60)
            except usb.core.USBError:
                break

    def reopen(self) -> bool:
        """Full soft 'replug': drop the handle and re-acquire it. Returns
        True if the device came back. Heavier than recover() — used when
        recover() can't un-wedge the endpoint."""
        self.close()
        time.sleep(0.4)
        try:
            self.open()
            return True
        except Exception:
            return False

    @staticmethod
    def _cbw(data_len: int, direction: int, cdb: bytes) -> bytes:
        assert len(cdb) == 16
        return (b"USBC"
                + b"\xde\xad\xbe\xef"
                + struct.pack("<I", data_len)
                + bytes([direction, 0x00, 0x10])
                + cdb)

    def _read_csw(self, retries: int = 5) -> int:
        """Read the 13-byte CSW. Returns status byte (0=OK) or raises.

        The CSW occasionally doesn't arrive on the first read on these AX206
        clones — the reference dpf-ax driver retries up to 5x on timeout, so
        we do the same rather than giving up after one attempt.
        """
        last = None
        for _ in range(retries):
            try:
                csw = self._bulk_in(13, timeout=2000)
            except usb.core.USBError as e:
                last = e
                continue
            if len(csw) >= 13 and csw[:4] == b"USBS":
                return csw[12]
            last = RuntimeError(f"bad CSW: {csw.hex()}")
        raise last if last else RuntimeError("no CSW")

    def _command(self, cdb: bytes, direction: int = DIR_OUT,
                 data: bytes = b"", in_len: int = 0) -> bytes:
        """Issue one vendor SCSI command, return any IN-phase data."""
        block_len = in_len if direction == DIR_IN else len(data)
        self._bulk_out(self._cbw(block_len, direction, cdb))
        result = b""
        if direction == DIR_OUT and data:
            self._bulk_out(data)
        elif direction == DIR_IN and in_len:
            result = self._bulk_in(in_len)
        status = self._read_csw()
        if status != 0:
            raise RuntimeError(f"command CSW status = {status}")
        return result

    # ---- vendor commands ----

    # !!! DANGER: the two commands below are part of the dpf-ax spec but this
    # particular SmartCool/QDtech firmware does NOT implement them. Calling
    # either one TIMES OUT and WEDGES the OUT endpoint, requiring a physical
    # replug to recover. Confirmed on hardware 2026-06-02. They are kept here
    # only for documentation — do not call them on this unit. BLIT is the only
    # working command.

    def get_lcd_info(self) -> tuple[int, int, int]:
        """[WEDGES THIS FIRMWARE — do not use] Read panel geometry."""
        cdb = bytes([0xCD, 0, 0, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        data = self._command(cdb, DIR_IN, in_len=5)
        w = data[0] | (data[1] << 8)
        h = data[2] | (data[3] << 8)
        return w, h, data[4]

    def set_brightness(self, level: int) -> None:
        """[WEDGES THIS FIRMWARE — do not use] Set brightness 0..7."""
        level = max(0, min(7, level))
        cdb = bytearray(16)
        cdb[0] = 0xCD
        cdb[5] = 0x06
        cdb[6] = USBCMD_SETPROPERTY
        struct.pack_into("<HH", cdb, 7, PROPERTY_BRIGHTNESS, level)
        self._command(bytes(cdb), DIR_OUT)

    def blit(self, x0: int, y0: int, x1: int, y1: int, pixels_rgb565_be: bytes) -> None:
        """Send a rectangle (x0,y0)..(x1,y1) exclusive. pixels = (x1-x0)*(y1-y0) RGB565 BE."""
        w, h = x1 - x0, y1 - y0
        if len(pixels_rgb565_be) != w * h * 2:
            raise ValueError(f"need {w*h*2} bytes, got {len(pixels_rgb565_be)}")
        cdb = bytearray(16)
        cdb[0] = 0xCD
        cdb[5] = 0x06
        cdb[6] = USBCMD_BLIT
        struct.pack_into("<HHHH", cdb, 7, x0, y0, x1 - 1, y1 - 1)
        self._command(bytes(cdb), DIR_OUT, data=pixels_rgb565_be)

    # ---- high-level drawing ----

    def fill(self, rgb: tuple[int, int, int] = (0, 0, 0)) -> None:
        """Fill the whole screen with one color."""
        img = Image.new("RGB", (self.width, self.height), rgb)
        self.blit(0, 0, self.width, self.height, to_rgb565_be(img))

    def clear(self) -> None:
        self.fill((0, 0, 0))

    def draw_image(self, img: Image.Image, x: int = 0, y: int = 0,
                   fit: str = "stretch") -> None:
        """Draw a PIL image at (x, y).

        fit:
          'stretch' — resize to fill the whole screen (ignores x/y)
          'contain' — scale to fit preserving aspect, letterbox on black
          'none'    — draw at native size at (x, y), clipped to the screen
        """
        if fit == "stretch":
            frame = img.convert("RGB").resize((self.width, self.height))
            self.blit(0, 0, self.width, self.height, to_rgb565_be(frame))
            return
        if fit == "contain":
            canvas = Image.new("RGB", (self.width, self.height), (0, 0, 0))
            src = img.convert("RGB")
            src.thumbnail((self.width, self.height))
            ox = (self.width - src.width) // 2
            oy = (self.height - src.height) // 2
            canvas.paste(src, (ox, oy))
            self.blit(0, 0, self.width, self.height, to_rgb565_be(canvas))
            return
        # 'none'
        src = img.convert("RGB")
        w = min(src.width, self.width - x)
        h = min(src.height, self.height - y)
        if w <= 0 or h <= 0:
            return
        src = src.crop((0, 0, w, h))
        self.blit(x, y, x + w, y + h, to_rgb565_be(src))
