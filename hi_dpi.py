#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高 DPI 适配：让界面在高分屏（125%/150% 缩放的 IPS 屏）上清晰不模糊。

用法::

    import hi_dpi
    hi_dpi.enable()          # 在创建 Tk 窗口之前调用
    ...
    root = tk.Tk()
    hi_dpi.apply_tk_scaling(root)   # 让 Tk 字体按真实 DPI 缩放
    n = hi_dpi.scale_px(1100)       # 像素尺寸按 DPI 缩放（窗口默认大小等）

原理：Tk 程序默认不声明 DPI 感知，Windows 会对整个窗口做位图拉伸，
文字和控件边线在高分 IPS 屏上就会发糊。声明 DPI 感知后窗口按真实像素渲染，
再把 tk scaling 设为真实 DPI，字体大小恢复到设计比例。
"""
import ctypes
import sys

_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
_scale_cache = None


def _user32():
    return ctypes.WinDLL("user32", use_last_error=True)


def _shcore():
    try:
        return ctypes.WinDLL("shcore", use_last_error=True)
    except OSError:
        return None


def enable():
    """声明进程 DPI 感知（在创建任何窗口之前调用，只调一次）。"""
    if not sys.platform.startswith("win"):
        return "skip"
    u = _user32()
    # Win10 1703+：Per-Monitor V2（PER_MONITOR_AWARE_V2 = -4，传 64 位补码）
    try:
        u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u.SetProcessDpiAwarenessContext.restype = ctypes.c_int
        if u.SetProcessDpiAwarenessContext(ctypes.c_void_p(0xFFFFFFFFFFFFFFFC)):
            return "per_monitor_v2"
    except Exception:  # noqa: BLE001
        pass
    # Win8.1+：shcore
    s = _shcore()
    if s is not None:
        try:
            s.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
            s.SetProcessDpiAwareness.restype = ctypes.c_int
            if s.SetProcessDpiAwareness(1):  # SYSTEM_DPI_AWARE
                return "system"
        except Exception:  # noqa: BLE001
            pass
    # Vista+ 兜底
    try:
        u.SetProcessDPIAware.argtypes = []
        u.SetProcessDPIAware.restype = ctypes.c_int
        if u.SetProcessDPIAware():
            return "legacy"
    except Exception:  # noqa: BLE001
        pass
    return "failed"


def system_dpi():
    """当前系统 DPI（主显示器），拿不到就按 96。"""
    global _scale_cache
    if _scale_cache:
        return _scale_cache
    dpi = 96
    try:
        if sys.platform.startswith("win"):
            u = _user32()
            # Win10 1607+
            try:
                u.GetDpiForSystem.argtypes = []
                u.GetDpiForSystem.restype = ctypes.c_uint
                dpi = int(u.GetDpiForSystem())
            except Exception:  # noqa: BLE001
                dc = u.GetDC(None)
                g = ctypes.WinDLL("gdi32")
                g.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
                g.GetDeviceCaps.restype = ctypes.c_int
                dpi = int(g.GetDeviceCaps(dc, 88))  # LOGPIXELSX
                u.ReleaseDC(None, dc)
    except Exception:  # noqa: BLE001
        dpi = 96
    if not dpi or dpi < 96:
        dpi = 96
    _scale_cache = dpi
    return dpi


def ui_scale():
    """UI 缩放系数：96 DPI = 1.0，125% = 1.25，150% = 1.5。"""
    return system_dpi() / 96.0


def scale_px(value):
    """把「按 96 DPI 设计的像素值」换算成当前 DPI 下的像素值。"""
    return int(round(value * ui_scale()))


def window_size(w, h, ratio=0.92):
    """按 DPI 缩放窗口尺寸，并钳制到屏幕工作区的 ratio 以内，避免超出屏幕。

    返回 (width, height) 像素值，可直接给 geometry/minsize 之外的初始尺寸用。
    """
    sw, sh = scale_px(w), scale_px(h)
    try:
        if sys.platform.startswith("win"):
            u = _user32()
            u.GetSystemMetrics.argtypes = [ctypes.c_int]
            u.GetSystemMetrics.restype = ctypes.c_int
            screen_w = u.GetSystemMetrics(0)  # SM_CXSCREEN
            screen_h = u.GetSystemMetrics(1)  # SM_CYSCREEN
            if screen_w > 0 and screen_h > 0:
                sw = min(sw, int(screen_w * ratio))
                sh = min(sh, int(screen_h * ratio))
    except Exception:  # noqa: BLE001
        pass
    return max(sw, 320), max(sh, 240)


def apply_tk_scaling(root):
    """让 Tk 的字体 point→pixel 换算按真实 DPI（字体大小恢复设计比例）。"""
    try:
        root.tk.call("tk", "scaling", system_dpi() / 72.0)
        return True
    except Exception:  # noqa: BLE001
        return False


def scale_geometry(geo):
    """把 "WxH" 或 "WxH+X+Y" 几何串按 DPI 缩放（恢复窗口大小时用）。"""
    import re
    m = re.match(r"^(\d+)x(\d+)((?:[+-]\d+)(?:[+-]\d+)?)?$", str(geo or ""))
    if not m:
        return geo
    w, h = int(m.group(1)), int(m.group(2))
    pos = m.group(3) or ""
    return "%dx%d%s" % (scale_px(w), scale_px(h), pos)
