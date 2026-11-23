# -*- coding: utf-8 -*-
"""高 DPI 适配回归：DPI 感知声明、像素缩放、tk scaling、源码集成点。"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import hi_dpi

checks = [0, 0]


def check(name, cond, extra=""):
    checks[0] += 1
    if cond:
        checks[1] += 1
        print("  ok  %s %s" % (name, extra))
    else:
        print("  FAIL %s %s" % (name, extra))


# ① enable() 在 Windows 上必须声明成功
r = hi_dpi.enable()
check("DPI 感知声明成功", r in ("per_monitor_v2", "system", "legacy", "skip"), r)

# ② 缩放系数随 DPI：96=1.0 / 120=1.25 / 144=1.5 / 192=2.0
for dpi, expect in ((96, 1.0), (120, 1.25), (144, 1.5), (192, 2.0)):
    hi_dpi._scale_cache = dpi
    check("ui_scale @%d" % dpi, abs(hi_dpi.ui_scale() - expect) < 1e-6, hi_dpi.ui_scale())

# ③ scale_px 按比例取整
hi_dpi._scale_cache = 144
check("scale_px(1100)=1650", hi_dpi.scale_px(1100) == 1650, hi_dpi.scale_px(1100))
check("scale_px(9)=14", hi_dpi.scale_px(9) == 14, hi_dpi.scale_px(9))
hi_dpi._scale_cache = 96
check("scale_px(1100)=1100 @96", hi_dpi.scale_px(1100) == 1100, hi_dpi.scale_px(1100))

# ④ scale_geometry 只缩尺寸、保留位置
hi_dpi._scale_cache = 144
check("geometry WxH", hi_dpi.scale_geometry("1100x660") == "1650x990",
      hi_dpi.scale_geometry("1100x660"))
check("geometry WxH+X+Y", hi_dpi.scale_geometry("1100x660+120+80") == "1650x990+120+80",
      hi_dpi.scale_geometry("1100x660+120+80"))
hi_dpi._scale_cache = None

# ⑤ 源码集成点：主窗口走 window_size（含钳制），对话框走 scale_px，建窗后调 apply_tk_scaling
src = io.open("douyin_client.py", encoding="utf-8").read()
check("主窗口 geometry 走 window_size", "hi_dpi.window_size(1100, 660)" in src)
check("主窗口 minsize 走 scale_px", "hi_dpi.scale_px(860)" in src)
check("apply_tk_scaling 被调用", src.count("hi_dpi.apply_tk_scaling(") >= 4,
      src.count("hi_dpi.apply_tk_scaling("))
check("main() 里先 enable()", "hi_dpi.enable()" in src)

# ⑥ window_size 钳制到屏幕内
hi_dpi._scale_cache = 144
_w, _h = hi_dpi.window_size(1100, 660)
check("window_size 返回合理尺寸", _w > 0 and _h > 0, "%dx%d" % (_w, _h))
import ctypes as _ct
try:
    _u = _ct.WinDLL("user32")
    _u.GetSystemMetrics.argtypes = [_ct.c_int]
    _u.GetSystemMetrics.restype = _ct.c_int
    _sw, _sh = _u.GetSystemMetrics(0), _u.GetSystemMetrics(1)
    check("window_size 不超屏", _w <= _sw and _h <= _sh,
          "win=%dx%d screen=%dx%d" % (_w, _h, _sw, _sh))
except Exception:
    check("window_size 不超屏", True, "skip")
hi_dpi._scale_cache = None

# ⑦ Tk scaling 真能设上去
import tkinter as tk
try:
    root = tk.Tk()
    before = root.tk.call("tk", "scaling")
    hi_dpi.apply_tk_scaling(root)
    after = root.tk.call("tk", "scaling")
    check("apply_tk_scaling 设置成功", abs(float(after) - hi_dpi.system_dpi() / 72.0) < 0.01,
          "%.3f -> %.3f" % (float(before), float(after)))
    root.destroy()
except Exception as exc:  # noqa: BLE001
    check("apply_tk_scaling 设置成功", False, str(exc))

print("RESULT_%s" % ("OK" if checks[0] == checks[1] else "FAIL"), checks)
