"""Split comparison: the upscaled picture on one side of a divider, the untouched original on the other.

The output window (host_window) is clipped to the left part with a window region, so the real
original shows through on the right - an honest comparison for every engine. On top, a
click-through layered window draws the divider and the two labels ("WITH: <engine>" / "WITHOUT").
Text goes through GDI (Uniscribe), so Arabic, Hindi, Bengali and Chinese are shaped correctly.
"""
import ctypes
import ctypes.wintypes as wt
import threading

import numpy as np
from PIL import Image, ImageDraw

from host_window import CLASS_NAME, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, \
    WS_EX_TRANSPARENT, WS_POPUP, WS_VISIBLE, _register_class, _windows, kernel32, user32

gdi32 = ctypes.WinDLL("gdi32")
gdi32.CreateRectRgn.restype = wt.HRGN
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.CreateFontW.restype = wt.HFONT
gdi32.GetStockObject.restype = wt.HGDIOBJ
user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
gdi32.SetBkColor.argtypes = [wt.HDC, wt.DWORD]
gdi32.SetTextColor.argtypes = [wt.HDC, wt.DWORD]
gdi32.GetTextExtentPoint32W.argtypes = [wt.HDC, wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(wt.SIZE)]
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.DrawTextW.argtypes = [wt.HDC, wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(wt.RECT), wt.UINT]
user32.SetWindowRgn.argtypes = [wt.HWND, wt.HRGN, wt.BOOL]
user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
                                       ctypes.POINTER(wt.POINT), wt.DWORD, ctypes.c_void_p, wt.DWORD]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte), ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


