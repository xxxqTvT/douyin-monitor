# -*- coding: utf-8 -*-
"""只允许运行一个实例：命名互斥体 + 唤回已有窗口

用法（main() 里）：
    import single_instance
    if not single_instance.ensure_single():
        return
拿到互斥体后 handle 保存在模块级变量里，进程活着就一直持有。
"""

import ctypes
import sys

from ctypes import wintypes

MUTEX_NAME = "Local\\DouYinMonitor_SingleInstance"
WINDOW_TITLE = "抖音账号监控客户端"
ERROR_ALREADY_EXISTS = 183

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32 = ctypes.WinDLL("user32", use_last_error=True)

# 句柄是 64 位，不显式声明类型会被截断成 c_int → GetWindowTextLengthW 永远返回 0
_u32.IsWindow.argtypes = [wintypes.HWND]
_u32.IsWindow.restype = wintypes.BOOL
_u32.IsWindowVisible.argtypes = [wintypes.HWND]
_u32.IsWindowVisible.restype = wintypes.BOOL
_u32.IsIconic.argtypes = [wintypes.HWND]
_u32.IsIconic.restype = wintypes.BOOL
_u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_u32.GetWindowTextLengthW.restype = ctypes.c_int
_u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u32.GetWindowTextW.restype = ctypes.c_int
_u32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
_u32.EnumWindows.restype = wintypes.BOOL
_u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.ShowWindow.restype = wintypes.BOOL
_u32.BringWindowToTop.argtypes = [wintypes.HWND]
_u32.BringWindowToTop.restype = wintypes.BOOL
_u32.SetForegroundWindow.argtypes = [wintypes.HWND]
_u32.SetForegroundWindow.restype = wintypes.BOOL
_u32.FlashWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
_u32.FlashWindow.restype = wintypes.BOOL
_u32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
_u32.SwitchToThisWindow.restype = None
_u32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
_u32.MessageBoxW.restype = ctypes.c_int
import os as _os
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

_HANDLE = None          # 模块级持有，进程退出前不释放


def acquire(name=MUTEX_NAME):
    """拿到互斥体返回 True；已有实例返回 False"""
    global _HANDLE
    _k32.CreateMutexW.restype = wintypes.HANDLE
    _k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    handle = _k32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if not handle:
        # 拿不到互斥体（极少见）时按“允许运行”处理，不要因为这里把程序卡住
        return True
    if err == ERROR_ALREADY_EXISTS:
        _k32.CloseHandle(handle)
        return False
    _HANDLE = handle
    return True


def find_window(title_part=WINDOW_TITLE):
    """找已运行实例的主窗口句柄，找不到返回 0"""
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if not _u32.IsWindow(hwnd):
            return True
        length = _u32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _u32.GetWindowTextW(hwnd, buf, length + 1)
        if title_part in buf.value:
            found.append(hwnd)
            return False
        return True

    _u32.EnumWindows(proto(_cb), 0)
    return found[0] if found else 0


def activate(title_part=WINDOW_TITLE):
    """把已运行的窗口还原并切到前台，成功返回 True"""
    hwnd = find_window(title_part)
    if not hwnd:
        return False
    try:
        if _u32.IsIconic(hwnd):
            _u32.ShowWindow(hwnd, 9)        # SW_RESTORE
        else:
            _u32.ShowWindow(hwnd, 5)        # SW_SHOW
        _u32.BringWindowToTop(hwnd)
        if not _u32.SetForegroundWindow(hwnd):
            # 抢不到前台时用 SwitchToThisWindow 兜底（资源管理器双击启动的常见情况）
            try:
                _u32.SwitchToThisWindow(hwnd, True)
            except Exception:  # noqa: BLE001
                pass
        _u32.FlashWindow(hwnd, True)
        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_single(title_part=WINDOW_TITLE, quiet=False):
    """已有实例时唤回它并返回 False（调用方直接 return）"""
    if acquire():
        return True
    ok = activate(title_part)
    if not ok and not quiet:
        try:
            _u32.MessageBoxW(None, "程序已经在运行了，请看一下任务栏或托盘图标。",
                             "已在运行", 0x40)
        except Exception:  # noqa: BLE001
            pass
    return False


def _self_test():
    first = acquire("Local\\DouYinMonitor_Test_%d" % ctypes.windll.kernel32.GetCurrentProcessId())
    second = acquire("Local\\DouYinMonitor_Test_%d" % ctypes.windll.kernel32.GetCurrentProcessId())
    print("acquire first:", first, "second:", second)
    print("find_window:", find_window(), "activate:", activate())


if __name__ == "__main__":
    _self_test()