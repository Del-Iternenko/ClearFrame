"""A window owned by ClearFrame that mpv draws into (mpv --wid=<hwnd>).

Owning the window ourselves lets us do what only the owning process may do:
- WDA_EXCLUDEFROMCAPTURE, so a captured screen doesn't capture its own upscaled copy;
- exact size and position (mpv shrinks its own windows to the work area);
- click-through, topmost, never focused - for the overlay.
"""
import ctypes
import ctypes.wintypes as wt
import threading

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
user32.GetWindowDisplayAffinity.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.DWORD, ctypes.c_ubyte, wt.DWORD]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.AdjustWindowRectEx.argtypes = [ctypes.POINTER(wt.RECT), wt.DWORD, wt.BOOL, wt.DWORD]
user32.LoadCursorW.restype = wt.HANDLE
user32.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
kernel32.GetModuleHandleW.restype = wt.HMODULE
gdi32.GetStockObject.restype = wt.HGDIOBJ


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


WS_POPUP, WS_VISIBLE, WS_OVERLAPPEDWINDOW, WS_CLIPCHILDREN = 0x80000000, 0x10000000, 0x00CF0000, 0x02000000
WS_EX_TOPMOST, WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = 0x8, 0x80000, 0x20, 0x08000000, 0x80
WM_CLOSE, WM_DESTROY = 0x0010, 0x0002
CLASS_NAME = "ClearFrameOutput"

_windows = {}            # hwnd -> HostWindow, for the shared window procedure
_class_lock = threading.Lock()
_class_ready = False


@WNDPROC
def _wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_DESTROY:
        win = _windows.pop(hwnd, None)
        if win:
            win.closed.set()
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _register_class():
    global _class_ready
    with _class_lock:
        if _class_ready:
            return
        wc = WNDCLASSW()
        wc.lpfnWndProc = _wndproc
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))  # IDC_ARROW
        wc.hbrBackground = gdi32.GetStockObject(4)  # BLACK_BRUSH
        wc.lpszClassName = CLASS_NAME
        if not user32.RegisterClassW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())
        _class_ready = True


class HostWindow:
    """overlay=True: borderless, topmost, click-through, never focused, at exactly x, y, w, h.
    overlay=False and popup=True: borderless window filling x, y, w, h (e.g. another monitor).
    otherwise: a normal resizable window whose client area is w x h."""

    def __init__(self, title, x, y, w, h, overlay=False, popup=False, exclude_from_capture=False):
        self.hwnd = None
        self.closed = threading.Event()
        ready = threading.Event()
        threading.Thread(target=self._run, args=(title, x, y, w, h, overlay, popup, exclude_from_capture, ready),
                         daemon=True).start()
        if not ready.wait(5) or not self.hwnd:
            raise RuntimeError("could not create the output window")

    def _run(self, title, x, y, w, h, overlay, popup, exclude, ready):
        _register_class()
        if overlay:
            style = WS_POPUP | WS_VISIBLE | WS_CLIPCHILDREN
            ex = WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        elif popup:
            style, ex = WS_POPUP | WS_VISIBLE | WS_CLIPCHILDREN, 0
        else:
            style, ex = WS_OVERLAPPEDWINDOW | WS_VISIBLE | WS_CLIPCHILDREN, 0
            r = wt.RECT(0, 0, w, h)                    # w x h is the client area
            user32.AdjustWindowRectEx(ctypes.byref(r), style, False, ex)
            w, h = r.right - r.left, r.bottom - r.top
        hwnd = user32.CreateWindowExW(ex, CLASS_NAME, title, style, x, y, w, h, None, None,
                                      kernel32.GetModuleHandleW(None), None)
        if hwnd:
            self.hwnd = hwnd
            _windows[hwnd] = self
            if overlay:
                user32.SetLayeredWindowAttributes(hwnd, 0, 255, 2)  # LWA_ALPHA, fully opaque
            if exclude:
                user32.SetWindowDisplayAffinity(hwnd, 0x11)  # WDA_EXCLUDEFROMCAPTURE
        ready.set()
        if not hwnd:
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self.closed.set()

    def excluded(self):
        aff = wt.DWORD()
        user32.GetWindowDisplayAffinity(self.hwnd, ctypes.byref(aff))
        return aff.value == 0x11

    def close(self):
        if self.hwnd and not self.closed.is_set():
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