def _dib(dc, w, h):
    """32-bit top-down DIB section selected into dc; returns (bitmap, numpy view of its pixels)."""
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    bmp = gdi32.CreateDIBSection(dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    view = np.ctypeslib.as_array((ctypes.c_ubyte * (w * h * 4)).from_address(bits.value)).reshape(h, w, 4)
    return bmp, view


def text_mask(text, px, rtl=False):
    """Coverage mask (h x w uint8) of text drawn by GDI at px pixels height, bold."""
    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    font = gdi32.CreateFontW(-px, 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 4, 0, "Segoe UI Semibold")  # ANTIALIASED_QUALITY
    old_font = gdi32.SelectObject(dc, font)
    size = wt.SIZE()
    gdi32.GetTextExtentPoint32W(dc, text, len(text), ctypes.byref(size))
    w, h = max(1, size.cx + 4), max(1, size.cy + 2)
    bmp, view = _dib(dc, w, h)
    old_bmp = gdi32.SelectObject(dc, bmp)
    gdi32.SetBkColor(dc, 0x000000)
    gdi32.SetTextColor(dc, 0xFFFFFF)
    r = wt.RECT(0, 0, w, h)
    flags = 0x20 | 0x800 | 0x1 | (0x20000 if rtl else 0)  # DT_SINGLELINE | DT_NOPREFIX | DT_CENTER | DT_RTLREADING
    user32.FillRect(dc, ctypes.byref(r), gdi32.GetStockObject(4))
    user32.DrawTextW(dc, text, -1, ctypes.byref(r), flags)
    mask = view[..., 1].copy()
    gdi32.SelectObject(dc, old_bmp)
    gdi32.SelectObject(dc, old_font)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteObject(font)
    gdi32.DeleteDC(dc)
    user32.ReleaseDC(None, screen)
    return mask


def draw_overlay(w, h, split, left_text, right_text, accent=(118, 185, 0), rtl=False):
    """RGBA image: divider at x=split, a label pill on each side of it near the top."""
    img = np.zeros((h, w, 4), np.uint8)
    pil = Image.fromarray(img, "RGBA")
    d = ImageDraw.Draw(pil)
    d.rectangle((split - 2, 0, split + 1, h), fill=(0, 0, 0, 110))      # soft shadow
    d.rectangle((split - 1, 0, split, h), fill=(255, 255, 255, 235))     # the divider itself
    px = max(14, min(26, h // 40))
    pad, gap, top = px * 3 // 4, px, max(12, h // 30)
    masks = [text_mask(left_text, px, rtl), text_mask(right_text, px, rtl)]
    boxes = []
    for i, m in enumerate(masks):
        bw, bh = m.shape[1] + 2 * pad, m.shape[0] + pad
        x0 = split - gap - bw if i == 0 else split + gap
        x0 = max(4, min(w - bw - 4, x0))
        boxes.append((x0, top, bw, bh, m))
        d.rounded_rectangle((x0, top, x0 + bw, top + bh), radius=bh // 2,
                            fill=(*accent, 230) if i == 0 else (20, 22, 28, 215))
    img = np.array(pil)
    for i, (x0, y0, bw, bh, m) in enumerate(boxes):
        ty, tx = y0 + (bh - m.shape[0]) // 2, x0 + pad
        hh, ww = min(m.shape[0], h - ty), min(m.shape[1], w - tx)
        a = m[:hh, :ww].astype(np.float32) / 255
        ink = np.array((10, 12, 16) if i == 0 else (240, 242, 246), np.float32)
        region = img[ty:ty + hh, tx:tx + ww].astype(np.float32)
        region[..., :3] = region[..., :3] * (1 - a[..., None]) + ink * a[..., None]
        region[..., 3] = np.maximum(region[..., 3], a * 255)
        img[ty:ty + hh, tx:tx + ww] = region.astype(np.uint8)
    return img


class LabelWindow:
    """Click-through, topmost layered window showing an RGBA image with per-pixel alpha."""

    def __init__(self, x, y, w, h, exclude_from_capture=False):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.hwnd = None
        self.closed = threading.Event()
        ready = threading.Event()
        threading.Thread(target=self._run, args=(exclude_from_capture, ready), daemon=True).start()
        ready.wait(5)

    def _run(self, exclude, ready):
        _register_class()
        ex = WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        hwnd = user32.CreateWindowExW(ex, CLASS_NAME, "ClearFrame Compare", WS_POPUP, self.x, self.y, self.w, self.h,
                                      None, None, kernel32.GetModuleHandleW(None), None)
        if hwnd:
            self.hwnd = hwnd
            _windows[hwnd] = self
            if exclude:
                user32.SetWindowDisplayAffinity(hwnd, 0x11)
        ready.set()
        if not hwnd:
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self.closed.set()

    def show(self, rgba):
        if not self.hwnd:
            return
        h, w = rgba.shape[:2]
        screen = user32.GetDC(None)
        dc = gdi32.CreateCompatibleDC(screen)
        bmp, view = _dib(dc, w, h)
        a = rgba[..., 3:4].astype(np.uint16)
        view[..., :3] = (rgba[..., 2::-1].astype(np.uint16) * a // 255).astype(np.uint8)   # premultiplied BGR
        view[..., 3] = rgba[..., 3]
        old = gdi32.SelectObject(dc, bmp)
        blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
        user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(wt.POINT(self.x, self.y)), ctypes.byref(wt.SIZE(w, h)),
                                   dc, ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(blend), 2)  # ULW_ALPHA
        user32.ShowWindow(self.hwnd, 8)  # SW_SHOWNA
        gdi32.SelectObject(dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(dc)
        user32.ReleaseDC(None, screen)

    def hide(self):
        if self.hwnd:
            user32.ShowWindow(self.hwnd, 0)

    def close(self):
        if self.hwnd and not self.closed.is_set():
            user32.PostMessageW(self.hwnd, 0x0010, 0, 0)  # WM_CLOSE


def clip_left(hwnd, width, height):
    """Show only the left `width` pixels of a window (None width: the whole window again)."""
    rgn = gdi32.CreateRectRgn(0, 0, width, height) if width is not None else None
    user32.SetWindowRgn(hwnd, rgn, True)   # the system owns rgn afterwards
