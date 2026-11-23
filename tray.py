#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 托盘图标 + 气泡通知（纯 ctypes，不需要第三方库）

用法::

    tray = Tray(tooltip="抖音账号监控", on_command=lambda cmd: ...)
    tray.notify("抖音更新", "花药 发布了 1 个新作品")
    tray.destroy()

回调（on_command）在托盘自己的线程里执行，参数是 "show" / "toggle" / "quit"，
调用方应把它丢进主线程队列（例如 tkinter 的 queue），不要直接操作界面。
"""
import ctypes
import ctypes.wintypes as wt
import threading

WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO, NIIF_WARNING = 0x01, 0x02
ID_TRAY = 1
ID_SHOW, ID_TOGGLE, ID_QUIT = 1001, 1002, 1003

MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x0002, 0x0100, 0x0080
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uTimeoutOrVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wt.HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT),
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
        ("hIconSm", wt.HICON),
    ]


kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE

user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wt.ATOM
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.LoadIconW.restype = wt.HICON
user32.LoadIconW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.AppendMenuW.restype = wt.BOOL
user32.TrackPopupMenu.argtypes = [
    wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.LPVOID]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL

IDI_APPLICATION = 32512
HWND_MESSAGE = wt.HWND(-3)


class Tray:
    """托盘图标：左键双击显示窗口，右键菜单，气泡通知。"""

    def __init__(self, tooltip="抖音账号监控", on_command=None, title="抖音账号监控"):
        self.tooltip = (tooltip or "抖音账号监控")[:120]
        self.title = (title or self.tooltip)[:60]
        self.on_command = on_command
        self._hwnd = None
        self._nid = None
        self._thread = None
        self._ready = threading.Event()
        self._error = None
        self._alive = False

    # ---- 对外接口 ----
    def start(self, timeout=5.0):
        """启动托盘线程，返回是否成功（失败时 self.error 有原因）"""
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self._alive

    @property
    def error(self):
        return self._error

    def notify(self, title, message, warning=False):
        """弹一条系统通知（气泡）。托盘没起来就返回 False。"""
        if not self._nid:
            return False
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = ID_TRAY
        nid.uFlags = NIF_INFO
        nid.szInfo = (message or "")[:250]
        nid.szInfoTitle = ((title or self.title) or "")[:60]
        nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        nid.uTimeoutOrVersion = 8000
        try:
            return bool(shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))
        except OSError:
            return False

    def destroy(self):
        if self._nid:
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            except OSError:
                pass
            self._nid = None
        if self._hwnd:
            try:
                user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            except OSError:
                pass
            self._hwnd = None
        self._alive = False

    def set_tooltip(self, text):
        if not self._nid:
            return False
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = ID_TRAY
        nid.uFlags = NIF_TIP
        nid.szTip = (text or self.tooltip)[:120]
        try:
            return bool(shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))
        except OSError:
            return False

    # ---- 内部实现 ----
    def _run(self):
        try:
            self._create_window()
            self._add_icon()
            self._alive = True
            self._ready.set()
            self._message_loop()
        except Exception as exc:  # noqa: BLE001 - 托盘失败不能影响主程序
            self._error = str(exc) or exc.__class__.__name__
            self._alive = False
            self._ready.set()

    def _create_window(self):
        self._class_name = "DouYinMonitorTray%s" % id(self)
        self._wndproc = WNDPROC(self._on_message)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = self._class_name
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err not in (1410,):  # 已经注册过就继续
                raise OSError(err, "RegisterClassExW 失败")
        hwnd = user32.CreateWindowExW(
            WS_EX_TOOLWINDOW, self._class_name, self._class_name, 0,
            0, 0, 0, 0, HWND_MESSAGE, None, wc.hInstance, None)
        if not hwnd:
            raise OSError(ctypes.get_last_error(), "CreateWindowExW 失败")
        self._hwnd = hwnd
        self._hicon = user32.LoadIconW(None, wt.LPCWSTR(IDI_APPLICATION))

    def _add_icon(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = ID_TRAY
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._hicon
        nid.szTip = self.tooltip
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            raise OSError(ctypes.get_last_error(), "Shell_NotifyIcon(NIM_ADD) 失败")
        self._nid = nid

    def _message_loop(self):
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _command(self, cmd):
        if not self.on_command:
            return
        try:
            self.on_command(cmd)
        except Exception:  # noqa: BLE001
            pass

    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            event = int(lparam) & 0xFFFF
            if event in (WM_LBUTTONDBLCLK, WM_LBUTTONUP):
                self._command("show")
                return 0
            if event == WM_RBUTTONUP:
                self._popup_menu()
                return 0
        elif msg == WM_COMMAND:
            cmd = int(wparam) & 0xFFFF
            if cmd == ID_SHOW:
                self._command("show")
            elif cmd == ID_TOGGLE:
                self._command("toggle")
            elif cmd == ID_QUIT:
                self._command("quit")
            return 0
        elif msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        elif msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _popup_menu(self):
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        user32.AppendMenuW(menu, MF_STRING, ID_SHOW, "显示主界面")
        user32.AppendMenuW(menu, MF_STRING, ID_TOGGLE, "开始 / 停止监控")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "退出")
        pos = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pos))
        user32.SetForegroundWindow(self._hwnd)
        cmd = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
            pos.x, pos.y, 0, self._hwnd, None)
        user32.PostMessageW(self._hwnd, 0x0000, 0, 0)  # WM_NULL，让菜单正常收起
        user32.DestroyMenu(menu)
        if cmd:
            self._on_message(self._hwnd, WM_COMMAND, cmd, 0)


def available():
    """当前平台是否支持托盘"""
    import sys
    return sys.platform == "win32"