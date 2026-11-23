# -*- coding: utf-8 -*-
"""
抖音账号监控客户端（图形界面版）
- 可添加多个抖音号（抖音号 / 主页链接 / sec_uid 均可）
- 定时检查每个账号：新视频 / 新图集 -> 内置下载器下载
- 检测开播 -> 自动获取直播拉流地址（FLV / M3U8）并保存为 txt
- 所有日志显示在界面中，同时写入 monitor.log

运行：双击“启动客户端.bat”，或命令行 python douyin_client.py
"""

import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

import douyin_monitor as core
import hi_dpi
import live_record

BASE_DIR = core.app_base_dir()
CONFIG_FILE = os.path.join(BASE_DIR, "client_config.json")
FROZEN = bool(getattr(sys, "frozen", False))
log = core.log


def _log_thread_exception(args):
    """任何后台线程未捕获的异常都写进 monitor.log（窗口版 stderr 看不见）。"""
    try:
        name = getattr(getattr(args, "thread", None), "name", "?")
        text = "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback))
        log.error("后台线程 %s 异常退出：%s", name, text)
    except Exception:  # noqa: BLE001
        pass


threading.excepthook = _log_thread_exception

DEFAULT_CONFIG = {
    "douk_dir": "",          # 旧字段：只作为兼容外部安装的线索
    "data_dir": "",          # 数据目录（空 = 程序目录\data，不可写时用 %LOCALAPPDATA%）
    "poll_interval": 300,       # 账号视频（作品）检测间隔（秒）
    "live_poll_interval": 300,  # 直播检测间隔（秒）；独立调度，可单独调快/调慢
    "full_audit_interval": 1440,  # 全量核账间隔（分钟）；0=只在手动核账时全量
    "full_audit_enabled": True,  # 是否启用自动全量核账（关掉后自动轮次只做增量，更快）
    "row_height": 30,           # 账号列表行高（像素，96 DPI 基准；按 DPI 缩放）
    "auto_start_full": False,   # 启动后自动监控时是否先做一次全量核账
    "monitor_scope": "both",    # 监控内容：both=作品+直播 / posts=只作品 / live=只直播
    "live_separate": True,      # 独立直播轮询（作品列表接口冷却时开播检测照常）
    "poll_pages": 3,          # 每轮最多翻几页作品列表（防止两轮之间发多了漏掉）
    "disk_free_min_mb": 500,   # 磁盘剩余空间低于这个值就先不下载
    "download_existing_on_first_run": False,
    "download_video": True,
    "download_images": True,
    "download_live_url": True,
    "live_check": True,
    "max_posts_per_poll": 5,
    "request_timeout": 20,
    "douk_download_timeout": 900,
    "max_accounts_per_round": 20,
    "state_file": "client_state.json",
    "log_file": "monitor.log",
    "live_url_dir": "直播地址",
    "live_quality": "FULL_HD1",
    "cookie_prompted": False,
    "login_skipped": False,
    "silent_mode": False,
    "auto_monitor": True,
    "clipboard_watch": False,
    "webhook_url": "",
    "accounts": [],
}


LEVEL_CN = {"DEBUG": "细节", "INFO": "信息", "WARNING": "注意",
            "ERROR": "错误", "CRITICAL": "严重"}


class ChineseLevelFormatter(logging.Formatter):
    """界面日志用：把 [INFO]/[WARNING]/[ERROR] 换成 [信息]/[注意]/[错误]。

    文件日志保持英文级别名，方便 grep 和给别处工具读。
    """

    def format(self, record):
        record.levelcn = LEVEL_CN.get(record.levelname, record.levelname)
        return super().format(record)


class QueueHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        try:
            self.q.put(("log", self.format(record)))
        except Exception:
            pass


MIN_INTERVAL = 30
MIN_LIVE_INTERVAL = 10        # 直播检测最快 10 秒（profile/other 单次请求，很轻）
MAX_INTERVAL = 86400


# -*- coding: utf-8 -*-
def human_size(num):
    """字节数 -> 易读的 KB/MB/GB"""
    try:
        num = float(num)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return ("%d B" % int(num)) if unit == "B" else ("%.1f %s" % (num, unit))
        num /= 1024.0
    return "-"


class DirBrowser:
    """内置文件浏览窗口：系统不让打开资源管理器时，也能在软件里查看并打开作品文件"""

    def __init__(self, master, path, title="浏览作品文件", shell_open=None):
        self.master = master
        self.shell_open = shell_open
        self.path = os.path.abspath(path)
        self.items = []
        self.alive = True
        self.owner_title = title

        self.win = tk.Toplevel(master)
        self.win.title("%s — %s" % (title, self.path))
        self.win.geometry("%dx%d" % (hi_dpi.scale_px(1000), hi_dpi.scale_px(600)))
        self.win.minsize(hi_dpi.scale_px(680), hi_dpi.scale_px(400))
        hi_dpi.apply_tk_scaling(self.win)

        top = ttk.Frame(self.win, padding=(8, 8, 8, 4))
        top.pack(fill="x")
        ttk.Label(top, text="目录：").pack(side="left")
        self.path_var = tk.StringVar(value=self.path)
        ttk.Entry(top, textvariable=self.path_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(top, text="复制路径", width=9, command=self.copy_path).pack(side="left")
        ttk.Button(top, text="在资源管理器中打开", width=17,
                   command=self.open_in_shell).pack(side="left", padx=(6, 0))

        bar = ttk.Frame(self.win, padding=(8, 0, 8, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="↑ 上级目录", command=self.go_up).pack(side="left")
        ttk.Button(bar, text="刷新", command=self.refresh).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="打开所选", command=self.open_selected).pack(side="left", padx=(6, 0))
        self.info_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.info_var).pack(side="right")

        body = ttk.Frame(self.win, padding=(8, 0, 8, 4))
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, columns=("size", "mtime", "kind"),
                                 show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="名称")
        self.tree.heading("size", text="大小")
        self.tree.heading("mtime", text="修改时间")
        self.tree.heading("kind", text="类型")
        self.tree.column("#0", width=520, anchor="w")
        self.tree.column("size", width=100, anchor="e", stretch=False)
        self.tree.column("mtime", width=150, anchor="w", stretch=False)
        self.tree.column("kind", width=90, anchor="w", stretch=False)
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("dir", foreground="#15507d")
        self.tree.bind("<Double-1>", lambda e: self.open_selected())
        self.tree.bind("<Return>", lambda e: self.open_selected())

        self.hint_var = tk.StringVar(value="双击文件夹进入，双击文件用系统默认程序打开。")
        ttk.Label(self.win, textvariable=self.hint_var, anchor="w",
                  foreground="#555555").pack(fill="x", padx=10)
        bottom = ttk.Frame(self.win, padding=(8, 4, 8, 8))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="关闭", command=self.close).pack(side="right")

        self.win.bind("<Escape>", lambda e: self.close())
        self.win.bind("<F5>", lambda e: self.refresh())
        self.win.bind("<BackSpace>", lambda e: self.go_up())
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()

    # -------------------------------------------------- 内部
    def _hint(self, text):
        try:
            self.hint_var.set(text)
        except Exception:
            pass

    def _clipboard(self, text):
        try:
            self.master.clipboard_clear()
            self.master.clipboard_append(text)
            return True
        except Exception:
            return False

    # -------------------------------------------------- 操作
    def refresh(self):
        try:
            names = os.listdir(self.path)
        except OSError as exc:
            self._hint("读不到这个目录：%s" % exc)
            self.items = []
            self.tree.delete(*self.tree.get_children())
            return
        dirs, files = [], []
        for name in names:
            full = os.path.join(self.path, name)
            try:
                is_dir = os.path.isdir(full)
            except OSError:
                is_dir = False
            if is_dir:
                dirs.append((name, full))
            else:
                try:
                    size = os.path.getsize(full)
                    mtime = os.path.getmtime(full)
                except OSError:
                    size, mtime = 0, 0.0
                files.append((name, full, size, mtime))
        dirs.sort(key=lambda x: x[0].casefold())
        files.sort(key=lambda x: x[3], reverse=True)

        self.tree.delete(*self.tree.get_children())
        self.items = []
        total = 0
        for name, full in dirs:
            self.items.append((full, True))
            self.tree.insert("", "end", iid=str(len(self.items) - 1), text="📁 " + name,
                             values=("", "", "文件夹"), tags=("dir",))
        for name, full, size, mtime in files:
            total += size
            self.items.append((full, False))
            ext = os.path.splitext(name)[1].lower().lstrip(".")
            self.tree.insert("", "end", iid=str(len(self.items) - 1), text=name,
                             values=(human_size(size),
                                     time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)),
                                     (ext.upper() + " 文件") if ext else "文件"))
        self.path_var.set(self.path)
        try:
            self.win.title("%s — %s" % (self.owner_title, self.path))
        except Exception:
            pass
        self.info_var.set("%d 个文件夹 / %d 个文件 / 共 %s"
                          % (len(dirs), len(files), human_size(total)))
        if not self.items:
            self._hint("这个目录是空的。作品下载完成后就会出现在这里。")
        else:
            self._hint("双击文件夹进入，双击文件用系统默认程序打开。")

    def enter(self, path):
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            self._hint("目录不存在：%s" % path)
            return
        self.path = path
        self.refresh()

    def go_up(self):
        parent = os.path.dirname(self.path.rstrip(os.sep).rstrip("/")) or self.path
        if os.path.normcase(parent) == os.path.normcase(self.path):
            self._hint("已经是最顶层了")
            return
        self.enter(parent)

    def open_selected(self):
        iid = self.tree.focus() or (self.tree.selection() or [""])[0]
        if not iid:
            self._hint("先在上面选中一个文件或文件夹")
            return
        try:
            full, is_dir = self.items[int(iid)]
        except (ValueError, IndexError):
            return
        if is_dir:
            self.enter(full)
        else:
            self.open_file(full)

    def open_file(self, full):
        try:
            os.startfile(full)  # noqa: S606 (Windows)
            self._hint("已用系统默认程序打开：%s" % os.path.basename(full))
        except OSError as exc:
            copied = self._clipboard(full)
            self._hint("打不开这个文件（%s）；%s" % (exc, "路径已复制" if copied else "请手动打开"))
            messagebox.showwarning(
                "打不开文件",
                "系统拒绝打开这个文件：\n%s\n\n%s"
                % (full, "路径已复制到剪贴板。" if copied else "可以手动复制上面的路径。"),
                parent=self.win)

    def open_in_shell(self):
        ok, err = True, ""
        try:
            if self.shell_open:
                ok, err = self.shell_open(self.path)
            else:
                os.startfile(self.path)
        except OSError as exc:
            ok, err = False, str(exc)
        if ok:
            self._hint("已在资源管理器中打开：%s" % self.path)
        else:
            self._hint("资源管理器打不开（%s）。可以在这里直接双击文件打开。" % (err or "被系统拒绝"))

    def copy_path(self):
        full = self.path
        iid = self.tree.focus()
        if iid:
            try:
                full = self.items[int(iid)][0]
            except (ValueError, IndexError):
                full = self.path
        self._hint("路径已复制：%s" % full if self._clipboard(full) else "复制失败")

    def close(self):
        self.alive = False
        try:
            self.win.destroy()
        except Exception:
            pass


class TreeTip:
    """鼠标停在账号昵称上时，用小浮窗显示抖音号（表格里不再单列抖音号）。"""

    def __init__(self, tree, getter, colors, delay=350):
        self.tree = tree
        self.getter = getter
        self.colors = colors or {}
        self.delay = delay
        self.tip = None
        self.after_id = None
        self.text = ""
        self.x = self.y = 0
        tree.bind("<Motion>", self._motion, add="+")
        tree.bind("<Leave>", self._hide, add="+")
        tree.bind("<ButtonPress>", self._hide, add="+")

    def _acc_text(self, event):
        row = self.tree.identify_row(event.y)
        if not row or self.tree.identify_column(event.x) != "#2":   # 昵称列
            return ""
        acc = self.getter(row) or {}
        target = str(acc.get("target") or "").strip()
        return "抖音号：%s" % (target or "（未填写）")

    def _motion(self, event):
        text = self._acc_text(event)
        if text == self.text:
            return
        self.text = text
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None
        if text:
            self.x, self.y = event.x_root, event.y_root
            self.after_id = self.tree.after(self.delay, self._show)

    def _show(self):
        self.after_id = None
        if not self.text:
            return
        c = self.colors
        self.tip = tk.Toplevel(self.tree)
        self.tip.wm_overrideredirect(True)
        tk.Label(self.tip, text=self.text, bg=c.get("card", "#ffffe1"),
                 fg=c.get("text", "#1f2430"), bd=1, relief="solid",
                 padx=8, pady=3).pack()
        self.tip.wm_geometry("+%d+%d" % (self.x + 16, self.y + 20))

    def _cancel(self):
        if self.after_id:
            try:
                self.tree.after_cancel(self.after_id)
            except Exception:  # noqa: BLE001
                pass
            self.after_id = None

    def _hide(self, event=None):
        self._cancel()
        self.text = ""
        if self.tip:
            self.tip.destroy()
            self.tip = None


SCOPE_LABELS = {"both": "作品和直播", "posts": "只监控作品", "live": "只监控直播"}
SCOPE_VALUES = dict((v, k) for k, v in SCOPE_LABELS.items())


class App:
    THEMES = {
        "light": {
            "bg": "#eef1f6", "card": "#ffffff", "border": "#dfe4ec", "text": "#1f2430",
            "muted": "#6b7280", "accent": "#2f6fed", "accent_active": "#1f57c9",
            "header": "#1b2437", "header_text": "#ffffff", "header_sub": "#9fb0c9",
            "ok": "#1f7a33", "warn": "#b45309", "bad": "#c0392b",
            "odd": "#f7f9fc", "select": "#e6efff",
            "btn_active": "#e9eef8", "btn_active_border": "#c9d5ea", "disabled": "#a8b0bd",
            "ghost_hover": "#eef2f8", "primary_disabled": "#a9c4f6",
            "header_btn_fg": "#dbe4f2", "header_line": "#3a4763",
            "header_field": "#27344e", "header_hover_border": "#4d5f85",
            "data_bg": "#f4f6fa", "head_fg": "#4a5568", "sel_fg": "#12305c",
            "scroll": "#e6eaf1", "live_bg": "#fff1f0", "err_bg": "#fff7e6",
            "paused": "#9aa0a6", "waiting": "#6b7280",
            "ok_bg": "#eaf7ee", "busy_bg": "#e8f0fe",
            "log_bg": "#fbfcfe", "row_hover": "#eef2f8",
        },
        "dark": {
            "bg": "#12161d", "card": "#1a202b", "border": "#2a3340", "text": "#e7ecf4",
            "muted": "#9aa7ba", "accent": "#4c8dff", "accent_active": "#3a76e0",
            "header": "#0d1117", "header_text": "#f4f8ff", "header_sub": "#8ea1bd",
            "ok": "#5ecf86", "warn": "#e0a33e", "bad": "#f07171",
            "odd": "#1f2733", "select": "#2a3b57",
            "btn_active": "#26303e", "btn_active_border": "#3a4759", "disabled": "#66707f",
            "ghost_hover": "#232b38", "primary_disabled": "#39527d",
            "header_btn_fg": "#cdd8ea", "header_line": "#2c3546",
            "header_field": "#161c26", "header_hover_border": "#42506a",
            "data_bg": "#161c26", "head_fg": "#aebbd0", "sel_fg": "#e4edff",
            "scroll": "#2a3340", "live_bg": "#3a2026", "err_bg": "#3a2f1c",
            "paused": "#7c8697", "waiting": "#96a3b6",
            "ok_bg": "#163122", "busy_bg": "#1c2b45",
            "log_bg": "#141a23", "row_hover": "#232b38",
        },
    }

    COLORS = THEMES["light"]

    def __init__(self, root):
        self.root = root
        self.cfg = self.load_config()
        self.dl_outer = None
        self.live_outer = None
        self.log_outer = None
        self.live_tree = None
        self.live_sum_var = None
        self.check_lock = threading.Lock()
        self._audit_gate = threading.Lock()
        self._audit_busy = False
        self.dl_kinds = dict()
        self._log_n = 0
        self._save_fails = 0
        self.dl_files = dict()
        self.dl_meta = dict()
        self.dl_tree = None
        self.dl_sum_var = None
        self.dl_visible = False
        self.dl_hidden = False      # 用户点了「✕」关闭面板，之后自动出现也不再打扰
        self.count_var = tk.StringVar(value="")
        self.platform_box = None
        self.job_running = False
        self.live_cycle_count = 0
        self.live_interval_var = tk.IntVar(value=int(self.cfg.get("live_poll_interval") or 60))
        self.live_separate_var = tk.BooleanVar(value=bool(self.cfg.get("live_separate", True)))
        # 这两个开关现在只放在「设置」菜单里（主页面不再占位置）
        self.log_warn_only_var = tk.BooleanVar(value=False)
        self.theme = str(self.cfg.get("theme") or "light").lower()
        self.colors = dict(self.THEMES.get(self.theme, self.THEMES["light"]))
        self.COLORS = self.colors
        self.live_rows = {}
        self.live_visible = False
        self.live_hidden = False    # 用户点了「✕」关闭面板
        self.live_info = {}      # key -> 最近一次直播检测结果（含拉流地址，供手动开录/继续）
        self.cookie_badges = {}
        self.log_warn_only = False
        self.log_autoscroll = True
        # 日志面板默认只显示「对用户有意义」的事件：检测/下载/直播录制。
        # 完整日志仍然逐行写 monitor.log，需要排查时勾「详细日志」即可在面板看到全部。
        self.log_summary_only = True
        self.log_detail_var = tk.BooleanVar(value=False)
        self.platform_var = tk.StringVar(
            value=core.platform_label(self.default_platform()))
        self.tray_var = tk.BooleanVar(value=bool(self.cfg.get("tray_enabled", True)))
        self.tray = None             # 托盘图标对象（setup_tray 里创建）
        self.tray_note = ""          # 托盘状态说明（写日志用）
        self.root.after(300, self.setup_tray)   # 启动稍缓一拍，不挡主窗口出现
        self.autostart_var = tk.BooleanVar(value=bool(self.cfg.get("auto_start", False)))
        try:
            import live_record
            self.live_mgr = live_record.RecorderManager(
                dest_dir=core.live_record_dir(self.cfg), logger=log)
        except Exception as exc:  # noqa: BLE001
            log.warning("直播录制管理器初始化失败（直播将不会录制）：%s", exc)
            self.live_mgr = None
        self.accounts = self.cfg.get("accounts", [])
        self._normalize_accounts()
        # 上次关闭时残留的「监控中/直播中」状态，启动时统一复位
        for acc in self.accounts:
            acc["state"] = "待监控" if acc.get("enabled", True) else "已暂停"
            acc["last_check"] = ""
        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.monitor_thread = None
        self.next_live_ts = 0.0       # 直播轮下次触发时间（状态栏显示）
        self.live_cycle_count = 0     # 独立直播轮计数
        self.dy = None
        self.clients = {}                 # 按平台复用的接口对象（DouYin / TikTok）
        self.dy_lock = threading.Lock()
        self._cookie_verify = {}          # 平台 -> (验证是否通过, 说明)
        self.run_cfg = {}
        self.state = self.load_state()
        self._row_seq = 0
        self._normalize_accounts()
        self.cycle_count = 0
        self.next_check_ts = 0.0
        self.history_busy = False
        self.backfill_busy = False   # 「补下载最近 3 天」是否在跑
        self.audit_running = False   # 「历史作品全量核账」是否在跑
        self._state_lock = threading.Lock()    # 状态写盘串行化（监控线程 / 界面线程都写）
        self.dl_total_items = 0
        self.dl_total_files = 0
        self.dl_total_size = 0
        self.session = None          # 本次监控的统计（开始时间/轮次/下载记录）
        self.session_shown = False
        self.status_override = ("", 0.0)   # 临时状态栏提示（如「已打开 xxx」）
        self.browser_win = None            # 内置文件浏览窗口（资源管理器打不开时的兜底）
        self.link_win = None               # 「单独下载链接」窗口
        self.link_busy = False
        self.watch_after = None            # 后台监听剪贴板的定时器
        self.watch_seen = {}               # 已处理链接（去重 + 过期清理）
        self.watch_count = 0               # 后台自动下载过的作品数
        self.watch_pending = []            # 正在下载时排队的链接
        self.watch_info_var = None
        self.login_win = None              # 「扫码登录」窗口
        self.login_platform = "douyin"     # 当前扫码登录的是哪个平台
        self.login_busy = False
        self.login_stop = threading.Event()
        self.login_status_var = None

        _ww, _wh = hi_dpi.window_size(1100, 660)
        root.title("抖音账号监控客户端")
        root.geometry("%dx%d" % (_ww, _wh))
        root.minsize(hi_dpi.scale_px(860), hi_dpi.scale_px(520))
        hi_dpi.apply_tk_scaling(root)

        self.build_ui()
        self.refresh_tree()
        try:
            self.root.update()   # 让窗口先完成映射：表格右键菜单要用到真实行几何
        except Exception:  # noqa: BLE001
            pass
        self.pump_queue()
        self.root.after(1000, self.tick_status)
        log.info("当前 Cookie：%s", core.cookie_summary(self.cfg))
        plat = self.default_platform()
        if not self.cookie_ready(plat) and not self.cfg.get("login_skipped"):
            if self.silent():
                log.info("静默模式：不自动打开浏览器登录；需要登录时点「设置 → 账号与登录」")
            else:
                self.root.after(600, lambda: self.start_browser_login(plat, auto=True))
        elif not self.cookie_ready(plat):
            log.info("还没有登录 %s Cookie：可点「设置 → 账号与登录 → %s 扫码登录…」",
                     core.platform_label(plat), core.platform_label(plat))
        self.root.after(1500, self.resolve_missing_nicknames)
        if self.silent():
            log.info("静默模式已开启：启动后自动最小化，不弹提示框（作品照常下载，日志里能看到）")
            self.root.after(300, self.root.iconify)
        if self.clipboard_watch_on():
            self.root.after(1200, self.start_clipboard_watch)
        # 启动后自动开始监控（等界面和托盘就绪再开，避免和登录/Cookie 检查抢跑）
        if bool(self.cfg.get("auto_monitor", True)):
            self.root.after(1800, self.auto_start_monitor)

        # 日志：界面 + 文件
        log.setLevel(logging.INFO)
        log.handlers[:] = []
        qh = QueueHandler(self.q)
        qh.setFormatter(ChineseLevelFormatter("%(asctime)s [%(levelcn)s] %(message)s", "%H:%M:%S"))
        log.addHandler(qh)
        try:
            fh = logging.FileHandler(os.path.join(BASE_DIR, "monitor.log"), encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
            log.addHandler(fh)
        except Exception:
            pass

        if self.accounts:
            log.info("客户端已启动，已加载 %d 个账号。点击「开始监控」开始检测更新。", len(self.accounts))
        else:
            log.info("客户端已启动。请添加要监控的抖音号，然后点击「开始监控」。")
        if getattr(self, "_need_save_urls", False):
            self.save_config()
        self._migrate_history_auto()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------ UI
    def menu_options(self):
        """菜单统一配色（跟界面同一套 COLORS）"""
        return {
            "tearoff": False,
            "bd": 0,
            "relief": "flat",
            "activeborderwidth": 0,
            "bg": self.COLORS["card"],
            "fg": self.COLORS["text"],
            "activebackground": self.COLORS["select"],
            "activeforeground": "#12305c",
            "disabledforeground": "#a8b0bd",
            "selectcolor": self.COLORS["card"],
            "font": ("Microsoft YaHei UI", 9),
        }

    def make_menu(self, parent=None, **extra):
        opts = self.menu_options()
        opts.update(extra)
        return tk.Menu(parent if parent is not None else self.root, **opts)

    def build_menubar(self):
        """左上角「设置」菜单：分组清晰、配色跟主界面一致"""
        menubar = self.make_menu()

        settings = self.make_menu(menubar)

        # ---------- 二级分类：账号 / 监控 / 下载 / 直播 / 界面 / 维护 ----------
        # 规则：设置 → 分类 → 具体项，最多两层；同类项用分隔线隔开，不再套第三层子菜单。
        # 每个分类里的项按「先动作、后开关、再目录」排。

        # ---- 账号与登录 ----
        account = self.make_menu(settings)
        account.add_command(label="添加账号…", command=self.add_account)
        account.add_command(label="添加 TikTok 账号…",
                            command=lambda: self.add_account("tiktok"))
        account.add_command(label="打开所选账号主页", command=self.open_selected_profile)
        account.add_separator()
        account.add_command(label="抖音扫码登录…",
                            command=lambda: self.start_browser_login("douyin", auto=False))
        account.add_command(label="TikTok 扫码登录…",
                            command=lambda: self.start_browser_login("tiktok", auto=False))
        account.add_separator()
        account.add_command(label="更改抖音 Cookie…",
                            command=lambda: self.change_cookie("douyin"))
        account.add_command(label="更改 TikTok Cookie…",
                            command=lambda: self.change_cookie("tiktok"))
        account.add_command(label="TikTok 代理…", command=self.set_tiktok_proxy)
        account.add_separator()
        for key, name in core.PLATFORMS.items():
            account.add_radiobutton(label="默认平台：%s" % name, value=name,
                                    variable=self.platform_var,
                                    command=self.on_platform_change)
        settings.add_cascade(label="账号与登录", menu=account)

        # ---- 监控与通知 ----
        monitor = self.make_menu(settings)
        monitor.add_command(label="刷新所选账号", command=self.refresh_selected)
        monitor.add_separator()
        monitor.add_checkbutton(label="启动软件后自动开始监控",
                                variable=self.automon_var, command=self.on_automon_change)
        monitor.add_checkbutton(label="自动全量核账",
                                variable=self.full_audit_enabled_var,
                                command=self.on_full_audit_enabled_change)
        monitor.add_checkbutton(label="开始监控时核账历史作品",
                                variable=self.audit_start_var,
                                command=self.on_auditstart_change)
        monitor.add_checkbutton(label="每轮检查都核账历史作品",
                                variable=self.auditevery_var,
                                command=self.on_auditevery_change)
        monitor.add_checkbutton(label="静默模式",
                                variable=self.silent_var, command=self.on_silent_change)
        monitor.add_checkbutton(label="桌面通知",
                                variable=self.notify_var, command=self.on_notify_change)
        monitor.add_checkbutton(label="后台监听剪贴板",
                                variable=self.watch_var, command=self.on_watch_change)
        settings.add_cascade(label="监控与通知", menu=monitor)

        # ---- 下载 ----
        dl = self.make_menu(settings)
        dl.add_command(label="单独下载链接…", command=self.open_link_dialog)
        dl.add_command(label="补下载最近 3 天漏掉的作品…", command=self.backfill_missing)
        dl.add_command(label="下载所选账号的历史作品…", command=self.download_history)
        dl.add_command(label="核账全部历史作品…",
                       command=lambda: self.start_audit(trigger="菜单核账",
                                                        dry_run=True))
        dl.add_separator()
        dl.add_checkbutton(label="下载视频", variable=self.dlvideo_var,
                           command=self.on_download_kind_change)
        dl.add_checkbutton(label="下载图集", variable=self.dlimg_var,
                           command=self.on_download_kind_change)
        dl.add_separator()
        dl.add_command(label="默认保存位置…", command=self.set_default_save_dir)
        dl.add_command(label="所选账号单独设置位置…", command=self.set_save_dir)
        dl.add_command(label="恢复默认保存位置", command=self.reset_default_save_dir)
        dl.add_separator()
        dl.add_command(label="历史下载保存位置…", command=self.set_history_save_dir)
        dl.add_command(label="恢复默认历史下载位置",
                       command=self.reset_history_save_dir)
        dl.add_command(label="整理历史下载…",
                       command=self.migrate_history_now)
        dl.add_separator()
        dl.add_command(label="打开作品下载目录", command=self.open_download_dir)
        dl.add_command(label="按文件夹浏览作品",
                       command=self.browse_download_files)
        dl.add_separator()
        dl.add_command(label="清除下载记录…", command=self.clear_download_records)
        settings.add_cascade(label="下载", menu=dl)

        # ---- 直播 ----
        live = self.make_menu(settings)
        live.add_checkbutton(label="直播自动录制",
                             variable=self.rec_var, command=self.on_live_record_change)
        live.add_checkbutton(label="直播独立轮询",
                             variable=self.live_separate_var,
                             command=self.on_live_separate_change)
        live.add_separator()
        for key, name in core.LIVE_QUALITY_LABELS:
            hint = core.LIVE_QUALITY_HINTS.get(key) or ""
            label = "录制画质：%s（%s）" % (name, hint) if hint else "录制画质：%s" % name
            live.add_radiobutton(label=label, value=key,
                                 variable=self.quality_var, command=self.on_quality_change)
        live.add_separator()
        live.add_command(label="所选账号：录制直播",
                         command=lambda: self.set_live_record_selected(True))
        live.add_command(label="所选账号：不录直播",
                         command=lambda: self.set_live_record_selected(False))
        live.add_command(label="所选账号：跟随全局设置",
                         command=lambda: self.set_live_record_selected(None))
        live.add_separator()
        live.add_command(label="打开直播回放目录", command=self.open_record_dir)
        settings.add_cascade(label="直播", menu=live)

        # ---- 界面与窗口 ----
        look = self.make_menu(settings)
        self.theme_var = tk.StringVar(value=self.theme)
        look.add_radiobutton(label="浅色", value="light", variable=self.theme_var,
                             command=lambda: self.apply_theme("light"))
        look.add_radiobutton(label="深色", value="dark", variable=self.theme_var,
                             command=lambda: self.apply_theme("dark"))
        look.add_separator()
        look.add_command(label="显示「直播录制」面板", command=self.show_live_panel)
        look.add_command(label="显示「本次下载」面板", command=self.show_download_panel)
        look.add_separator()
        look.add_radiobutton(label="关闭窗口：隐藏到托盘", value="minimize",
                             variable=self.close_var, command=self.on_close_action_change)
        look.add_radiobutton(label="关闭窗口：退出程序", value="exit",
                             variable=self.close_var, command=self.on_close_action_change)
        look.add_separator()
        look.add_separator()
        look.add_command(label="账号列表行高…", command=self.set_row_height)
        look.add_checkbutton(label="托盘图标", variable=self.tray_var,
                             command=self.on_tray_change)
        look.add_checkbutton(label="开机自启", variable=self.autostart_var,
                             command=self.on_autostart_change)
        settings.add_cascade(label="界面与窗口", menu=look)

        # ---- 数据与维护 ----
        maint = self.make_menu(settings)
        maint.add_command(label="打开数据目录", command=self.open_data_dir)
        maint.add_command(label="打开日志文件",
                          command=lambda: self.open_path(self.cfg["log_file"]))
        maint.add_separator()
        maint.add_command(label="诊断作品计数…",
                          command=self.diagnose_count)
        maint.add_command(label="清理旧作品…", command=self.clean_old_files)
        maint.add_command(label="自动清理…", command=self.set_auto_clean)
        settings.add_cascade(label="数据与维护", menu=maint)

        menubar.add_cascade(label="设置", menu=settings)
        self.settings_menu = settings
        self.root.config(menu=menubar)

    def open_settings_menu(self):
        """左上角「设置 ▼」按钮：弹出和菜单栏同一份设置菜单"""
        menu = getattr(self, "settings_menu", None)
        if menu is None:
            return
        try:
            menu.tk_popup(self.root.winfo_rootx() + 24,
                          self.root.winfo_rooty() + 64)
            menu.grab_release()
        except Exception:
            log.debug("打开设置菜单失败", exc_info=True)

    def on_quality_change(self):
        key = self.quality_var.get() or "FULL_HD1"
        self.cfg["live_quality"] = key
        self.save_config()
        log.info("直播画质已设置为：%s（%s）", key, core.LIVE_QUALITY_NAMES.get(key, key))

    def build_ui(self):
        self.quality_var = tk.StringVar(value=self.cfg.get("live_quality", "FULL_HD1"))
        self.silent_var = tk.BooleanVar(value=bool(self.cfg.get("silent_mode")))
        self.watch_var = tk.BooleanVar(value=bool(self.cfg.get("clipboard_watch")))
        self.rec_var = tk.BooleanVar(value=bool(self.cfg.get("live_record", True)))
        self.close_var = tk.StringVar(value=self.close_action())
        self.automon_var = tk.BooleanVar(value=bool(self.cfg.get("auto_monitor", True)))
        self.notify_var = tk.BooleanVar(value=bool(self.cfg.get("notify_enabled", True)))
        self.dlvideo_var = tk.BooleanVar(value=bool(self.cfg.get("download_video", True)))
        self.dlimg_var = tk.BooleanVar(value=bool(self.cfg.get("download_images", True)))
        self.auditevery_var = tk.BooleanVar(
            value=bool(self.cfg.get("audit_every_round", True)))
        self.full_audit_enabled_var = tk.BooleanVar(
            value=bool(self.cfg.get("full_audit_enabled", True)))
        self.audit_start_var = tk.BooleanVar(
            value=bool(self.cfg.get("audit_on_start", True)))
        self.theme = "dark" if str(self.cfg.get("theme") or "light").lower() == "dark" else "light"
        self.colors = dict(self.THEMES[self.theme])
        self.COLORS = self.colors
        self.platform_var.set(core.platform_label(self.default_platform()))
        self.live_rows = {}
        self.live_visible = False
        self.cookie_badges = {}
        self.setup_styles()
        self.build_menubar()
        c = self.colors

        # ---------------- 顶部：深色标题栏（设置入口 / 平台切换 / Cookie 指示灯） ----------------
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(14, 9, 14, 9))
        header.pack(fill="x")

        hrow = ttk.Frame(header, style="Header.TFrame")
        hrow.pack(fill="x")
        # 设置只保留菜单栏一处入口（原先这里还有一个「设置 ▾」按钮，属于重复入口）
        ttk.Label(hrow, text="抖音 / TikTok 账号监控",
                  style="HeaderTitle.TLabel").pack(side="left")

        badges = ttk.Frame(hrow, style="Header.TFrame")
        badges.pack(side="right")
        for plat in ("douyin", "tiktok"):
            lbl = ttk.Label(badges, text="", style="HeaderBadge.TLabel")
            lbl.pack(side="left", padx=(10, 0))
            self.cookie_badges[plat] = lbl
        self.platform_box = ttk.Combobox(
            hrow, textvariable=self.platform_var, state="readonly", width=7,
            style="Header.TCombobox",
            values=[core.platform_label(p) for p in core.PLATFORMS])
        self.platform_box.pack(side="right", padx=(10, 10))
        self.platform_box.bind("<<ComboboxSelected>>",
                               lambda _e: self.on_platform_change())
        ttk.Label(hrow, text="平台", style="HeaderSub.TLabel").pack(side="right")

        hrow2 = ttk.Frame(header, style="Header.TFrame")
        hrow2.pack(fill="x", pady=(7, 0))
        ttk.Label(hrow2, text="数据目录", style="HeaderSub.TLabel").pack(side="left")
        self.douk_var = tk.StringVar(value=self.current_douk_dir())
        dir_lbl = ttk.Label(hrow2, textvariable=self.douk_var,
                            style="HeaderBadge.TLabel",
                            cursor="hand2")
        dir_lbl.pack(side="left", padx=(8, 8), fill="x", expand=True)
        # 目录文字本身就是入口（原先旁边还有一个「打开」按钮，和菜单里重复）
        dir_lbl.bind("<Button-1>", lambda _e: self.open_data_dir())

        # ---------------- 最底部：状态栏 ----------------
        status = ttk.Frame(self.root, style="Status.TFrame", padding=(12, 4))
        status.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar(value="未开始")
        ttk.Label(status, textvariable=self.status_var,
                  style="Status.TLabel").pack(side="left")
        # 左下角：下次作品检查 / 下次直播检查 倒计时（tick_status 每秒刷新）
        self.countdown_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.countdown_var,
                  style="Status.TLabel").pack(side="left", padx=(16, 0))
        ttk.Label(status, text="作品与登录信息都在数据目录里",
                  style="Status.TLabel").pack(side="right")

        # ---------------- 底部控制卡片 ----------------
        bottom = ttk.Frame(self.root, style="Card.TFrame", padding=(12, 8))
        bottom.pack(side="bottom", fill="x", padx=12, pady=(0, 8))
        row1 = ttk.Frame(bottom, style="Card.TFrame")
        row1.pack(fill="x")
        ttk.Label(row1, text="检查间隔", style="Body.TLabel").pack(side="left")
        self.interval_var = tk.IntVar(value=int(self.cfg["poll_interval"]))
        self.interval_spin = ttk.Spinbox(row1, from_=MIN_INTERVAL, to=MAX_INTERVAL,
                                         increment=30, width=6,
                                         textvariable=self.interval_var)
        self.interval_spin.pack(side="left", padx=(4, 2))
        self.interval_spin.bind("<FocusOut>", self.on_interval_commit)
        self.interval_spin.bind("<Return>", self.on_interval_commit)
        ttk.Label(row1, text="秒", style="Body.TLabel").pack(side="left")
        self.live_var = tk.BooleanVar(value=bool(self.cfg["live_check"]))
        ttk.Checkbutton(row1, text="检测直播",
                        variable=self.live_var).pack(side="left", padx=(14, 0))
        ttk.Label(row1, text="直播间隔", style="Body.TLabel").pack(side="left", padx=(10, 0))
        self.live_interval_spin = ttk.Spinbox(row1, from_=MIN_LIVE_INTERVAL, to=MAX_INTERVAL,
                                              increment=10, width=6,
                                              textvariable=self.live_interval_var)
        self.live_interval_spin.pack(side="left", padx=(4, 2))
        ttk.Label(row1, text="秒", style="Body.TLabel").pack(side="left")
        ttk.Label(row1, text="全量核账间隔", style="Body.TLabel").pack(side="left", padx=(10, 0))
        self.full_audit_var = tk.IntVar(value=int(self.cfg.get("full_audit_interval", 1440)))
        self.full_audit_spin = ttk.Spinbox(row1, from_=0, to=10080, increment=60, width=6,
                                          textvariable=self.full_audit_var)
        self.full_audit_spin.pack(side="left", padx=(4, 2))
        self.full_audit_spin.bind("<FocusOut>", self.on_full_audit_commit)
        self.full_audit_spin.bind("<Return>", self.on_full_audit_commit)
        ttk.Label(row1, text="分，0=不全量", style="Body.TLabel").pack(side="left")

        row3 = ttk.Frame(bottom, style="Card.TFrame")
        row3.pack(fill="x", pady=(6, 0))
        self.auto_full_var = tk.BooleanVar(value=bool(self.cfg.get("auto_start_full", False)))
        ttk.Checkbutton(row3, text="启动后自动监控先全量核账",
                        variable=self.auto_full_var,
                        command=self.on_auto_full_change).pack(side="left")
        ttk.Label(row3, text="监控内容", style="Body.TLabel").pack(side="left", padx=(16, 0))
        self.scope_var = tk.StringVar(value=SCOPE_LABELS.get(
            str(self.cfg.get("monitor_scope") or "both"), "作品和直播"))
        self.scope_box = ttk.Combobox(row3, textvariable=self.scope_var, width=10,
                                      state="readonly",
                                      values=("作品和直播", "只监控作品", "只监控直播"))
        self.scope_box.pack(side="left", padx=(4, 0))
        self.scope_box.bind("<<ComboboxSelected>>", self.on_scope_change)

        row2 = ttk.Frame(bottom, style="Card.TFrame")
        row2.pack(fill="x", pady=(8, 0))
        self.start_btn = ttk.Button(row2, text="▶ 开始监控", style="Primary.TButton",
                                    command=lambda: self.toggle_monitor("按钮"))
        self.start_btn.pack(side="left")
        ttk.Button(row2, text="单独下载链接…",
                   command=self.open_link_dialog).pack(side="left", padx=(8, 0))
        ttk.Label(row2, text="提示：账号表右键可操作，左上角「设置」里可切换深浅色",
                  style="Hint.TLabel").pack(side="right")

        # ---------------- 中部：左右两栏（可拖动分隔条） ----------------
        body = ttk.Frame(self.root, padding=(12, 6, 12, 0))
        body.pack(fill="both", expand=True)
        pane = ttk.PanedWindow(body, orient="horizontal")
        pane.pack(fill="both", expand=True)

        left = ttk.Frame(pane, style="Card.TFrame", padding=(10, 8))
        right = ttk.Frame(pane)
        pane.add(left, weight=3)
        pane.add(right, weight=2)
        # 拖动分隔栏时记住位置，并保证两侧都不会被拖到看不见
        self.pane = pane
        self.left_pane = left
        self.right_pane = right
        pane.bind("<ButtonRelease-1>", self.on_sash_changed, add="+")
        pane.bind("<B1-Motion>", self.on_sash_motion, add="+")
        pane.bind("<Configure>", lambda e: self.root.after_idle(self.repaint_sides), add="+")

        head = ttk.Frame(left, style="Card.TFrame")
        head.pack(fill="x", pady=(0, 6))
        ttk.Label(head, text="账号列表", style="Title.TLabel").pack(side="left")
        ttk.Label(head, textvariable=self.count_var,
                  style="Hint.TLabel").pack(side="right")

        tree_wrap = ttk.Frame(left, style="Card.TFrame")
        cols = ("platform", "nickname", "state", "live", "refresh", "save", "last")
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings",
                                 selectmode="extended")
        # 列宽基准（96 DPI），实际按 DPI 缩放。按内容长短重新规划：
        # - 昵称固定宽度（不再 stretch）：内容一般几个字，固定后不再膨胀出一大片空白；
        # - 状态列加宽：能完整放下「出错:接口限流」等较长文案；
        # - 仅最右两列（保存位置/最近检查）参与 stretch：它们是内容长度不定的列，
        #   窗口变宽时由它们吸收多余空间，中间各列保持稳定，看起来更规整。
        # 固定列 width 合计 298 + 弹性列 min 合计 100 = 398(96DPI) < 表格最小宽约 425，
        # 150% DPI 下同比放大后仍留有余量，拖到最窄也能完整看全 7 列、不出横向滚动条。
        for col, txt, width, min_w, stretch in (
                ("platform", "平台", 42, 40, False),
                ("nickname", "昵称", 96, 60, False),
                ("state", "状态", 66, 56, False),
                ("live", "监控", 52, 46, False),
                ("refresh", "刷新", 50, 46, False),
                ("save", "保存位置", 82, 50, True),
                ("last", "最近检查", 86, 50, True)):
            self.tree.heading(col, text=txt, anchor="w")
            self.tree.column(col, width=hi_dpi.scale_px(width),
                             minwidth=hi_dpi.scale_px(min_w), anchor="w", stretch=stretch)
        # 账号行颜色：直播红、监控中绿、刷新/解析中蓝、限流/出错橙、暂停灰
        self.tree.tag_configure("live", foreground=c["bad"], background=c["live_bg"])
        self.tree.tag_configure("off", foreground=c["paused"])
        self.tree.tag_configure("ok", foreground=c["ok"])
        self.tree.tag_configure("busy", foreground=c["accent"], background=c["busy_bg"])
        self.tree.tag_configure("warn", foreground=c["warn"], background=c["err_bg"])
        self.tree.tag_configure("wait", foreground=c["waiting"])
        self.acc_tip = TreeTip(self.tree, self.account_by_iid, self.colors)
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<Button-3>", self.on_tree_context)
        self.tree.bind("<Double-1>", self.on_tree_double)
        vsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)

        # 按钮先按 bottom 打包占住高度，再让表格占剩余空间；否则窗口一矮，
        # Treeview(expand=True) 会先把空间吃光，最后一行按钮被压成一条缝。
        btns2 = ttk.Frame(left, style="Card.TFrame")
        btns2.pack(side="bottom", fill="x", pady=(6, 0))
        self.history_btn = ttk.Button(btns2, text="下载历史视频", command=self.download_history)
        self.history_btn.pack(side="left")
        ttk.Button(btns2, text="清除下载记录",
                   command=self.clear_download_records).pack(side="left", padx=4)
        btns = ttk.Frame(left, style="Card.TFrame")
        btns.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Button(btns, text="添加账号", command=self.add_account).pack(side="left")
        ttk.Button(btns, text="删除所选", command=self.remove_accounts).pack(side="left", padx=4)
        ttk.Button(btns, text="暂停 / 启用", command=self.toggle_enabled).pack(side="left")
        ttk.Button(btns, text="重新解析", command=self.resolve_selected).pack(side="left", padx=4)
        tree_wrap.pack(fill="both", expand=True)

        # ---- 右栏：直播录制 / 本次下载 / 运行日志（下载或开播时才出现上面两块） ----
        self.live_outer = ttk.Frame(right, style="Card.TFrame", padding=(10, 8))
        lhead = ttk.Frame(self.live_outer, style="Card.TFrame")
        lhead.pack(fill="x")
        ttk.Label(lhead, text="直播录制", style="Title.TLabel").pack(side="left")
        self.live_sum_var = tk.StringVar(value="")
        ttk.Label(lhead, textvariable=self.live_sum_var,
                  style="Hint.TLabel").pack(side="left", padx=(8, 0))
        self.live_close_btn = ttk.Button(lhead, text="✕", style="Ghost.TButton",
                                         width=2, command=self.close_live_panel)
        self.live_close_btn.pack(side="right")
        live_cols = ("nick", "quality", "res", "duration", "size", "state", "act")
        self.live_tree = ttk.Treeview(self.live_outer, columns=live_cols,
                                      show="headings", height=4)
        for col, txt, width in (("nick", "账号", 92), ("quality", "画质", 86),
                                ("res", "分辨率", 74), ("duration", "时长", 58),
                                ("size", "大小", 66), ("state", "状态", 86),
                                ("act", "操作", 46)):
            self.live_tree.heading(col, text=txt, anchor="w")
            self.live_tree.column(col, width=width, anchor="w")
        self.live_tree.tag_configure("rec", foreground=c["bad"])
        self.live_tree.tag_configure("err", foreground=c["warn"])
        self.live_tree.tag_configure("done", foreground=c["muted"])
        self.live_tree.bind("<Button-1>", self.on_live_click)
        self.live_tree.configure(cursor="hand2")
        self.live_tree.pack(fill="x", pady=(6, 0))
        lbtns = ttk.Frame(self.live_outer, style="Card.TFrame")
        lbtns.pack(fill="x", pady=(6, 0))
        ttk.Button(lbtns, text="停止全部录制",
                   command=self.stop_all_records).pack(side="left")
        ttk.Button(lbtns, text="打开录像目录",
                   command=self.open_record_dir).pack(side="left", padx=4)

        self.dl_outer = ttk.Frame(right, style="Card.TFrame", padding=(10, 8))
        dhead = ttk.Frame(self.dl_outer, style="Card.TFrame")
        dhead.pack(fill="x")
        ttk.Label(dhead, text="本次下载", style="Title.TLabel").pack(side="left")
        if self.dl_sum_var is None:
            self.dl_sum_var = tk.StringVar(value="")
        ttk.Label(dhead, textvariable=self.dl_sum_var,
                  style="Hint.TLabel").pack(side="left", padx=(8, 0))
        self.dl_close_btn = ttk.Button(dhead, text="✕", style="Ghost.TButton",
                                       width=2, command=self.close_download_panel)
        self.dl_close_btn.pack(side="right")
        dl_cols = ("when", "nick", "kind", "desc")
        self.dl_tree = ttk.Treeview(self.dl_outer, columns=dl_cols,
                                    show="headings", height=5)
        for col, txt, width in (("when", "时间", 62), ("nick", "账号", 82),
                                ("kind", "类型", 56), ("desc", "作品", 220)):
            self.dl_tree.heading(col, text=txt, anchor="w")
            self.dl_tree.column(col, width=width, anchor="w")
        self.dl_tree.configure(cursor="hand2")
        self.dl_tree.tag_configure("new", foreground=c["ok"])
        self.dl_tree.bind("<Button-1>", self._dl_click)
        self.dl_tree.bind("<Return>", lambda _e: self.open_download_selected())
        self.dl_tree.pack(fill="x", pady=(6, 0))
        dbtns = ttk.Frame(self.dl_outer, style="Card.TFrame")
        dbtns.pack(fill="x", pady=(6, 0))
        dbtns.columnconfigure(0, weight=1, uniform="dlbtn")
        dbtns.columnconfigure(1, weight=1, uniform="dlbtn")
        ttk.Button(dbtns, text="打开作品目录",
                   command=self.open_download_dir).grid(row=0, column=0, sticky="ew",
                                                        padx=(0, 4))
        ttk.Button(dbtns, text="清空列表",
                   command=self.clear_download_list).grid(row=0, column=1, sticky="ew")

        self.log_outer = ttk.Frame(right, style="Card.TFrame", padding=(10, 8))
        ghead = ttk.Frame(self.log_outer, style="Card.TFrame")
        ghead.pack(fill="x")
        ttk.Label(ghead, text="运行动态", style="Title.TLabel").pack(side="left")
        ttk.Button(ghead, text="清空", style="Ghost.TButton",
                   command=self.clear_log_view).pack(side="right")
        ttk.Checkbutton(ghead, text="详细日志", variable=self.log_detail_var,
                        command=self.on_log_detail_toggle).pack(side="right", padx=(0, 8))
        self.log_text = scrolledtext.ScrolledText(
            self.log_outer, wrap="word", state="disabled", font=("Consolas", 9),
            height=16, bg=c["log_bg"], fg=c["text"], insertbackground=c["text"],
            relief="flat", borderwidth=0)
        self.log_text.pack(fill="both", expand=True, pady=(6, 0))
        self.log_text.tag_config("err", foreground=c["bad"])
        self.log_text.tag_config("warn", foreground=c["warn"])
        self.log_text.tag_config("ok", foreground=c["ok"])
        self.log_text.tag_config("dim", foreground=c["muted"])
        self.log_text.configure(state="normal")
        self.log_text.insert(
            "end", "这里只显示每轮检测、下载结果和直播录制情况；完整技术日志在 "
                   "monitor.log（勾「详细日志」可在此查看全部）。\n", "dim")
        self.log_text.configure(state="disabled")

        self.layout_right_panels()
        self.refresh_cookie_badges()

    # -------------------------------------------------------------- 配置
    def load_config(self):
        cfg = dict(DEFAULT_CONFIG)
        saved = core.read_json(CONFIG_FILE, None)
        if isinstance(saved, dict):
            cfg.update(saved)
        for key in ("state_file", "log_file", "live_url_dir"):
            if not os.path.isabs(cfg[key]):
                cfg[key] = os.path.join(BASE_DIR, cfg[key])
        # 老配置里的 douk_dir 只当线索：找到旧的 Volume 就迁移登录信息/作品
        legacy = str(cfg.get("legacy_douk_dir") or "")
        if not legacy:
            douk = str(cfg.get("douk_dir") or "")
            if douk and os.path.isdir(os.path.join(douk, "Volume")):
                legacy = douk
            elif os.path.isdir(os.path.join(BASE_DIR, "DouK", "Volume")):
                legacy = os.path.join(BASE_DIR, "DouK")
        cfg["legacy_douk_dir"] = legacy
        cfg["douk_dir"] = legacy
        cfg["data_dir"] = str(cfg.get("data_dir") or "")
        # v1.7.35：关闭窗口默认改成「隐藏到托盘」。老配置里的 exit 当作没选过，迁一次；
        # 之后用户在菜单里明确选了「退出程序」，就按用户的选择来。
        if not cfg.get("close_action_v2"):
            cfg["close_action"] = "minimize"
            cfg["close_action_v2"] = True
        if not isinstance(cfg.get("accounts"), list):
            cfg["accounts"] = []
        return cfg

    def save_config(self):
        self.cfg["data_dir"] = str(self.cfg.get("data_dir") or "")
        self.cfg["poll_interval"] = self._clamp_int(
            getattr(self, "interval_var", None), getattr(self, "interval_spin", None),
            300, MIN_INTERVAL, MAX_INTERVAL)
        self.cfg["live_poll_interval"] = self._clamp_int(
            getattr(self, "live_interval_var", None), getattr(self, "live_interval_spin", None),
            300, MIN_LIVE_INTERVAL, MAX_INTERVAL)
        self.cfg["full_audit_interval"] = self._clamp_int(
            getattr(self, "full_audit_var", None), getattr(self, "full_audit_spin", None),
            1440, 0, 10080)
        self.cfg["live_separate"] = bool(getattr(self, "live_separate_var", None)
                                         and self.live_separate_var.get())
        self.cfg["live_check"] = bool(self.live_var.get())
        self.cfg["live_quality"] = self.quality_var.get() or "FULL_HD1"
        self.cfg["accounts"] = self.accounts
        # 写盘转相对路径（换目录也能用），内存里保持绝对路径
        saved = dict(self.cfg)
        for key in ("state_file", "log_file", "live_url_dir"):
            val = str(saved.get(key) or "")
            if os.path.isabs(val):
                try:
                    rel = os.path.relpath(val, BASE_DIR)
                except ValueError:
                    rel = ""
                if rel and not rel.startswith(".."):
                    saved[key] = rel
        core.write_json(CONFIG_FILE, saved)
        self.absolutize_paths()

    def absolutize_paths(self):
        """把 state_file / log_file / live_url_dir 统一成绝对路径（相对 BASE_DIR）"""
        for key in ("state_file", "log_file", "live_url_dir"):
            val = str(self.cfg.get(key) or "")
            if val and not os.path.isabs(val):
                self.cfg[key] = os.path.join(BASE_DIR, val)
        return self.cfg
    def load_state(self):
        st = core.read_json(self.cfg["state_file"], None)
        if isinstance(st, dict):
            st.setdefault("accounts", {})
            return st
        return {"accounts": {}}

    def save_state(self):
        """存状态：监控线程 + 界面线程都会调 → 加锁串行；写失败只警告，不把整轮检查炸掉。

        之前 `Permission denied: 'client_state.json.tmp'` 一抛，日志里就是
        「本轮检查出错」+「检查 0 个账号」，一整天都不再检查任何账号。
        """
        with self._state_lock:
            for sec, st in self.state["accounts"].items():
                st["seen"] = dict(list(st.get("seen", {}).items())[-800:])
            path = str(self.cfg.get("state_file") or "")
            if not os.path.isabs(path):
                path = os.path.join(BASE_DIR, path or "client_state.json")
                self.cfg["state_file"] = path
            try:
                core.write_json(path, self.state)
            except Exception as exc:  # noqa: BLE001
                self._save_fails = getattr(self, "_save_fails", 0) + 1
                if self._save_fails <= 2 or self._save_fails % 20 == 0:
                    log.warning("状态没存下来（第 %d 次）：%s；下一轮继续试，监控不停",
                                self._save_fails, exc)
            else:
                if getattr(self, "_save_fails", 0):
                    log.info("状态恢复保存：%s", path)
                self._save_fails = 0
    # ------------------------------------------------------------ 账号表
    def _normalize_accounts(self):
        """保证每个账号的 iid 唯一"""
        used = set()
        seq = 0
        for acc in self.accounts:
            m = re.match(r"row(\d+)$", str(acc.get("iid") or ""))
            if m:
                seq = max(seq, int(m.group(1)))
        for acc in self.accounts:
            iid = str(acc.get("iid") or "")
            if not iid or iid in used:
                seq += 1
                iid = "row%d" % seq
                acc["iid"] = iid
            used.add(iid)
        self._row_seq = seq
        changed = False
        for acc in self.accounts:
            sec = str(acc.get("sec_user_id") or "").strip()
            if not sec.startswith("MS4w"):
                continue
            canonical = "https://www.douyin.com/user/%s" % sec
            if str(acc.get("url") or "") != canonical:
                acc["url"] = canonical
                changed = True
        self._need_save_urls = changed

    def next_iid(self):
        used = {str(a.get("iid") or "") for a in self.accounts}
        while True:
            self._row_seq += 1
            iid = "row%d" % self._row_seq
            if iid not in used:
                return iid

    # ------------------------------------------------------- 主页链接
    def home_cell(self, acc):
        return "打开主页" if self.profile_url(acc) else ""

    def profile_url(self, acc):
        # 抖音主页的规范地址是 /user/<sec_uid>，用抖音号/短 ID 拼出来的地址会报“用户不存在”；
        # TikTok 主页是 /@<unique_id>
        if core.platform_of(acc) == "tiktok":
            uid = (acc.get("unique_id") or "").strip().lstrip("@")
            url = (acc.get("url") or "").strip()
            if url:
                return url
            if uid:
                return "https://www.tiktok.com/@%s" % uid
            sec = (acc.get("sec_user_id") or "").strip()
            return "https://www.tiktok.com/@%s" % sec if sec else ""
        sec = (acc.get("sec_user_id") or "").strip()
        if sec.startswith("MS4w"):
            return "https://www.douyin.com/user/%s" % sec
        url = (acc.get("url") or "").strip()
        if url:
            return url
        uid = (acc.get("unique_id") or "").strip()
        if uid and uid != "0":
            return "https://www.douyin.com/user/%s" % uid
        sec = (acc.get("sec_user_id") or "").strip()
        if sec:
            return "https://www.douyin.com/user/%s" % sec
        return ""

    def open_profile(self, acc):
        url = self.profile_url(acc)
        if not url:
            messagebox.showinfo("提示", "这个账号还没有解析出主页链接，请稍后或点「重新解析」")
            return
        log.info("打开主页：%s", url)
        webbrowser.open(url)

    def tree_row(self, y):
        iid = self.tree.identify_row(y)
        for acc in self.accounts:
            if acc.get("iid") == iid:
                return acc
        return None

    def column_tag(self, name, default="#1"):
        """列名 → Treeview 的 #N（列顺序调整后仍然点得对）"""
        try:
            return "#%d" % (list(self.tree["columns"]).index(name) + 1)
        except Exception:  # noqa: BLE001
            return default

    def refresh_cell(self, acc):
        return "刷新中…" if acc.get("refresh_busy") else "↻ 刷新"

    def on_tree_click(self, event):
        """点「刷新」只刷新这一个账号；点「保存位置」打开该账号的文件夹"""
        col = self.tree.identify_column(event.x)
        acc = self.tree_row(event.y)
        if acc is None:
            return None
        if col == self.column_tag("refresh", "#6"):
            self.refresh_account(acc)
            return "break"
        if col == self.column_tag("save", "#7"):
            self.open_account_save_dir(acc)
            return "break"
        return None

    def open_account_save_dir(self, acc):
        """账号的保存文件夹：单独设置过就打开它，否则打开默认下载目录"""
        path = self.account_save_dir(acc)
        if not path:
            path = os.path.join(self.current_douk_dir(), "Volume", "Download")
        self.open_path(path, create=True)

    def on_tree_double(self, event):
        """双击整行打开主页"""
        acc = self.tree_row(event.y)
        if acc is not None:
            self.open_profile(acc)
            return "break"

    def open_selected_profile(self):
        sel = self.selected_accounts()
        if not sel:
            messagebox.showinfo("提示", "请先选中一个账号")
            return
        self.open_profile(sel[0])

    def platform_cell(self, acc):
        return core.platform_label(core.platform_of(acc))

    SCOPE_CHOICES = (("both", "作品和直播"), ("posts", "监控作品"),
                     ("live", "监控直播"), (None, "跟全局"))

    def account_scope(self, acc):
        """账号级监控内容：both/posts/live；None=跟全局设置"""
        val = acc.get("scope")
        return val if val in ("both", "posts", "live") else None

    def scope_label(self, acc):
        val = self.account_scope(acc)
        if val is None:
            return "跟全局"
        return {"both": "作品+直播", "posts": "作品", "live": "直播"}[val]

    def live_cell(self, acc):
        """监控列：直播中用「●」标记，后面是该账号的监控内容"""
        return ("● " if acc.get("live") else "") + self.scope_label(acc)

    def row_tags(self, acc):
        """账号行颜色提示（优先级：暂停 > 直播 > 出错/限流 > 刷新中 > 监控中）"""
        if not acc.get("enabled", True):
            return ("off",)
        state = str(acc.get("state") or "")
        if acc.get("live") or state == "直播中":
            return ("live",)
        if state.startswith("出错") or state in ("解析失败", "接口限流"):
            return ("warn",)
        if acc.get("refresh_busy") or state in ("刷新中", "待解析"):
            return ("busy",)
        if state == "监控中":
            return ("ok",)
        if state in ("待监控", ""):
            return ("wait",)
        return ()

    def refresh_tree(self):
        keep = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for acc in self.accounts:
            iid = acc.get("iid") or self.next_iid()
            if self.tree.exists(iid):
                iid = self.next_iid()
            acc["iid"] = iid
            state_txt = acc.get("state", "待监控")
            if not acc.get("enabled", True):
                state_txt = "已暂停"
            self.tree.insert("", "end", iid=iid,
                             values=(self.platform_cell(acc),
                                     acc.get("nickname", ""), state_txt,
                                     self.live_cell(acc), self.refresh_cell(acc),
                                     self.save_dir_label(acc),
                                     acc.get("last_check", "")),
                             tags=self.row_tags(acc))
            self.tree.set(iid, "nickname", self.nickname_label(acc))
        for iid in keep:
            if self.tree.exists(iid):
                self.tree.selection_add(iid)
        self.refresh_account_count()

    def update_row(self, acc):
        iid = acc.get("iid")
        if iid and self.tree.exists(iid):
            state_txt = acc.get("state", "待监控")
            if not acc.get("enabled", True):
                state_txt = "已暂停"
            self.tree.item(iid,
                           values=(self.platform_cell(acc),
                                   acc.get("nickname", ""), state_txt,
                                   self.live_cell(acc), self.refresh_cell(acc),
                                   self.save_dir_label(acc),
                                   acc.get("last_check", "")),
                           tags=self.row_tags(acc))
            self.tree.set(iid, "nickname", self.nickname_label(acc))

    def account_by_iid(self, iid):
        for acc in self.accounts:
            if acc.get("iid") == iid:
                return acc
        return None

    def selected_accounts(self):
        out = []
        for iid in self.tree.selection():
            for acc in self.accounts:
                if acc.get("iid") == iid:
                    out.append(acc)
        return out

    def add_account(self, platform="douyin"):
        platform = core.platform_of(platform)
        label = core.platform_label(platform)
        if platform == "tiktok":
            hint = "输入 TikTok 账号（@用户名 / 主页链接 / secUid 均可）："
            title = "添加 TikTok 账号"
        else:
            hint = "输入目标账号（抖音号 / 主页链接 / sec_uid 均可）："
            title = "添加抖音号"
        target = simpledialog.askstring(title, hint, parent=self.root)
        if not target:
            return
        target = target.strip()
        # 从分享文案里抠出链接（「复制打开抖音… https://v.douyin.com/xxx/ 0@0.com」这类）
        link = core.extract_url(target)
        if link:
            target = link
        low = target.lower().rstrip("/")
        m_uid = re.search(r"douyin\.com/user/([A-Za-z0-9_.\-]+)", target)
        link_uid = m_uid.group(1).lower() if m_uid else ""
        for a in self.accounts:
            if core.platform_of(a) != platform:
                continue
            same = (a.get("target") or "").strip().lower().rstrip("/") == low
            same = same or ((a.get("url") or "").strip().lower().rstrip("/") == low)
            if link_uid and link_uid == (a.get("unique_id") or "").strip().lower():
                same = True
            if same:
                messagebox.showinfo("提示", "该账号已在列表中")
                return
        acc = {"iid": self.next_iid(), "target": target, "nickname": "",
               "platform": platform,
               "sec_user_id": "", "unique_id": "", "url": "",
               "enabled": True, "state": "待解析",
               "last_check": "", "save_dir": "", "is_new": True}
        low_target = target.lower()
        if platform == "tiktok":
            # TikTok：@用户名 / 主页链接；主页链接里 @后面就是 unique_id
            if low_target.startswith(("http://", "https://")) or "tiktok.com/" in low_target:
                acc["url"] = target if low_target.startswith("http") else "https://" + target
                m = re.search(r"tiktok\.com/@([A-Za-z0-9_.\-]+)", target)
                if m:
                    acc["unique_id"] = m.group(1)
                    acc["target"] = "@" + m.group(1)
            elif acc["target"].startswith("@"):
                acc["unique_id"] = acc["target"][1:]
            if acc["unique_id"] and not acc["url"]:
                acc["url"] = "https://www.tiktok.com/@%s" % acc["unique_id"]
        elif low_target.startswith(("http://", "https://")) or "douyin.com/" in low_target:
            # 输入的是主页链接时，直接记下链接（列表最后一列可以点开）
            acc["url"] = target if low_target.startswith("http") else "https://" + target
            # 注意：链接里的 ?user/MS4w... 是 sec_uid，不是抖音号，别写到 unique_id 里
        self.accounts.append(acc)
        self.save_config()
        self.refresh_tree()
        log.info("已添加%s账号：%s，正在解析 ...", label, target)
        threading.Thread(target=self.resolve_account, args=(acc,), daemon=True).start()

    def remove_accounts(self):
        sel = self.selected_accounts()
        if not sel:
            return
        if not messagebox.askyesno("确认", f"确定删除选中的 {len(sel)} 个账号？"):
            return
        for acc in sel:
            if acc in self.accounts:
                self.accounts.remove(acc)
        self.save_config()
        self.refresh_tree()
        log.info("已删除 %d 个账号", len(sel))

    def nickname_label(self, acc):
        """昵称列为空时给出明确占位，避免看起来像“显示成抖音号/空白”"""
        nick = (acc.get("nickname") or "").strip()
        if nick:
            return nick
        state = acc.get("state") or ""
        if state in ("待解析", "解析中"):
            return "（解析中…）"
        return "（未解析）"

    def save_dir_label(self, acc):
        path = acc.get("save_dir") or ""
        if not path:
            return "默认"
        return os.path.basename(os.path.normpath(path)) or path

    # ------------------------------------------------------------ Cookie
    def change_cookie(self, platform="douyin"):
        self.open_cookie_dialog(platform)

    def open_cookie_dialog(self, platform="douyin"):
        """填写/更换指定平台的 Cookie：写入数据目录 Volume/settings.json"""
        platform = core.platform_of(platform)
        label = core.platform_label(platform)
        site = "tiktok.com" if platform == "tiktok" else "douyin.com"
        win = getattr(self, "cookie_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return
        win = tk.Toplevel(self.root)
        self.cookie_win = win
        win.title("设置%s Cookie" % label)
        win.transient(self.root)
        win.resizable(False, False)
        win.grab_set()

        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)

        head = "修改%s Cookie（手动粘贴）" % label
        ttk.Label(frm, text=head, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        ttk.Label(
            frm,
            text=("一般不需要手动填：点「设置 → 账号与登录 → %s 扫码登录…」会自动登录并取 Cookie。\n"
                  "要手动填的话：浏览器登录 %s → F12 → Console 输入 document.cookie 回车，\n"
                  "把整段内容粘到下面（形如 sessionid=…; uid_tt=…; …）。" % (label, site)),
            foreground="#555", justify="left").pack(anchor="w", pady=(6, 8))

        txt = tk.Text(frm, width=78, height=7, wrap="char", font=("Consolas", 9))
        txt.pack(fill="both", expand=True)
        try:
            current = core.cookie_to_text(core.load_cookie_map(self.cfg, platform))
        except Exception:
            current = ""
        if current:
            txt.insert("1.0", current)

        status_var = tk.StringVar(value="")
        status = ttk.Label(frm, textvariable=status_var, foreground="#666",
                           wraplength=620, justify="left")
        status.pack(anchor="w", pady=(8, 0))
        self.cookie_status_var = status_var
        self.cookie_status_label = status

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(10, 0))

        def read_existing():
            try:
                existing = core.cookie_to_text(core.load_cookie_map(self.cfg, platform))
            except Exception as exc:
                messagebox.showerror("读取失败", str(exc), parent=win)
                return
            if not existing:
                messagebox.showinfo("提示", "还没有保存 %s 的 Cookie。" % label, parent=win)
                return
            txt.delete("1.0", "end")
            txt.insert("1.0", existing)
            status_var.set("已读入当前保存的 Cookie（%d 项）" % len(core.parse_cookie_text(existing)))
            status.configure(foreground="#666")

        save_btn = ttk.Button(btns, text="保存并验证", command=None)
        save_btn.pack(side="right")
        ttk.Button(btns, text="取消", command=win.destroy).pack(side="right", padx=6)
        ttk.Button(btns, text="读取当前 Cookie", command=read_existing).pack(side="left")

        def on_save():
            text = txt.get("1.0", "end").strip()
            cookie = core.parse_cookie_text(text)
            if not cookie:
                messagebox.showwarning("内容为空", "请先粘贴 Cookie 内容。", parent=win)
                return
            if not cookie.get("sessionid"):
                if not messagebox.askyesno(
                        "可能未登录",
                        "这段 Cookie 里没有 sessionid，通常表示还没登录%s。\n\n仍要保存吗？" % label,
                        parent=win):
                    return
            try:
                count = core.save_cookie_text(self.cfg, text, platform)
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc), parent=win)
                return
            self.reset_clients()        # 让下一次检查用新 Cookie 重新建连
            self.cfg["cookie_prompted"] = True
            self.save_config()
            log.info("%s Cookie 已更新（%d 项），正在验证…", label, count)
            status_var.set("已保存 %d 项，正在验证…" % count)
            status.configure(foreground="#666")
            save_btn.state(["disabled"])

            self.verify_cookie_async(platform)

        save_btn.configure(command=on_save)

        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 3
        win.geometry("+%d+%d" % (max(x, 0), max(y, 0)))
        win.focus_force()
        txt.focus_set()

    def on_cookie_result(self, payload):
        if isinstance(payload, dict):
            ok = bool(payload.get("ok"))
            msg = str(payload.get("msg") or "")
            platform = core.platform_of(payload.get("platform")
                                        or self.default_platform())
        else:
            ok, msg = payload
            platform = self.default_platform()
        label = core.platform_label(platform)
        verify = getattr(self, "_cookie_verify", None)
        if verify is None:
            verify = self._cookie_verify = {}
        verify[platform] = (ok, msg)
        if ok:
            log.info("%s Cookie 验证通过：%s", label, msg)
        else:
            log.warning("%s Cookie 验证未通过：%s", label, msg)
        self.refresh_cookie_badges()
        win = getattr(self, "cookie_win", None)
        if win is None or not win.winfo_exists():
            return
        var = getattr(self, "cookie_status_var", None)
        label_w = getattr(self, "cookie_status_label", None)
        if var is not None:
            var.set(("✔ " if ok else "✘ ") + msg)
        if label_w is not None:
            label_w.configure(foreground="#2e7d32" if ok else "#c62828")
        if ok:
            win.after(900, lambda: win.winfo_exists() and win.destroy())

    # ---------------------------------------------------------- 静默模式
    def silent(self):
        """静默模式：能不打扰就不打扰，提示只写进日志/文件"""
        try:
            return bool(self.cfg.get("silent_mode"))
        except Exception:
            return False

    def popup(self, kind, title, message, **kw):
        """弹提示；静默模式下只写日志，不弹窗"""
        if self.silent():
            log.info("静默模式（不弹窗）%s：%s", title, str(message).replace("\n", " "))
            return None
        fn = getattr(messagebox, kind, None)
        if fn is None:
            return None
        try:
            return fn(title, message, **kw)
        except Exception as exc:
            log.warning("弹窗失败：%s", exc)
            return None

    def on_silent_change(self):
        on = bool(self.silent_var.get())
        self.cfg["silent_mode"] = on
        try:
            self.save_config()
        except Exception as exc:
            log.warning("保存配置失败：%s", exc)
        if on:
            log.info("已开启静默模式：不弹提示框、停止监控不弹总结窗口、开始监控后自动最小化")
            self.status_override = ("静默模式已开启", time.time() + 8)
        else:
            log.info("已关闭静默模式：恢复弹出提示与总结窗口")
            self.status_override = ("静默模式已关闭", time.time() + 8)

    # ------------------------------------------------------ 扫码登录（浏览器）
    def cookie_ready(self, platform=None):
        """指定平台是否已经有可用的登录 Cookie（sessionid）。

        platform 不传就按「默认平台」判断 —— 启动检查、自动开监控、单账号刷新、
        核账都走这里；TikTok 的 Cookie 存在 settings.json 的 cookie_tiktok 里。
        """
        platform = core.platform_of(platform or self.default_platform())
        try:
            jar = core.load_cookie_map(self.cfg, platform)
        except Exception:
            return False
        return bool(str(jar.get("sessionid") or "").strip())

    def start_browser_login(self, platform="douyin", auto=False):
        """打开浏览器扫码登录指定平台，成功后把 Cookie 写回对应平台"""
        platform = core.platform_of(platform)
        label = core.platform_label(platform)
        if self.login_busy:
            win = self.login_win
            if win is not None and win.winfo_exists():
                win.lift()
                win.focus_force()
            return
        try:
            import browser_login
        except Exception as exc:
            log.error("加载扫码登录模块失败：%s", exc)
            self.popup("showerror", "无法扫码登录", "加载登录模块失败：%s" % exc, parent=self.root)
            return
        exe = browser_login.find_browser()
        if not exe:
            msg = ("没找到 Chrome 或 Edge 浏览器，无法自动扫码登录。\n\n"
                   "可以手动填 Cookie：设置 → 账号与登录 → 更改 Cookie…")
            log.warning("扫码登录不可用：没有找到 Chrome / Edge")
            if auto:
                self.root.after(200, lambda: self.popup(
                    "showwarning", "无法扫码登录", msg, parent=self.root))
            else:
                self.popup("showwarning", "无法扫码登录", msg, parent=self.root)
            return
        if auto:
            log.info("还没有登录 %s 的 Cookie，正在打开浏览器（%s）等待扫码…",
                     label, os.path.basename(exe))
        self.login_busy = True
        self.login_platform = platform
        self.login_stop.clear()
        self._show_login_window(os.path.basename(exe), platform)

        def worker():
            try:
                out = browser_login.login_via_browser(
                    on_status=lambda text: self.q.put(("login_status", text)),
                    should_stop=self.login_stop.is_set,
                    platform=platform)
            except Exception as exc:
                log.error("扫码登录出错：%s", exc)
                out = {"ok": False, "cookie": {}, "message": "登录出错：%s" % exc,
                       "browser": "", "platform": platform}
            out.setdefault("platform", platform)
            self.q.put(("login_done", out))

        threading.Thread(target=worker, daemon=True).start()

    def _show_login_window(self, browser_name, platform="douyin"):
        platform = core.platform_of(platform)
        label = core.platform_label(platform)
        site = "tiktok.com" if platform == "tiktok" else "douyin.com"
        app = "TikTok" if platform == "tiktok" else "抖音"
        win = self.login_win
        if win is not None and win.winfo_exists():
            win.lift()
            return
        win = tk.Toplevel(self.root)
        self.login_win = win
        win.title("扫码登录%s" % label)
        win.transient(self.root)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="请在弹出的浏览器窗口里扫码登录",
                  font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frm, wraplength=440, justify="left", foreground="#555",
                  text=("已经用 %s 打开 %s。\n"
                        "用手机%s「扫一扫」或验证码登录，登录成功后本程序会自动\n"
                        "取到 Cookie 并关掉浏览器，不需要手动复制任何东西。"
                        % (browser_name, site, app))
                  ).pack(anchor="w", pady=(8, 10))
        self.login_status_var = tk.StringVar(value="正在打开浏览器…")
        ttk.Label(frm, textvariable=self.login_status_var, foreground="#1565c0",
                  wraplength=440, justify="left").pack(anchor="w")
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="取消登录", command=self.cancel_browser_login).pack(side="right")
        ttk.Button(btns, text="手动填写 Cookie…",
                   command=lambda: self.change_cookie(platform)).pack(side="right", padx=6)
        win.protocol("WM_DELETE_WINDOW", self.cancel_browser_login)
        win.minsize(hi_dpi.scale_px(480), hi_dpi.scale_px(220))
        hi_dpi.apply_tk_scaling(win)
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 3
        win.geometry("+%d+%d" % (max(x, 0), max(y, 0)))
        try:
            win.attributes("-topmost", True)
            win.after(1200, lambda: win.winfo_exists() and win.attributes("-topmost", False))
        except Exception:
            pass
        win.focus_force()

    def cancel_browser_login(self):
        if self.login_busy:
            self.login_stop.set()
            if self.login_status_var is not None:
                self.login_status_var.set("正在取消…")
            log.info("已请求取消扫码登录")
        else:
            self._close_login_window()

    def _close_login_window(self):
        win = self.login_win
        self.login_win = None
        if win is not None:
            try:
                if win.winfo_exists():
                    win.destroy()
            except Exception:
                pass

    def on_login_status(self, text):
        if self.login_status_var is not None and self.login_win is not None:
            try:
                if self.login_win.winfo_exists():
                    self.login_status_var.set(text)
            except Exception:
                pass
        log.info("%s", text)

    def on_login_done(self, out):
        out = out or {}
        platform = core.platform_of(out.get("platform")
                                    or getattr(self, "login_platform", "douyin"))
        label = core.platform_label(platform)
        self.login_busy = False
        self._close_login_window()
        if out.get("ok"):
            jar = out.get("cookie") or {}
            try:
                count = core.save_cookie_text(self.cfg, core.cookie_to_text(jar), platform)
            except Exception as exc:
                log.error("保存 Cookie 失败：%s", exc)
                self.popup("showerror", "保存 Cookie 失败", str(exc), parent=self.root)
                return
            self.reset_clients()
            self.cfg["cookie_prompted"] = True
            self.cfg["login_skipped"] = False
            self.save_config()
            log.info("%s 扫码登录成功，已保存 %d 项 Cookie（%s）", label, count,
                     core.cookie_summary(self.cfg, platform))
            self.status_override = ("%s 扫码登录成功，Cookie 已更新" % label, time.time() + 10)
            self.verify_cookie_async(platform)
            return
        msg = str(out.get("message") or "未知原因")
        log.warning("扫码登录未完成：%s", msg)
        if "取消" in msg:
            self.cfg["login_skipped"] = True
            self.save_config()
            log.info("已跳过登录：稍后可点「设置 → 账号与登录 → %s 扫码登录…」重新登录", label)
            return
        self.popup(
            "showwarning", "扫码登录未完成",
            "%s\n\n可以再试一次，或者点「设置 → 更改 Cookie…」手动填写。" % msg,
            parent=self.root)

    def verify_cookie_async(self, platform=None):
        """后台验证指定平台的 Cookie（结果经队列回到 on_cookie_result）"""
        platform = core.platform_of(platform or self.default_platform())
        sec = ""
        for acc in self.accounts:
            if core.platform_of(acc) == platform and acc.get("sec_user_id"):
                sec = acc["sec_user_id"]
                break

        def worker():
            try:
                ok, msg = core.verify_cookie(
                    self.cfg, sec, timeout=int(self.cfg.get("request_timeout") or 20),
                    platform=platform)
            except Exception as exc:
                ok, msg = False, "验证出错：%s" % exc
            self.q.put(("cookie_result", {"ok": ok, "msg": msg, "platform": platform}))

        threading.Thread(target=worker, daemon=True).start()

    def target_accounts(self, action):
        """选中了账号就作用于选中的；一个都没选时询问是否作用于全部账号"""
        sel = self.selected_accounts()
        if sel:
            return sel
        if not self.accounts:
            messagebox.showinfo("提示", "列表里还没有账号，请先「添加账号」")
            return []
        question = ("当前没有选中账号。\n是否对全部 %d 个账号执行「%s」？") % (len(self.accounts), action)
        if messagebox.askyesno("确认", question):
            return list(self.accounts)
        return []

    def set_save_dir(self):
        sel = self.target_accounts("设置保存位置")
        if not sel:
            return
        current = sel[0].get("save_dir") or os.path.join(
            self.douk_var.get().strip(), "Volume", "Download")
        path = filedialog.askdirectory(parent=self.root, title="选择该账号作品保存位置",
                                       initialdir=current if os.path.isdir(current) else BASE_DIR,
                                       mustexist=False)
        if not path:
            return
        path = os.path.abspath(path)
        for acc in sel:
            acc["save_dir"] = path
        self.save_config()
        self.refresh_tree()
        names = "、".join(a.get("nickname") or a.get("target") for a in sel)
        log.info("已设置保存位置：%s -> %s", names, path)

    def set_history_save_dir(self):
        """「下载历史视频」专用保存位置（和平时监控下载分开）"""
        current = core.history_dir(self.cfg)
        path = filedialog.askdirectory(
            parent=self.root, title="选择历史作品保存位置（下载历史视频专用）",
            initialdir=current if os.path.isdir(current) else BASE_DIR, mustexist=False)
        if not path:
            return
        path = os.path.abspath(path)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("无法使用该目录", "创建目录失败：\n%s\n\n%s" % (path, exc))
            return
        self.cfg["history_save_dir"] = path
        self.save_config()
        log.info("历史作品保存位置已设为：%s", path)
        self.status_override = ("历史下载：%s" % path, time.time() + 8)

    def reset_history_save_dir(self):
        if not self.cfg.get("history_save_dir"):
            messagebox.showinfo("提示", "当前已经是默认位置：数据目录\\Volume\\历史下载")
            return
        self.cfg["history_save_dir"] = ""
        self.save_config()
        log.info("历史作品保存位置已恢复默认：%s", core.history_dir(self.cfg))
        self.status_override = ("历史下载已恢复默认位置", time.time() + 6)

    def migrate_history_now(self):
        """手动把 Volume\\Download 里的旧作品挪到历史下载目录"""
        src = os.path.join(self.current_douk_dir(), "Volume", "Download")
        dst = core.history_dir(self.cfg)
        if not os.path.isdir(src) or not os.listdir(src):
            messagebox.showinfo("提示", "没有需要整理的历史作品。\n\n当前历史下载位置：\n%s" % dst)
            return
        if not messagebox.askyesno(
                "迁移旧作品",
                "把 Download 里的作品按账号移动到：\n%s\n\n目录名用 UID<抖音号>_<昵称>_发布作品，下载记录会一起更新。继续吗？" % dst,
                parent=self.root):
            return
        try:
            res = core.migrate_history_downloads(self.cfg, self.accounts)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("迁移失败", str(exc), parent=self.root)
            return
        msg = "已整理 %d 个文件，失败 %d 个，更新记录 %d 条" % (
            res["moved"], res["skipped"], res["records"])
        log.info("手动迁移历史作品：%s", msg)
        self.status_override = (msg, time.time() + 10)
        messagebox.showinfo("迁移完成", msg, parent=self.root)

    def _migrate_history_auto(self):
        """升级后第一次启动：自动把旧 Download 作品归到历史下载目录（只做一次）"""
        if self.cfg.get("history_migrated"):
            return
        src = os.path.join(self.current_douk_dir(), "Volume", "Download")
        self.cfg["history_migrated"] = True
        if not os.path.isdir(src) or not os.listdir(src):
            self.save_config()
            return

        def worker():
            try:
                res = core.migrate_history_downloads(self.cfg, self.accounts)
                if res["moved"] or res["skipped"]:
                    self.q.put(("log", "已把 %d 个旧作品归到历史下载目录：%s（失败 %d 个）"
                                % (res["moved"], res["to"], res["skipped"])))
                else:
                    self.q.put(("log", "Download 里没有可迁移的作品"))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("log", "[WARNING] 迁移旧作品到历史下载目录失败：%s" % exc))
            self.q.put(("save", None))

        threading.Thread(target=worker, daemon=True).start()

    def download_history(self):
        sel = self.selected_accounts()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选中要下载历史作品的账号（可多选）")
            return
        if self.history_busy:
            messagebox.showinfo("提示", "上一批历史作品还在下载中，请等日志出现完成提示后再试")
            return
        names = "、".join(a.get("nickname") or a.get("target") for a in sel)
        ok = messagebox.askyesno(
            "下载历史视频",
            "将用内置下载器下载 %s 的全部历史作品"
            "（视频 / 图集 / 实况）。\n\n保存到：%s\\<账号>_发布作品\n"
            "（每个账号单独一个文件夹，目录名：UID<抖音号>_<昵称>_发布作品；"
            "已下载过的作品会被跳过；"
            "想全部重新下载请先点「清除下载记录」）\n\n继续吗？"
            % (names, core.history_dir(self.cfg)),
            parent=self.root)
        if not ok:
            return
        cfg = dict(self.cfg)
        cfg["data_dir"] = self.cfg.get("data_dir") or ""
        if not os.path.isdir(self.current_douk_dir()):
            messagebox.showerror("错误", "数据目录不可用，请检查程序所在位置是否可写")
            return
        self.history_busy = True
        self.history_btn.config(state="disabled")
        self.status_var.set("正在下载历史作品…")
        threading.Thread(target=self._download_history_worker,
                         args=(list(sel), cfg), daemon=True).start()

    def _download_history_worker(self, accs, cfg):
        for acc in accs:
            name = acc.get("nickname") or acc.get("target")
            try:
                sec = acc.get("sec_user_id") or ""
                if not sec:
                    dy = core.DouYin("",
                                     timeout=int(cfg.get("request_timeout", 20)),
                                     data_dir=str(cfg.get("data_dir") or ""))
                    sec = dy.resolve_sec_uid(acc["target"])
                    acc["sec_user_id"] = sec
                    acc["nickname"] = dy.fetch_profile(sec).get("nickname") or name
                    name = acc["nickname"]
                    self.q.put(("row", acc))
                    self.q.put(("save", None))
                one = dict(cfg)
                one["save_dir"] = core.history_account_dir(one, acc)
                self.q.put(("log", "【%s】开始获取作品列表（可能要翻十几页，请稍等）…" % name))
                last = {"pct": -1}

                def on_progress(done, total, _name=name, _last=last):
                    if not total:
                        return
                    pct = done * 100 // total
                    # 每 5% 或每 20 个报一次，避免刷屏
                    if pct >= _last["pct"] + 5 or done == 1 or done == total:
                        _last["pct"] = pct
                        self.q.put(("log", "【%s】下载进度 %d/%d（%d%%）"
                                    % (_name, done, total, pct)))

                stats, files, text = core.download_account_history(
                    one, sec, nickname=name, progress=on_progress)
                summary = "，".join("%s %d 个" % (k, v) for k, v in stats.items()) or "未解析到统计信息"
                self.q.put(("log", "【%s】历史下载结束：%s；新增文件 %d 个"
                            % (name, summary, len(files))))
                if one["save_dir"] and files:
                    self.q.put(("log", "【%s】已保存到：%s" % (name, os.path.abspath(one["save_dir"]))))
                if files:
                    self.q.put(("downloaded", {
                        "nickname": name,
                        "items": self._items_from_files(files),
                        "files": files,
                    }))
                if not files:
                    skipped = sum(v for k, v in stats.items() if k.startswith("跳过"))
                    if skipped:
                        self.q.put(("log", "【%s】%d 个作品被跳过（已有下载记录或文件已存在）；"
                                           "想全部重新下载请点「清除下载记录」并在弹窗里选「是」"
                                           % (name, skipped)))
                    else:
                        self.q.put(("log", "【%s】没有下载任何文件，可能账号没有作品或 Cookie 失效"
                                    % name))
                        for line in [x for x in text.splitlines() if x.strip()][-10:]:
                            self.q.put(("log", "    " + line.strip()))
            except Exception as exc:
                self.q.put(("log", "[ERROR] 【%s】下载历史作品失败：%s" % (name, exc)))
        self.q.put(("history_done", None))

    # ------------------------------------------------------------ 单独下载链接
    def open_link_dialog(self):
        """单独下载：粘贴一条或多条作品链接（视频 / 图集），与监控的账号无关"""
        win = getattr(self, "link_win", None)
        try:
            if win is not None and win.winfo_exists():
                win.deiconify()
                win.lift()
                win.focus_force()
                return
        except Exception:
            pass
        win = tk.Toplevel(self.root)
        self.link_win = win
        win.title("单独下载作品（视频 / 图集）")
        win.geometry("%dx%d" % (hi_dpi.scale_px(700), hi_dpi.scale_px(470)))
        win.minsize(hi_dpi.scale_px(580), hi_dpi.scale_px(420))
        hi_dpi.apply_tk_scaling(win)

        pad = ttk.Frame(win, padding=(10, 10, 10, 6))
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, justify="left", text=(
            "把作品链接粘贴到下面，一行一条；也可以直接粘贴整段分享文案（会自动提取链接）。\n"
            "支持视频、图集（图文）、实况；短链 v.douyin.com/… 也行。")).pack(anchor="w")

        box = ttk.Frame(pad)
        box.pack(fill="both", expand=True, pady=(6, 4))
        self.link_text = tk.Text(box, height=10, wrap="word", font=("Consolas", 9), undo=True)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.link_text.yview)
        self.link_text.configure(yscrollcommand=sb.set)
        self.link_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for ev in ("<KeyRelease>", "<<Paste>>"):
            self.link_text.bind(ev, lambda e: self._link_count())
        self.link_text.bind("<<Modified>>", self._link_modified)

        self.link_count_var = tk.StringVar(value="已识别 0 条链接")
        self.link_clear_var = tk.BooleanVar(value=True)
        row = ttk.Frame(pad)
        row.pack(fill="x")
        ttk.Label(row, textvariable=self.link_count_var).pack(side="left")
        ttk.Checkbutton(row, text="下载完成后清空输入框",
                        variable=self.link_clear_var).pack(side="right")

        self.link_status_var = tk.StringVar(value="")
        ttk.Label(pad, textvariable=self.link_status_var, foreground="#2e7d32").pack(
            anchor="w", pady=(2, 0))

        wrow = ttk.Frame(pad)
        wrow.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(wrow, text="后台监听剪贴板（复制链接就自动下载，不用粘贴）",
                        variable=self.watch_var,
                        command=self.on_watch_change).pack(side="left")
        self.watch_info_var = tk.StringVar(value="")
        ttk.Label(wrow, textvariable=self.watch_info_var, foreground="#1565c0").pack(side="right")
        self.refresh_watch_info()

        bar = ttk.Frame(win, padding=(10, 0, 10, 10))
        bar.pack(fill="x")
        self.link_btn = ttk.Button(bar, text="开始下载", command=self.start_link_download)
        self.link_btn.pack(side="left")
        ttk.Button(bar, text="粘贴", command=self.paste_to_links).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="清空", command=self.clear_link_text).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())
        try:
            self.link_text.focus_set()
        except Exception:
            pass

    # ---------------------------------------------------- 后台监听剪贴板
    def clipboard_watch_on(self):
        return bool(self.cfg.get("clipboard_watch"))

    def on_watch_change(self):
        on = bool(self.watch_var.get())
        self.cfg["clipboard_watch"] = on
        try:
            self.save_config()
        except Exception as exc:
            log.warning("保存配置失败：%s", exc)
        if on:
            self.start_clipboard_watch()
        else:
            self.stop_clipboard_watch()
        self.refresh_watch_info()

    def start_clipboard_watch(self):
        """开启后台监听：每秒看一眼剪贴板，复制到抖音作品链接就自动下载"""
        if self.watch_after is not None:
            return
        # 启动时剪贴板里可能还留着上次的链接，先记下来，避免每次启动都重下一遍
        try:
            text = self.root.clipboard_get()
        except Exception:
            text = ""
        kept = 0
        if text:
            for url in core.extract_links(text):
                self.watch_seen[core.link_key(url)] = time.time()
                kept += 1
        log.info("已开启后台监听：复制抖音作品链接（或整段分享文案）后会自动下载，不用粘贴")
        if kept:
            log.info("【后台监听】已忽略剪贴板里原有的 %d 条链接（避免重复下载），复制新的链接即可", kept)
        self.status_override = ("后台监听已开启", time.time() + 8)
        self.watch_after = self.root.after(800, self._watch_tick)

    def stop_clipboard_watch(self):
        if self.watch_after is not None:
            try:
                self.root.after_cancel(self.watch_after)
            except Exception:
                pass
            self.watch_after = None
        self.watch_pending = []
        log.info("已停止后台监听剪贴板（已自动下载 %d 个作品）", self.watch_count)
        self.status_override = ("后台监听已停止", time.time() + 8)

    def _watch_prune(self, keep=1800):
        """清掉 30 分钟前的去重记录，让同一个链接过一会儿还能再下一次"""
        now = time.time()
        for key in [k for k, ts in self.watch_seen.items() if now - ts > keep]:
            self.watch_seen.pop(key, None)

    def _watch_tick(self):
        self.watch_after = None
        if not self.clipboard_watch_on():
            return
        try:
            text = ""
            try:
                text = self.root.clipboard_get()
            except Exception:
                text = ""
            if text:
                self._watch_prune()
                links = []
                for url in core.extract_links(text):
                    key = core.link_key(url)
                    if key in self.watch_seen:
                        continue
                    self.watch_seen[key] = time.time()
                    links.append(url)
                if links:
                    log.info("【后台监听】剪贴板里发现 %d 条新链接：%s", len(links),
                             "；".join(links[:3]) + ("…" if len(links) > 3 else ""))
                    self._watch_download(links)
        except Exception as exc:
            log.warning("【后台监听】检查剪贴板出错：%s", exc)
        finally:
            if self.clipboard_watch_on():
                self.watch_after = self.root.after(1000, self._watch_tick)

    def _watch_download(self, links):
        if not links:
            return
        if self.link_busy:
            self.watch_pending.extend(links)
            log.info("【后台监听】当前还在下载，先把 %d 条链接排进队列（共 %d 条）",
                     len(links), len(self.watch_pending))
            self.refresh_watch_info()
            return
        douk = self.current_douk_dir()
        if not douk:
            log.warning("【后台监听】数据目录不可用，跳过这 %d 条链接", len(links))
            return
        cfg = dict(self.cfg)
        cfg["data_dir"] = self.cfg.get("data_dir") or ""
        cfg["save_dir"] = self.default_save_dir()
        self.link_busy = True
        self.status_override = ("后台监听下载中…", time.time() + 900)
        log.info("【后台监听】自动下载 %d 条链接：%s", len(links), "、".join(links)[:300])
        if cfg["save_dir"]:
            log.info("【后台监听】保存位置：%s", cfg["save_dir"])
        self.refresh_watch_info()
        threading.Thread(target=self._link_worker, args=(list(links), cfg, True),
                         daemon=True).start()

    def _watch_flush(self):
        if self.link_busy or not self.watch_pending:
            return
        if not self.clipboard_watch_on():
            self.watch_pending = []
            self.refresh_watch_info()
            return
        batch = self.watch_pending[:]
        self.watch_pending = []
        self._watch_download(batch)

    def refresh_watch_info(self):
        var = getattr(self, "watch_info_var", None)
        if var is None:
            return
        try:
            if self.clipboard_watch_on():
                extra = " · 还有 %d 条排队" % len(self.watch_pending) if self.watch_pending else ""
                var.set("后台监听中 · 已自动下载 %d 个作品%s" % (self.watch_count, extra))
            else:
                var.set("后台监听已关闭")
        except Exception:
            pass

    def _link_modified(self, event=None):
        try:
            self.link_text.edit_modified(False)
        except Exception:
            pass
        self._link_count()

    def _link_count(self):
        try:
            n = len(core.extract_links(self.link_text.get("1.0", "end")))
            self.link_count_var.set("已识别 %d 条链接" % n)
        except Exception:
            pass

    def clear_link_text(self):
        try:
            self.link_text.delete("1.0", "end")
            self._link_count()
            self.link_status_var.set("")
        except Exception:
            pass

    def paste_to_links(self):
        try:
            data = self.root.clipboard_get()
        except Exception:
            messagebox.showinfo("粘贴", "剪贴板里没有文本内容", parent=self.link_win)
            return
        try:
            head = self.link_text.get("1.0", "end").strip()
            self.link_text.insert("end", ("\n" if head else "") + data)
            self._link_count()
        except Exception:
            pass

    def start_link_download(self):
        if getattr(self, "link_busy", False):
            return
        links = core.extract_links(self.link_text.get("1.0", "end"))
        if not links:
            messagebox.showwarning(
                "没有识别到链接",
                "请粘贴抖音作品链接，例如：\n"
                "https://www.douyin.com/video/73…\n"
                "https://www.douyin.com/note/73…\n"
                "https://v.douyin.com/xxxxxx/\n\n"
                "也可以直接粘贴整段分享文案。", parent=self.link_win)
            return
        douk = self.current_douk_dir()
        if not douk:
            messagebox.showerror(
                "数据目录不可用",
                "程序目录和用户目录都写不进去，请把本程序放到可写的文件夹再运行。",
                parent=self.link_win)
            return
        cfg = dict(self.cfg)
        cfg["data_dir"] = self.cfg.get("data_dir") or ""
        cfg["save_dir"] = self.default_save_dir()   # 单独下载走「默认保存位置」
        self.link_busy = True
        self.link_btn.config(state="disabled")
        self.link_status_var.set("正在下载 %d 条链接，请稍候…" % len(links))
        self.append_log("开始单独下载 %d 条链接：" % len(links))
        for u in links:
            self.append_log("    " + u)
        if cfg["save_dir"]:
            self.append_log("保存位置：%s" % cfg["save_dir"])
        self.status_override = ("单独下载中…", time.time() + 3600)
        threading.Thread(target=self._link_worker, args=(list(links), cfg),
                         daemon=True).start()

    @staticmethod
    def _items_from_files(files):
        """把下载器返回的文件列表还原成「作品」行（图集多个文件合成一条）。"""
        out, seen = [], {}
        for f in files or []:
            name = os.path.basename(str(f))
            stem = os.path.splitext(name)[0]
            parts = stem.split("-")
            kind = next((x for x in parts[:4] if x in ("视频", "图集", "实况")), "作品")
            base = re.sub(r"_\d+$", "", stem)
            key = (kind, base)
            if key in seen:
                seen[key]["files"].append(f)
                continue
            desc = "-".join(parts[3:]).strip() if len(parts) > 3 else ""
            desc = re.sub(r"_\d+$", "", desc)[:60] or stem[:60]
            item = {"aweme_id": "", "kind": kind, "desc": desc, "files": [f]}
            seen[key] = item
            out.append(item)
        return out

    def _link_worker(self, links, cfg, watch=False):
        tag = "【后台监听】" if watch else "【单独下载】"
        try:
            self.q.put(("log", "%s调用内置下载器下载 %d 条链接 ..." % (tag, len(links))))
            stats, files, text = core.download_links(
                cfg, links, timeout=int(cfg.get("douk_download_timeout", 900)))
            summary = "，".join("%s %d 个" % (k, v) for k, v in stats.items()) or "未解析到统计信息"
            self.q.put(("log", "%s结束：%s；新增文件 %d 个" % (tag, summary, len(files))))
            if cfg["save_dir"] and files:
                self.q.put(("log", "%s已保存到：%s" % (tag, os.path.abspath(cfg["save_dir"]))))
            if not files:
                skipped = sum(v for k, v in stats.items() if k.startswith("跳过"))
                if skipped:
                    self.q.put(("log", "%s%d 个作品被跳过（已有下载记录或文件已存在）" % (tag, skipped)))
                else:
                    self.q.put(("log", "%s没有下载文件：可能链接无效、作品已删除或 Cookie 失效" % tag))
                    for line in [x for x in text.splitlines() if x.strip()][-10:]:
                        self.q.put(("log", "    " + line.strip()))
            items = self._items_from_files(files)
            if items or files:
                self.q.put(("downloaded", {
                    "nickname": "后台监听" if watch else "单独下载",
                    "items": items, "files": files}))
            self.q.put(("links_done", {"ok": True, "count": len(files), "watch": watch}))
        except Exception as exc:
            self.q.put(("log", "[ERROR] %s失败：%s" % (tag, exc)))
            self.q.put(("links_done", {"ok": False, "error": str(exc), "count": 0, "watch": watch}))

    def clear_download_records(self):
        sel = self.selected_accounts()
        if not sel:
            messagebox.showinfo("提示", "请先选中要清除下载记录的账号（可多选）")
            return
        names = "、".join(a.get("nickname") or a.get("target") for a in sel)
        choice = messagebox.askyesnocancel(
            "清除下载记录",
            "将清除 %s 的下载记录。\n\n"
            "清除后，下次「下载历史视频」会重新下载这个账号的全部作品。\n\n"
            "选「是」：删除记录 + 把该账号已下载的旧文件移到备份文件夹（推荐，文件不会重复占地方）\n"
            "选「否」：只删除记录，旧文件留在原处（重新下载会覆盖同名文件）\n"
            "选「取消」：放弃" % names,
            parent=self.root)
        if choice is None:
            return
        move_files = bool(choice)
        cfg = dict(self.cfg)
        cfg["data_dir"] = self.cfg.get("data_dir") or ""
        threading.Thread(target=self._clear_records_worker,
                         args=(list(sel), cfg, move_files), daemon=True).start()

    def _clear_records_worker(self, accs, cfg, move_files=False):
        for acc in accs:
            name = acc.get("nickname") or acc.get("target")
            sec = acc.get("sec_user_id") or ""
            if not sec:
                self.q.put(("log", "[ERROR] 【%s】还没有解析出 sec_uid，无法清除下载记录" % name))
                continue
            try:
                if move_files:
                    bdir, nd, nf = core.backup_account_downloads(
                        cfg, name, acc.get("save_dir") or "",
                        uid=str(acc.get("unique_id") or ""), platform=core.platform_of(acc))
                    if bdir:
                        self.q.put(("log", "【%s】已把旧文件移到备份：%d 个作品目录 / %d 个文件 → %s"
                                    % (name, nd, nf, bdir)))
                    else:
                        self.q.put(("log", "【%s】没有找到已下载的旧文件，直接清除记录" % name))
                total, deleted = core.clear_download_records(
                    cfg, sec, platform=core.platform_of(acc))
                self.q.put(("log", "【%s】下载记录已清除：共 %d 个作品，删除 %d 条记录"
                            % (name, total, deleted)))
                self.q.put(("cleared", sec))
            except Exception as exc:
                self.q.put(("log", "[ERROR] 【%s】清除下载记录失败：%s" % (name, exc)))

    def toggle_enabled(self):
        sel = self.selected_accounts()
        if not sel:
            return
        for acc in sel:
            acc["enabled"] = not acc.get("enabled", True)
        self.save_config()
        for acc in sel:
            self.update_row(acc)
        log.info("已切换 %d 个账号的启用状态", len(sel))

    def resolve_selected(self):
        sel = self.selected_accounts()
        if not sel:
            return
        for acc in sel:
            acc["sec_user_id"] = ""
            threading.Thread(target=self.resolve_account, args=(acc,), daemon=True).start()

    def resolve_missing_nicknames(self):
        """以前加过、但昵称没取到的账号，启动后自动补解析一次"""
        for acc in self.accounts:
            if not (acc.get("nickname") or "").strip() and acc.get("sec_user_id"):
                log.info("【%s】缺少昵称，正在补解析…", acc.get("target"))
                threading.Thread(target=self.resolve_account, args=(acc,), daemon=True).start()

    def fetch_nickname(self, dy, sec, unique_id="", raw=""):
        """尽量拿到昵称：主页资料接口 → 免签名用户信息接口"""
        try:
            profile = dy.fetch_profile(sec)
        except Exception as exc:
            log.debug("资料接口失败：%s", exc)
            profile = {}
        nick = str((profile or {}).get("nickname") or "").strip()
        if nick:
            return nick, profile
        uid = unique_id or str((profile or {}).get("unique_id") or "").strip()
        if not uid:
            m = re.search(r"douyin\.com/user/([A-Za-z0-9_.\-]+)", raw or "")
            if m:
                uid = m.group(1)
        if uid and not uid.startswith("MS4wLjAB"):
            try:
                info = dy.user_info_by_unique_id(uid)
            except Exception as exc:
                log.debug("用户信息接口失败：%s", exc)
                info = {}
            nick = str((info or {}).get("nickname") or "").strip()
            if nick:
                return nick, profile or info
        return "", profile

    def resolve_account(self, acc):
        platform = core.platform_of(acc)
        label = core.platform_label(platform)
        try:
            dy = self.get_client(platform)
            raw = (acc.get("target") or "").strip()
            acc["state"] = "待解析"
            self.q.put(("row", acc))
            if platform == "tiktok":
                # TikTok 的 fetch_profile 直接吃 @用户名 / 链接里的用户名 / secUid
                profile = dy.fetch_profile(raw) or {}
                sec = str(profile.get("sec_uid") or "").strip()
                nick = str(profile.get("nickname") or "").strip()
                uid = str(profile.get("unique_id") or "").strip()
                if uid:
                    acc["unique_id"] = uid
                acc["sec_user_id"] = sec
                acc["nickname"] = nick or acc.get("nickname") or ""
                if uid:
                    acc["url"] = "https://www.tiktok.com/@%s" % uid
                    acc["target"] = "@" + uid
            else:
                sec = dy.resolve_sec_uid(raw)
                nick, profile = "", {}
                for attempt in range(3):
                    nick, profile = self.fetch_nickname(dy, sec, acc.get("unique_id"), raw)
                    if nick:
                        break
                    if attempt < 2:
                        time.sleep(0.8 * (attempt + 1))
                acc["sec_user_id"] = sec
                acc["nickname"] = nick or acc.get("nickname") or ""
                uid = str(profile.get("unique_id") or "").strip()
                if uid:
                    acc["unique_id"] = uid
                elif not acc.get("unique_id"):
                    m = re.search(r"douyin\.com/user/([A-Za-z0-9_.\-]+)", raw)
                    if m:
                        acc["unique_id"] = m.group(1)
                acc["url"] = "https://www.douyin.com/user/%s" % (sec or acc.get("unique_id") or "")
                # 用链接添加的账号：列表里显示抖音号，链接放到最后一列
                if raw.lower().startswith(("http://", "https://")) or "douyin.com/" in raw.lower():
                    acc["target"] = acc.get("unique_id") or acc["nickname"] or raw
            acc["state"] = "待监控"
            self.q.put(("row", acc))
            self.q.put(("save", None))  # 保存配置必须在主线程执行
            if acc["nickname"]:
                log.info("解析成功：%s -> %s（%s号 %s）", raw, acc["nickname"], label,
                         acc.get("unique_id") or "-")
            else:
                log.warning("已解析到账号（%s），但昵称接口没返回名字；检查时会再试一次",
                            acc.get("unique_id") or acc.get("sec_user_id") or "")
        except Exception as e:
            acc["state"] = "解析失败"
            self.q.put(("row", acc))
            log.error("解析%s账号 %s 失败：%s", label, acc.get("target"), e)
            if platform == "tiktok":
                log.warning("提示：TikTok 需要本机能访问 tiktok.com，不通时先在设置里配代理，"
                            "也可以直接输入 @用户名 重试")
            else:
                log.warning("提示：抖音号（如 rmrbxmt）比链接更稳，可以直接输入抖音号重试")

    # ------------------------------------------------------------ 设置读写
    @staticmethod
    def _clamp_int(var, spin, default, lo, hi):
        """从 Spinbox/IntVar 读整数并夹到 [lo, hi]；读不出来用 default。"""
        try:
            val = int(float(str(var.get()).strip()))
        except Exception:
            try:
                val = int(float(str(spin.get()).strip())) if spin is not None else default
            except Exception:
                val = default
        return min(hi, max(lo, val))

    def validate_interval(self, show_dialog=True):
        """校验作品/直播检查间隔并写回配置；越界时夹取，作品间隔可弹窗说明。"""
        raw = ""
        try:
            raw = str(self.interval_var.get())
        except Exception:
            try:
                raw = self.interval_spin.get().strip()
            except Exception:
                raw = ""
        val = self._clamp_int(self.interval_var, self.interval_spin,
                              MIN_INTERVAL, MIN_INTERVAL, MAX_INTERVAL)
        try:
            bad = int(float(raw)) < MIN_INTERVAL or int(float(raw)) > MAX_INTERVAL
        except Exception:
            bad = bool(raw)
        if bad and show_dialog:
            messagebox.showwarning(
                "检查间隔超出范围",
                "检查间隔应为 %d ～ %d 秒（%d 秒到 24 小时）。\n\n"
                "当前输入：%s\n已自动调整为 %d 秒。"
                % (MIN_INTERVAL, MAX_INTERVAL, MIN_INTERVAL,
                   raw if raw else "空", val),
                parent=self.root)
        self.interval_var.set(val)
        self.cfg["poll_interval"] = val
        lval = self._clamp_int(self.live_interval_var, self.live_interval_spin,
                               MIN_LIVE_INTERVAL, MIN_LIVE_INTERVAL, MAX_INTERVAL)
        self.live_interval_var.set(lval)
        self.cfg["live_poll_interval"] = lval
        self.cfg["live_separate"] = bool(self.live_separate_var.get())
        return val

    @staticmethod
    def _countdown_text(seconds):
        total = int(max(0, seconds) + 0.999)
        m, sec = divmod(total, 60)
        h, m = divmod(m, 60)
        if h:
            return "%d:%02d:%02d" % (h, m, sec)
        return "%02d:%02d" % (m, sec)

    def countdown_text(self):
        """左下角倒计时文字：作品检查 + 直播检查（独立轮询开启时）。"""
        run = getattr(self, "run_cfg", {}) or {}
        parts = []
        if self.next_check_ts:
            parts.append("下次作品检查 %s" % self._countdown_text(self.next_check_ts - time.time()))
        else:
            parts.append("作品检查进行中…")
        if run.get("live"):
            if run.get("live_separate") and self.next_live_ts:
                parts.append("下次直播检查 %s"
                             % self._countdown_text(self.next_live_ts - time.time()))
            elif not run.get("live_separate"):
                parts.append("直播随作品轮检查")
        return "　·　".join(parts)

    def watch_monitor(self):
        """监控线程意外退出（异常/被杀）时自动重启，最多 3 次。"""
        th = getattr(self, "monitor_thread", None)
        if not getattr(self, "job_running", False) or th is None:
            return
        if th.is_alive() or self.stop_event.is_set():
            return
        n = int(getattr(self, "_monitor_restarts", 0) or 0) + 1
        self._monitor_restarts = n
        if n <= 3:
            log.warning("监控线程意外退出，已自动重启（第 %d 次）", n)
            try:
                self.q.put(("log", "监控线程意外退出，已自动重启（第 %d 次）" % n))
            except Exception:  # noqa: BLE001
                pass
            self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
            self.monitor_thread.start()
        else:
            log.error("监控线程连续 3 次异常退出，已停止监控，请查看 monitor.log 后重新开始")
            self.job_running = False
            self.status_var.set("已停止（异常）")
            self.countdown_var.set("")

    def tick_status(self):
        """右侧状态栏：每秒刷新一次倒计时；同时让「直播录制」面板的时长/大小实时走"""
        try:
            try:
                self.refresh_live_panel()
            except Exception:  # noqa: BLE001
                pass
            override, until = getattr(self, "status_override", ("", 0.0))
            if override and time.time() < until:
                self.status_var.set(override)
                return
            self.watch_monitor()
            if self.monitor_thread and self.monitor_thread.is_alive():
                self.status_var.set("监控中 · 第 %d 轮" % max(self.cycle_count, 1))
                self.countdown_var.set(self.countdown_text())
            elif self.monitor_thread is not None:
                self.status_var.set("已停止")
                self.countdown_var.set("")
            else:
                self.status_var.set("未开始")
                self.countdown_var.set("")
        except Exception:
            pass
        finally:
            self.root.after(1000, self.tick_status)

    def toggle_monitor(self, source="界面"):
        if self.monitor_thread and self.monitor_thread.is_alive():
            # 刚自动开始监控的十几秒里，用户很可能是在点「开始监控」（按钮位置刚变过），
            # 也可能是回车/空格误触了焦点按钮；这种情况先忽略一次，再点才会真停。
            started = float(getattr(self, "_monitor_started_at", 0) or 0)
            if started and time.time() - started < 15 and int(self.cycle_count or 0) == 0:
                log.info("监控刚开始 15 秒内，忽略这次停止请求（来源：%s）；再点一次才会停止", source)
                self.status_override = ("监控刚开始，再点一次才会停止", time.time() + 5)
                return
            self.stop_event.set()
            self.start_btn.config(text="▶ 开始监控")
            self.status_var.set("正在停止…")
            log.info("正在停止监控 ...（来源：%s）", source)
            if self.live_mgr and self.live_mgr.items:
                log.info("停止监控：先把正在录制的直播收尾")
                self.stop_all_records()
            return
        enabled = [a for a in self.accounts if a.get("enabled", True)]
        if not enabled:
            messagebox.showwarning("提示", "请先添加并启用至少一个抖音号")
            return
        self.validate_interval(show_dialog=True)
        self.save_config()
        if not os.path.isdir(self.current_douk_dir()):
            messagebox.showerror("错误", "数据目录不可用，请检查程序所在位置是否可写")
            return
        self.stop_event.clear()
        self._monitor_started_at = time.time()
        self._monitor_restarts = 0
        self.next_check_ts = 0.0
        self.next_live_ts = 0.0     # 开始监控立即查一次开播，之后按 live_poll_interval
        self.save_config()
        # 界面上能读的 Tk 变量只能在主线程读，先拍个快照给后台线程用
        self.run_cfg = {
            "live": bool(self.live_var.get()),
            "interval": int(self.cfg["poll_interval"]),
            "live_interval": int(self.cfg["live_poll_interval"]),
            "live_separate": bool(self.cfg["live_separate"]),
            "data_dir": self.cfg.get("data_dir") or "",
            "scope": str(self.cfg.get("monitor_scope") or "both"),
        }
        self.start_btn.config(text="■ 停止监控")
        self.status_var.set("监控中")
        self.session = {
            "start": time.time(),
            "rounds": 0,
            "checks": 0,
            "errors": 0,
            "accounts": [a.get("nickname") or a.get("target") for a in enabled],
            "downloads": [],
            "lives": [],
        }
        self.session_shown = False
        self.job_running = True
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()
        names = "、".join((a.get("nickname") or a.get("target"))[:8]
                          for a in enabled[:5])
        if len(enabled) > 5:
            names += "（等）"
        log.info("开始监控（来源：%s）", source)
        log.info("开始监控 %d 个账号（%s），每 %d 秒查一次（独立直播轮询 %d 秒）",
                 len(enabled), names, int(self.run_cfg.get("interval") or 0),
                 int(self.run_cfg.get("live_interval") or 0))
        if bool(self.run_cfg.get("live_separate")) and bool(self.run_cfg.get("live")):
            log.info("检测间隔：作品 %d 秒 / 直播 %d 秒（独立轮询，作品接口冷却时开播检测照常）",
                     int(self.run_cfg.get("interval") or 0),
                     int(self.run_cfg.get("live_interval") or 0))
        else:
            log.info("检测间隔：%d 秒（作品与直播同一轮）", int(self.run_cfg.get("interval") or 0))
        if bool(self.cfg.get("audit_on_start", True)) and str(self.run_cfg.get("scope") or "both") != "live":
            full_on_start = bool(self.cfg.get("auto_start_full", False)) and source == "启动自动"
            self.start_audit(enabled, trigger="启动全量核账" if full_on_start else "开始监控")
        if self.silent():
            self.root.iconify()
            log.info("静默模式：已最小化到任务栏，发现新作品/开播时只写日志与文件")

    def monitor_loop(self):
        try:
            self._monitor_loop_body()
        except BaseException:  # noqa: BLE001 - 线程里没人接异常，必须自己记录
            log.error("监控线程异常退出：%s", traceback.format_exc())
            # job_running 保持 True，让 tick_status 里的看门狗自动重启；连挂 3 次才停
            try:
                self.q.put(("log", "监控线程异常退出，正在自动重启…"))
            except Exception:  # noqa: BLE001
                pass

    def _monitor_loop_body(self):
        sess = self.session
        while not self.stop_event.is_set():
            t0 = time.time()
            self.next_check_ts = 0.0
            self.cycle_count += 1
            if self.session is not None:
                self.session["rounds"] += 1
            stats = {}
            # 作品列表接口被抖音风控静默限流时（HTTP 200 + 空 body），整个冷却期
            # 一个请求都不发，直接睡到冷却结束再自动试探恢复；退避时长由网络层自适应。
            rate_left = core.rate_limit_remaining()
            if rate_left > 0:
                wait = max(10, min(rate_left, 3600))
                stats = {"rate_wait": wait}
            else:
                try:
                    stats = self.monitor_cycle() or {}
                except Exception as e:
                    if self.session is not None:
                        self.session["errors"] += 1
                    log.error("本轮检查出错：%s", e)
                    log.debug("%s", traceback.format_exc())
                cost = time.time() - t0
                wait = max(10, int(self.run_cfg.get("interval") or self.cfg["poll_interval"]) - cost)
            self.next_check_ts = time.time() + wait
            try:
                self.log_round_summary(stats, time.time() - t0, wait)
            except Exception as exc:  # noqa: BLE001
                log.debug("轮次汇总日志失败：%s", exc)
            # 等待下一次作品检测；期间按「直播检测间隔」独立插入直播轮。
            # 作品列表接口风控冷却时（rate_wait）照样跑 live_cycle，开播检测不中断。
            self._idle(time.time() + wait)
        self.job_running = False
        self.q.put(("status", "已停止"))
        self.q.put(("session_end", sess))
    def monitor_cycle(self):
        """跑一轮：逐个账号查直播 + 新作品。返回本轮计数，供 monitor_loop 打汇总行。"""
        accounts = [a for a in self.accounts if a.get("enabled", True)][
            : int(self.cfg["max_accounts_per_round"])]
        t0 = time.time()
        checked = new_total = files_total = failed = 0
        rate_hit = False
        # 独立直播轮询开启时，作品轮不做直播检测（交给 live_cycle 按自己的间隔）
        separate = bool(self.run_cfg.get("live_separate",
                                         self.cfg.get("live_separate", True)))
        live_on = bool(self.run_cfg.get("live", self.cfg["live_check"]))
        do_live = not (separate and live_on)
        deep_accounts = deep_missing = deep_posts = deep_full = 0
        deep = bool(self.cfg.get("audit_every_round", True))
        # 全量翻页开销大，按间隔做（默认每 6 轮/30 分钟），开始监控与手动刷新仍立即核账
        audit_gap = max(1, int(self.cfg.get("audit_round_interval") or 1))
        if deep and audit_gap > 1 and int(self.cycle_count or 0) % audit_gap != 0:
            deep = False
        for acc in accounts:
            if self.stop_event.is_set():
                break
            try:
                client = self.get_client(core.platform_of(acc))
                client.reload_cookie()   # 每次重读 Cookie，重新登录后无需重启客户端
                with self.check_lock:
                    res = self.check_account(client, acc, do_live=do_live) or {}
                checked += 1
                new_total += int(res.get("items") or 0)
                files_total += int(res.get("files") or 0)
                if deep and not self.stop_event.is_set():
                    # 每轮都做全量核账：隐藏作品后来公开时发布时间是旧的，增量翻页抓不到
                    got = self.audit_account(acc, trigger="每轮检查", quiet=True) or {}
                    deep_accounts += 1
                    if got.get("full"):
                        deep_full += 1
                    deep_posts += int(got.get("posts") or 0)
                    deep_missing += int(got.get("missing") or 0)
                    files_total += int(got.get("files") or 0)
                    new_total += int(got.get("items") or 0)
                acc["state"] = acc.get("state", "监控中")
            except core.RateLimited as e:
                # 风控不是「账号出错」，别每个账号刷一条 ERROR；打一条 WARNING 就停，
                # 冷却由网络层接管，monitor_loop 下一轮会整轮跳过直到自动恢复。
                rate_hit = True
                acc["state"] = "接口限流"
                log.warning("抖音作品列表接口触发限流，本轮停止检查：%s"
                            "（程序会自动降频，冷却结束后自动恢复，无需操作）", e)
                break
            except Exception as e:
                failed += 1
                acc["state"] = f"出错: {str(e)[:18]}"
                log.error("账号 %s 检查失败：%s", acc.get("nickname") or acc.get("target"), e)
                log.debug("%s", traceback.format_exc())
            acc["last_check"] = time.strftime("%H:%M:%S")
            self.q.put(("row", acc))
        try:
            self.save_state()  # 一轮结束写一次，避免每个账号都重写整个状态文件
        except Exception as exc:  # noqa: BLE001
            log.warning("一轮结束存状态失败（不影响继续监控）：%s", exc)
        self.q.put(("save", None))
        return {"checked": checked, "new": new_total, "files": files_total,
                "failed": failed, "deep": deep_accounts, "deep_missing": deep_missing,
                "deep_full": deep_full, "deep_posts": deep_posts, "rate_hit": rate_hit,
                "cost": time.time() - t0}
    def check_account(self, dy, acc, do_live=True, ask_history=True):
        """检查一个账号。ask_history=False 时不弹「首次运行是否下载已有作品」，
        只把现有作品登记为已见（手动刷新走这条，避免刷新被几百个作品的下载卡住）。"""
        if not acc.get("sec_user_id"):
            sec = dy.resolve_sec_uid(acc["target"])
            acc["sec_user_id"] = sec
        sec = acc["sec_user_id"]
        opt_live = bool(self.run_cfg.get("live", self.cfg["live_check"]))
        scope = (self.account_scope(acc)
                 or str(self.run_cfg.get("scope") or self.cfg.get("monitor_scope") or "both"))
        do_posts = scope != "live"
        # 独立直播轮询开启时，作品检测轮不做直播（交给 live_cycle 按自己的间隔查）；
        # 全局「检测直播」开关仍然生效。手动刷新不传 do_live → 默认 True，两类都查。
        do_live = bool(do_live) and opt_live and scope != "posts"
        st_tmp = self.state["accounts"].get(sec) or {}
        need_profile = ((not st_tmp.get("first_run_done", False)) or do_live
                        or not acc.get("uid") or not acc.get("nickname"))
        profile = dy.fetch_profile(sec) if need_profile else {}
        nickname = profile.get("nickname") or acc.get("nickname") or ""
        if not nickname:
            nickname, _ = self.fetch_nickname(dy, sec, acc.get("unique_id"), acc.get("target"))
            if nickname:
                log.info("【%s】昵称解析补上了：%s", acc.get("target"), nickname)
        acc["nickname"] = nickname or acc.get("nickname") or ""
        # 记下数字 uid：DouK 的账号作品目录名是 Volume/UID<uid>_<昵称>_发布作品
        acc["uid"] = str(profile.get("uid") or acc.get("uid") or "")

        default_st = {"seen": {}, "last_live_room": "", "live_active": False,
                      "first_run_done": False}
        st = self.state["accounts"].setdefault(sec, dict(default_st))
        # 刚添加的账号：即使以前监控过，也按「新账号」重新走一次首次检查（会弹窗询问）
        if acc.pop("is_new", False):
            st = dict(default_st)
            self.state["accounts"][sec] = st
            self.q.put(("save", None))
        first_run = not st.get("first_run_done", False)

        cfg = dict(self.cfg)
        cfg["sec_user_id"] = sec
        cfg["save_dir"] = self.account_save_dir(acc)
        # 首次运行下载的「已有作品」按历史作品处理：放进该账号的历史下载文件夹
        if first_run and not str(acc.get("save_dir") or "").strip():
            cfg["save_dir"] = core.history_account_dir(cfg, acc)
        cfg["uid"] = acc.get("uid") or ""
        cfg["nickname"] = nickname
        cfg["unique_id"] = acc.get("unique_id") or ""
        cfg["download_existing_on_first_run"] = False   # 首次检查只登记，不再询问/自动下历史
        cfg["live_check"] = bool(do_live)

        report = {}
        new_items = core.check_posts(cfg, dy, st, first_run, report=report) if do_posts else []
        was_live = bool(st.get("live_active"))
        if cfg["live_check"]:
            core.check_live(cfg, dy, st, profile, report=report)
        if cfg["live_check"]:
            live_key = str(acc.get("sec_user_id") or sec or acc.get("target") or "")
            self.sync_live(acc, live_key, nickname, report.get("live"), was_live)
        # 监控/刷新时新作品下载完也要进右上「本次下载」面板，不能只写日志
        if report.get("items") or report.get("files"):
            self.q.put(("downloaded", {
                "nickname": nickname,
                "target": acc.get("target"),
                "items": report.get("items") or [],
                "files": report.get("files") or [],
            }))
        self.record_session(acc, nickname, report)

        # 没有新作品时不写 INFO（5 分钟一轮 × 5 个账号 = 一天 1440 行噪音），
        # 改由 monitor_cycle 输出一行「第 N 轮检查完成：…」汇总；有东西才逐账号记。
        if new_items:
            log.info("【%s】检查完成：新作品 %d 个%s", nickname, len(new_items),
                     "，下载 %d 个文件" % len(report.get("files") or [])
                     if report.get("files") else "")
        else:
            log.debug("【%s】检查完成：没有新作品", nickname)
        st["first_run_done"] = True
        acc["state"] = "直播中" if st.get("live_active") else "监控中"
        return {"items": len(new_items), "files": len(report.get("files") or []),
                "live": bool(st.get("live_active"))}

    MAX_SESSION_ITEMS = 300

    def record_session(self, acc, nickname, report):
        """把本轮结果记进本次监控的统计，供「停止监控」后的总结使用"""
        sess = self.session
        if sess is None:
            return
        sess["checks"] += 1
        items = report.get("items") or []
        files = report.get("files") or []
        if items or files:
            kinds = []
            for it in items:
                k = it.get("kind") or "作品"
                if k not in kinds:
                    kinds.append(k)
            sess["downloads"].append({
                "time": time.time(),
                "account": nickname or acc.get("target"),
                "target": acc.get("target"),
                "kinds": "、".join(kinds) or "作品",
                "items": items,
                "files": files,
            })
            del sess["downloads"][:-self.MAX_SESSION_ITEMS]
        live = report.get("live")
        if live:
            sess["lives"].append({
                "time": time.time(),
                "account": nickname or acc.get("target"),
                "title": live.get("title") or "",
                "room": live.get("room") or "",
                "quality": live.get("quality") or "",
                "quality_label": live.get("quality_label") or "",
                "txt": live.get("txt") or "",
            })
            del sess["lives"][:-self.MAX_SESSION_ITEMS]

    # ------------------------------------------------------------ 操作按钮
    def open_data_dir(self):
        """打开数据目录（作品、下载记录、登录信息都在里面）"""
        self.open_path(self.current_douk_dir(), create=True)

    def current_douk_dir(self):
        """数据目录的绝对路径（保留旧方法名，界面其它地方还在用）"""
        try:
            return os.path.abspath(core.data_root(self.cfg))
        except Exception as exc:
            log.warning("数据目录不可用：%s", exc)
            return ""

    def account_download_dirs(self, acc):
        """某个账号可能存放作品的目录（按优先级）：账号指定目录 > DouK 账号作品目录 > Volume/Download"""
        cfg = dict(self.cfg)
        cfg["data_dir"] = self.cfg.get("data_dir") or ""
        cfg["save_dir"] = self.account_save_dir(acc)
        dirs = []
        if cfg["save_dir"]:
            dirs.append(cfg["save_dir"])
        try:
            dirs.extend(core.account_storage_dirs(cfg, acc.get("nickname") or "",
                                                  acc.get("uid") or ""))
        except Exception as e:
            log.warning("查找账号作品目录失败：%s", e)
        dirs.append(os.path.join(core.douk_volume_dir(cfg), "Download"))
        out, seen = [], set()
        for d in dirs:
            if not d:
                continue
            full = os.path.abspath(os.path.expanduser(d))
            key = os.path.normcase(full)
            if key in seen:
                continue
            seen.add(key)
            out.append(full)
        return out

    @staticmethod
    def _dir_weight(path):
        """目录“有内容”的权重：文件数优先，其次最新文件时间"""
        try:
            files = [os.path.join(path, n) for n in os.listdir(path)]
        except OSError:
            return (-1, 0.0)
        files = [p for p in files if os.path.isfile(p)]
        newest = 0.0
        for p in files:
            try:
                newest = max(newest, os.path.getmtime(p))
            except OSError:
                pass
        return (len(files), newest)

    def resolve_download_dir(self):
        """算出最该打开的作品目录：有文件的账号目录里最新的那个；一个都没有就返回 Volume"""
        douk = self.current_douk_dir()
        if not douk or not os.path.isdir(douk):
            return ""
        # 没选中账号时看全部账号，尽量把“真的有文件”的目录打开给用户
        sel = self.selected_accounts() or list(self.accounts)
        cands = []
        for acc in sel:
            cands.extend(self.account_download_dirs(acc))
        if not cands:
            vol = os.path.join(douk, "Volume")
            cands = [os.path.join(vol, "Download"), vol]
        uniq, seen = [], set()
        for d in cands:
            key = os.path.normcase(os.path.abspath(d))
            if key not in seen:
                seen.add(key)
                uniq.append(os.path.abspath(d))
        existing = [d for d in uniq if os.path.isdir(d)]
        nonempty = [d for d in existing if self._dir_weight(d)[0] > 0]
        if nonempty:
            return max(nonempty, key=self._dir_weight)
        # 该账号还没下载过作品：返回 Volume（能看到所有账号的作品目录），不让用户面对空文件夹
        vol = os.path.join(douk, "Volume")
        if os.path.isdir(vol):
            return vol
        return uniq[0] if uniq else vol

    def open_download_dir(self):
        """打开作品实际保存的目录；资源管理器被系统拒绝时自动改用内置浏览窗口"""
        douk = self.current_douk_dir()
        if not douk or not os.path.isdir(douk):
            messagebox.showerror(
                "无法打开下载目录",
                "数据目录不存在或不正确：\n%s\n\n请先在「设置」里确认数据目录。"
                % (douk or "（空）"))
            return
        target = self.resolve_download_dir()
        if not target:
            messagebox.showerror("无法打开下载目录", "没有找到可用的作品目录。")
            return
        sel = self.selected_accounts()
        if sel:
            log.info("【%s】作品目录：%s", sel[0].get("nickname") or sel[0].get("target"), target)
        else:
            log.info("作品目录：%s", target)
        self.open_path(target, create=True)

    def browse_download_files(self):
        """不用资源管理器，直接在软件里按文件夹浏览作品文件"""
        douk = self.current_douk_dir()
        if not douk or not os.path.isdir(douk):
            messagebox.showerror(
                "无法浏览作品",
                "数据目录不存在或不正确：\n%s\n\n请先在「设置」里确认数据目录。"
                % (douk or "（空）"))
            return
        target = self.resolve_download_dir()
        try:
            os.makedirs(target, exist_ok=True)
        except OSError:
            pass
        log.info("内置浏览：%s", target)
        self.open_browser(target)

    def ask_main(self, title, question):
        """在工作线程里请求主线程弹出一个 是/否 对话框，并等待用户选择。

        **只能在后台线程里调**：它把问题塞进队列后阻塞等主线程回答。
        如果自己就在主线程（菜单/按钮回调里），那就是 UI 线程自己等自己 —— 界面直接卡死
        （v1.5.2 修的 bug：设置 → 补下载最近 3 天漏掉的作品… 一点就卡住）。
        这里加一道判断：主线程调用时直接弹模态框，不走队列。
        """
        if self.silent():
            log.info("静默模式：不弹问题框，自动按「否」处理（%s）", title)
            return False
        if threading.current_thread() is threading.main_thread():
            log.debug("ask_main 在主线程被调用，改为直接弹窗：%s", title)
            try:
                return bool(messagebox.askyesno(title, question, parent=self.root))
            except Exception as exc:  # noqa: BLE001
                log.warning("弹窗失败：%s", exc)
                return False
        msg = {"kind": "ask", "title": title, "question": question,
               "result": [], "event": threading.Event()}
        self.q.put(("ask", msg))
        msg["event"].wait()
        return bool(msg["result"][0]) if msg["result"] else False

    def report_callback_exception(self, exc, val, tb):
        log.error("界面操作异常：%s", val)
        log.debug("".join(traceback.format_exception(exc, val, tb)))
        if self.silent():
            return
        try:
            messagebox.showerror("出错了", "%s\n\n详细信息见 monitor.log" % val)
        except Exception:
            pass

    def shell_open_dir(self, path):
        """在资源管理器里打开目录，返回 (ok, err)。

        顺序：os.startfile（走系统文件夹关联，普通桌面环境最稳）→ explorer.exe。
        拒绝访问（WinError 5，多见于受限账户/沙箱）时不再启动 explorer：那种环境下
        拉起的 explorer.exe 会直接弹「应用程序无法正常启动」，反而吓人。
        """
        err = ""
        try:
            os.startfile(path)  # noqa: S606  (Windows)
            return True, ""
        except OSError as exc:
            err = str(exc)
            log.warning("startfile 打开失败（%s），改用 explorer：%s", exc, path)
            if (getattr(exc, "winerror", None) or getattr(exc, "errno", None)) == 5:
                return False, err
        except Exception as exc:  # pragma: no cover - 兜底
            err = str(exc)
            log.warning("startfile 打开失败（%s），改用 explorer：%s", exc, path)
        explorer = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")
        if not os.path.isfile(explorer):
            explorer = "explorer.exe"
        try:
            # 干净的 PATH：避免子进程加载到 DouK 的 DLL
            subprocess.Popen([explorer, os.path.normpath(path)], env=core.child_env(),
                             close_fds=True)
            return True, ""
        except OSError as exc:
            log.error("explorer 打开失败（%s）：%s", exc, path)
            return False, "%s；%s" % (err, exc)

    def show_path_help(self, path, err=""):
        """自动打开失败时：把路径复制到剪贴板，并提示手动打开（不再让系统弹错误框）"""
        copied = False
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(path)
            copied = True
        except Exception:
            pass
        log.warning("无法自动打开目录：%s（%s）", path, err)
        tip = "路径已复制到剪贴板，" if copied else ""
        self.popup(
            "showwarning", "无法自动打开目录",
            "系统拒绝打开资源管理器（可能是在受限账户/沙箱里运行）。\n\n"
            "目录：\n%s\n\n%s把它粘贴到资源管理器的地址栏或 Win+R 里手动打开。\n"
            "提示：直接双击 抖音监控.exe（不要在受限环境里启动）通常就能正常打开。"
            % (path, tip))
        self.status_override = ("未能打开目录，路径已复制", time.time() + 10)
        try:
            self.status_var.set(self.status_override[0])
        except Exception:
            pass

    def open_browser(self, path, title="浏览作品文件"):
        """打开内置文件浏览窗口（同一个窗口复用，切换目录即可）"""
        path = os.path.abspath(os.path.expanduser(str(path or "").strip()))
        if not os.path.isdir(path):
            messagebox.showwarning("目录不存在", "目录还不存在：\n%s" % path)
            return None
        win = getattr(self, "browser_win", None)
        if win is not None and getattr(win, "alive", False):
            try:
                win.enter(path)
                win.win.deiconify()
                win.win.lift()
                win.win.focus_force()
                return win
            except Exception:
                self.browser_win = None
        self.browser_win = DirBrowser(self.root, path, title=title,
                                      shell_open=self.shell_open_dir)
        return self.browser_win

    def open_path(self, path, create=False):
        """打开目录：不存在时按需创建；打开失败给出可复制的路径提示"""
        path = os.path.abspath(os.path.expanduser(str(path or "").strip()))
        if not str(path or "").strip():
            messagebox.showerror("打开失败", "目录路径为空，请先在「设置」里配置。")
            return
        try:
            if not os.path.isdir(path):
                if create:
                    os.makedirs(path, exist_ok=True)
                else:
                    messagebox.showwarning("目录不存在", "目录还不存在：\n%s" % path)
                    return
            ok, err = self.shell_open_dir(path)
            if not ok:
                # 系统不允许开资源管理器（受限账户/沙箱）：改用内置浏览窗口，保证能看能开
                log.warning("资源管理器打不开（%s），已改用内置浏览：%s", err, path)
                self.open_browser(path)
                self.status_override = ("资源管理器打不开，已用内置浏览打开", time.time() + 8)
                return
            try:
                self.root.lift()
            except Exception:
                pass
            try:
                count = len([n for n in os.listdir(path) if os.path.isfile(os.path.join(path, n))])
            except OSError:
                count = -1
            log.info("已打开目录：%s（%s 个文件）", path,
                     "?" if count < 0 else count)
            try:
                self.status_var.set("已打开：%s" % path)
            except Exception:
                pass
            self.status_override = ("已打开：%s" % path, time.time() + 8)
        except Exception as e:
            log.error("打开目录失败：%s（%s）", path, e)
            self.show_path_help(path, e)

    # ------------------------------------------------------------ 监控总结
    @staticmethod
    def fmt_duration(seconds):
        seconds = max(0, int(seconds))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return "%d 小时 %d 分 %d 秒" % (h, m, s)
        if m:
            return "%d 分 %d 秒" % (m, s)
        return "%d 秒" % s

    def build_session_summary(self):
        """生成「本次监控总结」文本：时长、刷新次数、下载内容与时间、直播情况"""
        sess = self.session or {}
        start = sess.get("start") or time.time()
        end = time.time()
        downloads = list(sess.get("downloads") or [])
        lives = list(sess.get("lives") or [])
        rounds = int(sess.get("rounds") or 0)
        checks = int(sess.get("checks") or 0)
        errors = int(sess.get("errors") or 0)
        accounts = [str(a) for a in (sess.get("accounts") or []) if a]
        total_files = sum(len(d.get("files") or []) for d in downloads)
        total_items = sum(len(d.get("items") or []) for d in downloads)

        lines = []
        lines.append("监控时长：%s" % self.fmt_duration(end - start))
        lines.append("起止时间：%s — %s" % (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start)),
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(end))))
        lines.append("刷新轮次：%d 轮（账号检查 %d 次）" % (rounds, checks))
        if accounts:
            lines.append("监控账号：%s" % "、".join(accounts))
        lines.append("下载情况：%d 个作品，%d 个文件" % (total_items, total_files))
        lines.append("直播情况：%s" % ("检测到 %d 次开播" % len(lives) if lives
                                       else "本次未检测到开播"))
        if errors:
            lines.append("异常次数：%d 次（详见 monitor.log）" % errors)
        lines.append("")
        lines.append("=" * 62)
        lines.append("下载内容与时间")
        lines.append("=" * 62)
        if not downloads:
            lines.append("（本次监控没有下载新作品）")
        for d in downloads:
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d.get("time") or start))
            lines.append("")
            lines.append("[%s] %s · %s · %d 个作品 / %d 个文件" % (
                when, d.get("account") or "", d.get("kinds") or "作品",
                len(d.get("items") or []), len(d.get("files") or [])))
            if not (d.get("files") or []):
                lines.append("    （没有生成新文件：可能已有下载记录，详见 monitor.log）")
            for it in d.get("items") or []:
                lines.append("    * %s  %s  %s" % (it.get("kind") or "作品",
                                                   it.get("aweme_id") or "",
                                                   it.get("desc") or ""))
                if it.get("url"):
                    lines.append("      %s" % it["url"])
            for f in d.get("files") or []:
                try:
                    mt = time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(f)))
                except OSError:
                    mt = "--:--:--"
                lines.append("    - [%s] %s" % (mt, os.path.basename(f)))
        if lives:
            lines.append("")
            lines.append("=" * 62)
            lines.append("直播拉流地址")
            lines.append("=" * 62)
            for lv in lives:
                lines.append("")
                lines.append("[%s] %s 开播：%s" % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(lv.get("time") or start)),
                    lv.get("account") or "", lv.get("title") or ""))
                lines.append("    房间：%s  画质：%s（%s）" % (
                    lv.get("room") or "", lv.get("quality") or "", lv.get("quality_label") or ""))
                if lv.get("txt"):
                    lines.append("    地址文件：%s" % lv["txt"])
        return "\n".join(lines)

    def show_session_summary(self):
        """停止监控后弹出总结日志，并写入 monitor.log 与 监控总结_时间.txt"""
        if self.session is None or self.session_shown:
            return
        self.session_shown = True
        text = self.build_session_summary()
        log.info("=" * 62)
        log.info("本次监控已停止，统计开始 ...")
        for line in text.splitlines():
            if line.strip():
                log.info("  %s", line)
        log.info("本次监控总结结束")
        fn = ""
        try:
            fn = os.path.join(BASE_DIR, "监控总结_%s.txt" % time.strftime("%Y%m%d_%H%M%S"))
            with open(fn, "w", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception as e:
            log.warning("保存监控总结失败：%s", e)
            fn = ""

        if self.silent():
            log.info("静默模式：总结窗口不弹出，已保存到 %s", fn or "（未能保存）")
            self.status_override = ("本次总结已保存（静默模式）", time.time() + 12)
            return

        win = tk.Toplevel(self.root)
        win.title("本次监控总结")
        win.geometry("%dx%d" % (hi_dpi.scale_px(780), hi_dpi.scale_px(580)))
        win.minsize(hi_dpi.scale_px(560), hi_dpi.scale_px(360))
        hi_dpi.apply_tk_scaling(win)
        win.transient(self.root)
        frame = ttk.Frame(win, padding=8)
        frame.pack(fill="both", expand=True)
        box = scrolledtext.ScrolledText(frame, wrap="word", font=("Microsoft YaHei UI", 9))
        box.pack(fill="both", expand=True)
        box.insert("1.0", text)
        box.configure(state="disabled")
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(8, 0))

        def close():
            win.destroy()

        def save_as():
            path = filedialog.asksaveasfilename(
                parent=win, title="保存监控总结", defaultextension=".txt",
                initialfile=os.path.basename(fn) if fn else "监控总结.txt",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
            if not path:
                return
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text + "\n")
                log.info("监控总结已另存为：%s", path)
            except Exception as e:
                messagebox.showerror("保存失败", str(e), parent=win)

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            log.info("监控总结已复制到剪贴板")

        ttk.Label(bar, text=("已自动保存：" + fn) if fn else "（未能自动保存到文件）",
                  foreground="#666").pack(side="left")
        ttk.Button(bar, text="复制", command=copy).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="另存为…", command=save_as).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="关闭", command=close).pack(side="right")
        win.bind("<Escape>", lambda e: close())
        win.lift()

    # ------------------------------------------------------------ 队列刷新
    def pump_queue(self):
        try:
            if not self.root.winfo_exists():
                return
        except Exception:
            return
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.append_log(payload)
                elif kind == "row":
                    self.update_row(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "session_end":
                    if payload is None or payload is self.session:
                        self.show_session_summary()
                elif kind == "ask":
                    msg = payload
                    try:
                        answer = messagebox.askyesno(msg.get("title", "提示"),
                                                     msg.get("question", ""),
                                                     parent=self.root)
                    except Exception as exc:
                        log.warning("弹窗失败：%s", exc)
                        answer = False
                    msg["result"].append(bool(answer))
                    msg["event"].set()
                elif kind == "live_seen":
                    info = payload or {}
                    key = info.get("key")
                    if key:
                        if info.get("live"):
                            self.live_info[key] = info
                        else:
                            self.live_info.pop(key, None)
                        self.refresh_live_panel()
                elif kind == "live_start":
                    self.on_live_start(payload or {})
                elif kind == "live_stop":
                    self.on_live_stop(payload or {})
                elif kind == "history_done":
                    self.history_busy = False
                    try:
                        self.history_btn.config(state="normal")
                    except Exception:
                        pass
                    if self.monitor_thread and self.monitor_thread.is_alive():
                        self.status_var.set("监控中")
                    else:
                        self.status_var.set("未开始")
                elif kind == "links_done":
                    self.link_busy = False
                    info = payload or {}
                    if info.get("watch"):
                        self.watch_count += int(info.get("count") or 0)
                        self.refresh_watch_info()
                    if str(self.status_override[0]).startswith("单独下载"):
                        self.status_override = ("", 0.0)
                    try:
                        if self.link_win is not None and self.link_win.winfo_exists():
                            self.link_btn.config(state="normal")
                            if info.get("ok"):
                                self.link_status_var.set("完成：新增 %d 个文件" % info.get("count", 0))
                                if self.link_clear_var.get():
                                    self.link_text.delete("1.0", "end")
                                    self._link_count()
                            else:
                                self.link_status_var.set("失败：%s" % info.get("error", ""))
                    except Exception:
                        pass
                    try:
                        running = bool(self.monitor_thread and self.monitor_thread.is_alive())
                        self.status_var.set("监控中" if running else "未开始")
                    except Exception:
                        pass
                    self._watch_flush()
                elif kind == "cookie_result":
                    self.on_cookie_result(payload)
                elif kind == "login_status":
                    self.on_login_status(payload)
                elif kind == "login_done":
                    self.on_login_done(payload)
                elif kind == "cleared":
                    sec = payload
                    self.state["accounts"][sec] = {"seen": {}, "last_live_room": "",
                                                   "live_active": False,
                                                   "first_run_done": False}
                    try:
                        self.save_state()
                    except Exception:
                        pass
                    for acc in self.accounts:
                        if acc.get("sec_user_id") == sec:
                            acc["state"] = "待监控"
                            self.update_row(acc)
                elif kind == "refresh_done":
                    self.on_refresh_done(payload or {})
                elif kind == "downloaded":
                    self.on_downloaded(payload)
                elif kind == "backfill_done":
                    self.on_backfill_done(payload)
                elif kind == "audit_done":
                    self.on_audit_done(payload)
                elif kind == "save":
                    try:
                        self.save_config()
                    except Exception:
                        pass
                elif kind == "tray":
                    self.on_tray_command(payload)
        except queue.Empty:
            pass
        self.root.after(200, self.pump_queue)

    OK_WORDS = ("下载结束", "下载完成", "核账完成", "补下载完成", "解析成功",
                "已开始录制", "回放已保存", "拉流地址已保存", "保存成功")

    # 默认「概要日志」只放行这些词：检测结果、下载结果、直播录制、以及核账/补下载结果。
    # 其余技术细节（连接、冷却、路径、设置项）只写文件日志，不刷界面。
    EVENT_WORDS = ("检查完成", "检测完成", "本轮检查", "下载完成", "下载结束",
                   "新增文件", "开始录制", "录制结束", "录制中断", "录制失败",
                   "这次不录", "回放已保存", "核账完成", "核账开始",
                   "补下载完成", "解析成功", "拉流地址已保存", "监控总结",
                   "历史下载结束", "下载进度", "开始获取作品列表",
                   "监控已停止", "停止监控", "开始监控",
                   "直播流已结束", "限流", "风控", "没能跑")

    def append_log(self, line):
        """往日志面板写一行：按级别上色、连续重复折叠、可只看提醒/错误。

        级别不进正文（[信息] 直接删掉，[细节] 缩进两格），颜色即级别：
        橙=注意、红=错误、绿=结果、灰=细节。
        """
        tag = ""
        if "[错误]" in line or "[严重]" in line:
            tag = "err"
        elif "[注意]" in line:
            tag = "warn"
        elif any(w in line for w in self.OK_WORDS):
            tag = "ok"
        elif "[细节]" in line:
            tag = "dim"
        if getattr(self, "log_summary_only", False) and tag not in ("warn", "err"):
            # 概要模式：只留用户关心的事件，其余交给 monitor.log
            if not any(w in line for w in self.EVENT_WORDS):
                return
        if getattr(self, "log_warn_only", False) and tag not in ("warn", "err"):
            return
        show = line.replace("[信息] ", "", 1)
        if "[细节]" in show:
            show = "  " + show
        self.log_text.configure(state="normal")
        key = line[9:] if len(line) > 9 else line      # 去掉 "HH:MM:SS "
        if key and key == getattr(self, "_log_key", None) and self.log_text.index("end-1c") != "1.0":
            self._log_n = getattr(self, "_log_n", 1) + 1
            # 改写最后一行，标出「重复 ×N」而不是刷屏
            self.log_text.delete("end-2l", "end-1l")
            self.log_text.insert("end", "%s（重复 ×%d）\n" % (show, self._log_n), tag)
        else:
            self._log_key, self._log_n = key, 1
            self.log_text.insert("end", show + "\n", tag)
        # 只保留最近 3000 行，避免占用内存
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 3000:
            self.log_text.delete("1.0", f"{lines - 3000}.0")
        if getattr(self, "log_autoscroll", True):
            self.log_text.see("end")
        self.log_text.configure(state="disabled")


    def on_close(self, force=False):
        """点窗口 ×：按「关闭窗口时」的设置退出或最小化；force=True 时一定退出"""
        if not force and self.close_action() == "minimize":
            self.minimize_to_background()
            return
        self.stop_event.set()
        try:
            if self.live_mgr and self.live_mgr.items:
                self.live_mgr.stop_all(reason="关闭程序")
        except Exception:  # noqa: BLE001
            pass
        try:
            self.save_config()
            self.save_state()
        except Exception:
            pass
        try:
            if getattr(self, "tray", None):
                self.tray.destroy()
                self.tray = None
        except Exception:
            pass
        self.root.destroy()

    def account_save_dir(self, acc):
        """账号实际生效的保存位置：该账号单独设置 > 全局默认 > 空（用数据目录）"""
        return str(acc.get("save_dir") or "").strip() or self.default_save_dir()

    def apply_theme(self, name, save=True):
        """切换浅色 / 深色：存配置后整界面重建，账号与录制器都不受影响"""
        name = "dark" if str(name).lower() in ("dark", "深色") else "light"
        self.theme = name
        self.colors = dict(self.THEMES[name])
        self.COLORS = self.colors
        if save:
            self.cfg["theme"] = name
            self.save_config()
        try:
            self.rebuild_ui()
        except Exception as exc:  # noqa: BLE001
            log.error("切换界面外观失败：%s", exc)
            log.debug("%s", traceback.format_exc())
            return
        if getattr(self, "theme_var", None) is not None:
            try:
                self.theme_var.set(name)
            except Exception:  # noqa: BLE001
                pass
        log.info("界面外观已切换：%s", "深色" if name == "dark" else "浅色")

    def clean_old_files(self):
        """菜单：手动清理旧作品"""
        days = simpledialog.askinteger("清理旧作品", "删除多少天以前的旧作品文件？（0 = 只清理失效记录）",
                                       initialvalue=int(self.cfg.get("auto_clean_days") or 30),
                                       minvalue=0, maxvalue=3650, parent=self.root)
        if days is None:
            return
        try:
            preview = core.cleanup_data(self.cfg, days=days, dry_run=True)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("清理失败", "统计旧文件时出错：%s" % exc, parent=self.root)
            return
        if not messagebox.askyesno(
                "确认清理",
                "将删除 %d 个文件，释放约 %s；同时清理 %d 条失效记录。\n继续？"
                % (preview["files"], human_size(preview["freed"]), preview["records"]),
                parent=self.root):
            return
        res = core.cleanup_data(self.cfg, days=days)
        msg = "已删除 %d 个文件，释放 %s，清理 %d 条失效记录" % (
            res["files"], human_size(res["freed"]), res["records"])
        if res["skipped"]:
            msg += "（%d 个文件被占用没删掉）" % res["skipped"]
        log.info("手动清理：%s", msg)
        self.status_override = (msg, time.time() + 10)
        messagebox.showinfo("清理完成", msg, parent=self.root)

    def default_save_dir(self):
        """设置里的「默认保存位置」（所有账号通用），没设置时返回空串"""
        path = str(self.cfg.get("default_save_dir") or "").strip()
        return os.path.abspath(os.path.expanduser(path)) if path else ""

    def get_dy(self):
        with self.dy_lock:
            if self.dy is None:
                self.dy = core.DouYin("",
                              timeout=int(self.cfg["request_timeout"]),
                              data_dir=str(self.cfg.get("data_dir") or ""))
            return self.dy

    def on_autostart_change(self):
        self.set_autostart(bool(self.autostart_var.get()))

    def on_download_kind_change(self):
        video = bool(self.dlvideo_var.get())
        images = bool(self.dlimg_var.get())
        self.cfg["download_video"] = video
        self.cfg["download_images"] = images
        try:
            self.save_config()
        except Exception as exc:  # noqa: BLE001
            log.warning("保存配置失败：%s", exc)
        log.info("下载开关：视频 %s / 图集 %s（下一轮检查生效）",
                 "开" if video else "关", "开" if images else "关")
        if not video and not images:
            log.warning("视频和图集都关掉了：新作品只会登记，不会下载")

    def on_interval_commit(self, event=None):
        self.validate_interval(show_dialog=True)

    def on_auto_full_change(self):
        want = bool(self.auto_full_var.get())
        self.cfg["auto_start_full"] = want
        self.save_config()
        log.info("启动后自动监控先全量核账：%s", "开启（启动后多花约一分钟）" if want else "关闭（按全量间隔走）")

    def on_scope_change(self, event=None):
        val = SCOPE_VALUES.get(str(self.scope_var.get()), "both")
        self.cfg["monitor_scope"] = val
        self.save_config()
        log.info("监控内容：%s", SCOPE_LABELS.get(val, val))

    def on_full_audit_commit(self, event=None):
        val = self._clamp_int(self.full_audit_var, self.full_audit_spin,
                              int(self.cfg.get("full_audit_interval", 1440) or 1440), 0, 10080)
        self.full_audit_var.set(val)
        self.cfg["full_audit_interval"] = val
        self.save_config()
        log.info("全量核账间隔：%s", "关闭（只在手动核账时全量）" if val == 0 else "%d 分钟" % val)

    def set_row_height(self):
        """设置 → 界面与窗口 → 账号列表行高：对话框输入 20~80，立即生效。"""
        cur = int(self.cfg.get("row_height", 30) or 30)
        val = simpledialog.askinteger(
            "账号列表行高", "每行高度（20~80 像素）：",
            initialvalue=cur, minvalue=20, maxvalue=80, parent=self.root)
        if val is None:
            return
        val = min(80, max(20, int(val)))
        self.cfg["row_height"] = val
        self.save_config()
        try:
            style = ttk.Style(self.root)
            style.configure("Treeview", rowheight=hi_dpi.scale_px(val))
        except Exception:  # noqa: BLE001
            pass
        log.info("账号列表行高：%d 像素", val)
        self.set_status("账号列表行高已设为 %d 像素" % val, 5)

    def on_live_separate_change(self):
        """设置菜单：直播独立轮询（与作品检测错开）"""
        self.cfg["live_separate"] = bool(self.live_separate_var.get())
        self.save_config()
        log.info("直播独立轮询：%s", "开启" if self.live_separate_var.get() else "关闭")

    def on_live_record_change(self):
        want = bool(self.rec_var.get())
        self.cfg["live_record"] = want
        self.save_config()
        log.info("直播自动录制已%s", "开启" if want else "关闭")
        if not want:
            self.stop_all_records()

    def on_live_start(self, payload):
        """后台检查到开播 → 开始录制（是否该录由 sync_live 按账号三态决定）"""
        if not self.live_mgr:
            return
        key = payload.get("key") or ""
        if not key or (self.live_mgr.get(key) and self.live_mgr.get(key).running):
            return
        url = payload.get("flv") or payload.get("hls") or ""
        if not url:
            log.warning("【直播录制】%s 没有可用的拉流地址，跳过录制", payload.get("nickname") or key)
            return
        try:
            self.live_mgr.start(
                key, nickname=payload.get("nickname") or "",
                platform=payload.get("platform") or "douyin", url=url,
                quality=payload.get("quality") or "",
                quality_label=payload.get("quality_label") or "原画",
                title=payload.get("title") or "")
        except Exception as exc:  # noqa: BLE001
            log.error("【直播录制】启动失败：%s", exc)
        self.refresh_live_panel()

    def on_live_stop(self, payload):
        """直播结束 → 收尾并记录时长/大小"""
        key = (payload or {}).get("key") or ""
        if not key or not self.live_mgr:
            return
        rec = self.live_mgr.get(key)
        if rec:
            self.live_mgr.stop(key, reason=(payload or {}).get("reason") or "直播已结束")
            self.refresh_live_panel()

    def live_record_allowed(self, acc):
        """这个账号此刻该不该录：账号三态（跟全局 / 录 / 不录）+ 全局开关"""
        mode = acc.get("live_record")          # None=跟全局 / True=录 / False=不录
        if mode is None:
            return bool(self.cfg.get("live_record", True))
        return bool(mode)

    def sync_live(self, acc, key, nickname, live, was_live):
        """把一次直播检测结果同步到录制器。

        - 开播：账号设置允许才录（账号「录」可覆盖全局关；账号「不录」一律不录）；
        - 同一场已经在录就不重复起；下播/被设为不录就收尾；
        - 事件都走队列回主线程，录制器和面板只在主线程动。
        """
        nick = nickname or (live or {}).get("nickname") or acc.get("target") or ""
        rec = self.live_mgr.get(key) if (self.live_mgr and key) else None
        if key:
            # 面板要能在「没开自动录制」时也显示谁正在直播（行内可手动开录）
            seen = {"key": key, "nickname": nick, "live": bool(live)}
            if live:
                seen.update({
                    "platform": live.get("platform") or core.platform_of(acc),
                    "title": live.get("title") or "",
                    "quality": live.get("quality") or "",
                    "quality_label": live.get("quality_label") or "",
                    "flv": live.get("flv") or "",
                    "hls": live.get("hls") or "",
                    "room": live.get("room") or "",
                })
            self.q.put(("live_seen", seen))
        if not live:
            if rec is not None and rec.running:
                self.q.put(("live_stop", {"key": key, "reason": "直播已结束"}))
            return
        if not self.live_record_allowed(acc):
            if rec is not None and rec.running:
                self.q.put(("live_stop", {"key": key, "reason": "账号设置为不录制"}))
                log.info("【直播录制】%s 这次不录（账号设置为「不录」或全局已关），已停止录制", nick)
            return
        if rec is not None and rec.running:
            return                      # 同一场还在录，沿用
        url = live.get("flv") or live.get("hls") or ""
        if not url:
            log.warning("【直播录制】%s 没有可用的拉流地址，跳过录制", nick)
            return
        if was_live:
            # 上一场还没收尾（断流/换房间），先结束再起新的
            self.q.put(("live_stop", {"key": key, "reason": "换了一场直播"}))
        self.q.put(("live_start", {
            "key": key,
            "nickname": nick,
            "platform": live.get("platform") or core.platform_of(acc),
            "title": live.get("title") or "",
            "quality": live.get("quality") or "",
            "quality_label": live.get("quality_label") or "",
            "flv": live.get("flv") or "",
            "hls": live.get("hls") or "",
        }))

    def on_platform_change(self):
        platform = core.platform_of(self.platform_var.get())
        label = core.platform_label(platform)
        self.cfg["default_platform"] = platform
        self.cfg["platform_chosen"] = True
        self.save_config()
        if self.platform_box is not None:
            self.platform_box.set(label)
        log.info("默认平台已切换为：%s", label)
        self.status_override = ("默认平台：%s" % label, time.time() + 8)
        if not self.cookie_ready(platform):
            log.info("还没有登录 %s 的 Cookie，可点「设置 → 扫码登录（浏览器）」登录", label)

    def on_tray_change(self):
        want = bool(self.tray_var.get())
        self.cfg["tray_enabled"] = want
        self.save_config()
        if want:
            if not self.tray:
                self.setup_tray()
        else:
            if self.tray:
                self.tray.destroy()
                self.tray = None
                core.set_notify_hook(None)
        log.info("托盘图标已%s", "开启" if want else "关闭")

    def open_record_dir(self):
        self.open_path(self.record_dir(), create=True)

    def refresh_selected(self):
        """工具栏「刷新所选」/ 菜单：选中了就刷选中的，没选就刷第一个启用账号"""
        sel = [a for a in self.selected_accounts() if not a.get("refresh_busy")]
        if not sel:
            sel = [a for a in self.accounts
                   if a.get("enabled", True) and not a.get("refresh_busy")][:1]
        if not sel:
            messagebox.showinfo("提示", "请先在列表里选中要刷新的账号", parent=self.root)
            return
        for acc in sel[:5]:
            self.refresh_account(acc)

    def set_auto_clean(self):
        days = simpledialog.askinteger(
            "自动清理", "自动删除多少天以前的旧作品？（0 = 关闭自动清理）",
            initialvalue=int(self.cfg.get("auto_clean_days") or 0),
            minvalue=0, maxvalue=3650, parent=self.root)
        if days is None:
            return
        self.cfg["auto_clean_days"] = int(days)
        self.save_config()
        log.info("自动清理已%s", ("设置为 %d 天" % days) if days else "关闭")
        if days:
            self.run_auto_clean(force=True)

    def set_account_scope(self, value, accs=None):
        """账号级监控内容：both/posts/live；None=跟全局"""
        targets = list(accs or self.selected_accounts())
        if not targets:
            return
        val = value if value in ("both", "posts", "live") else None
        for acc in targets:
            if val is None:
                acc.pop("scope", None)
            else:
                acc["scope"] = val
        self.save_config()
        for acc in targets:
            self.update_row(acc)
        names = "、".join(a.get("nickname") or a.get("target") or "?" for a in targets[:5])
        log.info("监控内容（%s）：%s", names,
                 "跟全局（%s）" % SCOPE_LABELS.get(
                     str(self.cfg.get("monitor_scope") or "both"), "作品和直播")
                 if val is None else SCOPE_LABELS.get(val, val))

    def set_live_record_selected(self, value):
        sel = self.selected_accounts()
        if not sel:
            messagebox.showinfo("提示", "请先在列表里选中账号")
            return
        stopped = 0
        for acc in sel:
            acc["live_record"] = value
            self.update_row(acc)
            if not self.live_record_allowed(acc) and self.live_mgr:
                key = str(acc.get("sec_user_id") or acc.get("target") or "")
                rec = self.live_mgr.get(key)
                if rec is not None and rec.running:
                    self.live_mgr.stop(key, reason="已关闭该账号的直播录制")
                    stopped += 1
        if stopped:
            self.refresh_live_panel()
        self.save_config()
        text = {None: "跟随全局", True: "录制", False: "不录"}[value]
        log.info("%d 个账号的直播录制已设为：%s", len(sel), text)

    def stop_all_records(self):
        if not self.live_mgr or not self.live_mgr.items:
            return
        self.live_mgr.stop_all(reason="手动停止")
        self.refresh_live_panel()

    def audit_account(self, acc, trigger="每轮检查", dry_run=False, quiet=False):
        """核账入口：同一时刻只允许一个账号在核账。

        增量轮询、开始监控、手动刷新、菜单核账都可能同时想核账；两个核账一起跑会把
        作品列表接口打到限流，还会两边同时补下载、互相覆盖 下载记录.json。
        后到的一个直接跳过，等下一轮（或下一只手动的）再核。
        """
        with self._audit_gate:
            if self._audit_busy:
                log.debug("【%s】已有核账在跑，这次跳过（%s）",
                          acc.get("nickname") or acc.get("target"), trigger)
                return {"skipped": "已有核账在跑", "posts": 0, "missing": 0,
                        "items": 0, "files": 0, "already": 0}
            self._audit_busy = True
        try:
            return self._audit_account_impl(acc, trigger=trigger, dry_run=dry_run, quiet=quiet)
        finally:
            with self._audit_gate:
                self._audit_busy = False

    def _audit_account_impl(self, acc, trigger="每轮检查", dry_run=False, quiet=False):
        """核账**一个**账号：主页全部作品 vs 下载记录，缺的补下来。

        为什么单独抽出来：开始监控、每轮检查、左侧列表手动刷新都要做同一件事 ——
        「发布时隐藏/审核中、后来才公开」的作品发布时间是旧的，增量翻页永远刷不到，
        只有全量比对才发现得了。

        quiet=True：没缺口时不写日志、不动状态栏（每轮都跑，不能每轮刷 6 行）；
        有缺口或补到了文件照样写 INFO。返回 {"posts","missing","items","files","skipped"}。
        """
        out = {"posts": 0, "missing": 0, "items": 0, "files": 0, "already": 0,
               "skipped": ""}
        nick = acc.get("nickname") or acc.get("target") or "?"
        platform = core.platform_of(acc)
        if not self.cookie_ready(platform):
            out["skipped"] = "没有 %s 的 Cookie" % core.platform_label(platform)
            if not quiet:
                log.info("【%s】%s，跳过核账", nick, out["skipped"])
            return out
        try:
            dy = self.get_client(platform)
            try:
                dy.reload_cookie()
            except Exception as exc:  # noqa: BLE001
                log.debug("核账前重读 Cookie 失败：%s", exc)
            sec = acc.get("sec_user_id") or ""
            if not sec and platform != "tiktok":
                sec = dy.resolve_sec_uid(acc.get("target") or "")
                acc["sec_user_id"] = sec
            if not sec:
                out["skipped"] = "还没拿到 sec_user_id"
                if not quiet:
                    log.info("【%s】%s，跳过核账（下一轮监控会补上）", nick, out["skipped"])
                return out
            cfg = dict(self.cfg)
            cfg.update({"platform": platform, "sec_user_id": sec,
                        "unique_id": acc.get("unique_id") or "",
                        "save_dir": self.account_save_dir(acc),
                        "uid": acc.get("uid") or ""})
            hist_dest = ""
            if not str(acc.get("save_dir") or "").strip():
                hist_dest = core.history_account_dir(cfg, acc)
            if not quiet:
                self.q.put(("status", "核账中：%s" % nick))
            declared = 0
            try:
                prof = dy.fetch_profile(sec) or {}
                declared = int(prof.get("aweme_count") or 0)
                if not acc.get("uid"):
                    acc["uid"] = str(prof.get("uid") or "")
                    cfg["uid"] = acc["uid"]
            except Exception as exc:  # noqa: BLE001
                log.debug("核账取主页信息失败：%s", exc)
            acct_state = self.state.setdefault("accounts", {}).setdefault(sec, {})
            full_audit = any(w in str(trigger) for w in ("手动", "菜单", "全部", "刷新", "全量"))
            # 增量只覆盖水位之后的作品；「发布时审核中/隐藏、过几天才公开」的作品发布时间是旧的，
            # 会落在水位以下。所以每天至少全量核一次，启动后如果隔了超过一天也先全量。
            now_ts = int(time.time())
            last_full = int(acct_state.get("full_audit_ts") or 0)
            try:
                _full_min = int(self.cfg.get("full_audit_interval", 1440))
            except Exception:
                _full_min = 1440
            _full_gap = _full_min * 60
            # 只有用户开启「自动全量核账」时，定时到点才触发全量；
            # 手动/菜单/刷新/启动全量（full_audit）不受此开关影响。
            auto_full_on = bool(self.cfg.get("full_audit_enabled", True))
            due_full = bool(auto_full_on and _full_gap > 0
                            and ((not last_full) or (now_ts - last_full >= _full_gap)))
            force_full = bool(full_audit or due_full)
            if force_full and not full_audit:
                # 到点就做真全量：不能用「主页作品数没变」当理由跳过，
                # 否则用户设的「全量间隔」到期后永远是假全量，隐藏后放出的作品会漏。
                log.info("【%s】距上次全量已到设定间隔（%d 分钟），本轮做全量核对（防止隐藏后放出的作品漏掉）",
                         nick, _full_min)
            since = 0 if force_full else int(acct_state.get("audit_top_ts") or 0)

            def _mark_audit_top():
                """本轮没缺口才把「已核到最新时间」往前推；有缺口保持旧值，下轮重核。"""
                if dry_run:
                    return
                try:
                    top = int(cfg.get("_audit_newest_ts") or 0)
                    acct_state["declared"] = int(declared or 0)
                    if top:
                        acct_state["audit_top_ts"] = top
                        if force_full:
                            acct_state["full_audit_ts"] = int(time.time())
                        log.debug("【%s】增量核账进度记为 %s", nick, top)
                        if hasattr(self, "save_state"):
                            self.save_state()
                except Exception as exc:  # noqa: BLE001
                    log.debug("记录核账进度失败：%s", exc)
            with self.check_lock:
                missing, total_posts, missing_total = core.find_missing_all(
                    cfg, dy, limit=self.audit_limit(), since_ts=since)
            out["posts"] = int(total_posts or 0)
            out["missing"] = int(missing_total or 0)
            out["full"] = bool(force_full)
            # 首次检查时用户选了「不下载已有作品」的作品（seen 里 skip=True）不算缺，
            # 否则核账会把这些作品整批重新下到下载目录，和账号历史文件夹形成两份。
            seen = (self.state.get("accounts", {}).get(sec) or {}).get("seen") or {}
            if missing and seen:
                kept = []
                for it in missing:
                    mark = seen.get(str(it.get("aweme_id"))) or {}
                    if mark.get("skip"):
                        continue
                    kept.append(it)
                if len(kept) != len(missing):
                    log.info("【%s】%d 个作品当初选了不下载，核账跳过它们", nick,
                             len(missing) - len(kept))
                missing = kept
            incremental = bool(since)
            extra = "" if incremental else self.audit_extra_text(declared, total_posts)
            if not total_posts:
                if not quiet:
                    log.info("【%s】没翻到公开作品%s", nick,
                             ("（接口计数 %d，可能都是私密或审核中）" % declared)
                             if declared else "")
                return out
            if not missing:
                _mark_audit_top()
                if not quiet:
                    if incremental:
                        log.info("【%s】未检测到发布新作品", nick)
                    else:
                        log.info("【%s】公开作品 %d 个，没有缺失%s", nick, total_posts, extra)
                return out
            over = "" if missing_total <= len(missing) else "，本轮先补 %d 个" % len(missing)
            tail = ("只报告，不下载" if dry_run
                    else "开始补下载；文件已在本地会自动跳过并补记录")
            log.info("【%s】公开作品 %d 个，缺 %d 个%s（%s）", nick, total_posts,
                     missing_total, over,
                     "；".join([x for x in (extra.strip("（）"), tail) if x]))
            if dry_run:
                for it in missing[:10]:
                    log.info("   缺：%s  %s  %s", it.get("aweme_id"),
                             time.strftime("%Y-%m-%d %H:%M",
                                           time.localtime(int(it.get("create_time") or 0))),
                             core.brief_text(it, 30))
                if len(missing) > 10:
                    log.info("   …还有 %d 个", len(missing) - 10)
                return out
            dirs = []
            if hist_dest:
                dirs.append(hist_dest)
            custom_dir = self.account_save_dir(acc)
            if custom_dir:
                dirs.append(custom_dir)
            dirs.append(os.path.join(core.douk_volume_dir(cfg), 'Download'))
            additions = {}
            remote = []
            local_n = 0
            for it in missing:
                cn = core.post_kind(it)
                kind = {'视频': 'video', '图集': 'image', '实况': 'live'}.get(cn, 'video')
                # 图集必须「所有图都在」才算下过；只看前缀会把下了一半的图集当完整，永远补不齐
                try:
                    urls = core.douk_dl.pick_media(it)[1]
                except Exception:  # noqa: BLE001
                    urls = []
                hit = (core.douk_dl.existing_files(it, kind, urls, dirs) if urls
                       else core.douk_dl.match_name_files(it, kind, dirs))
                aid = str(it.get('aweme_id') or '')
                if hit and aid:
                    try:
                        shown = os.path.relpath(hit[0], core.data_root(cfg))
                    except ValueError:
                        shown = hit[0]
                    additions['%s:%s' % (platform, aid)] = {
                        'time': time.strftime('%Y-%m-%d %H:%M:%S'),
                        'file': shown, 'title': core.brief_text(it, 60)}
                    local_n += 1
                else:
                    remote.append(it)
            if local_n:
                # 和下载线程共用 check_lock，避免两边各拿一份旧记录写回去丢条目
                with self.check_lock:
                    recs = core.douk_dl.load_records(core.data_root(cfg)) or {}
                    recs.update(additions)
                    core.douk_dl.save_records(core.data_root(cfg), recs)
                out['already'] = int(out.get('already') or 0) + local_n
                log.info('【%s】核账：%d 个作品的文件已在账号文件夹里，只补下载记录，没有重复下载',
                         nick, local_n)
            missing = remote
            if not missing:
                _mark_audit_top()
                return out

            for i in range(0, len(missing), 30):
                chunk = missing[i:i + 30]
                report = {}
                try:
                    with self.check_lock:
                        stats, files, text = core.download_known_items(
                            cfg, chunk, platform=platform, dy=dy, dest=hist_dest, alt_dirs=dirs)
                        core._finish_download(cfg, files, stats, text, chunk, report)
                except Exception as exc:  # noqa: BLE001
                    log.error("【%s】核账补下载失败：%s", nick,
                              core.classify_error(exc, platform))
                    continue
                files = report.get("files") or []
                out["files"] += len(files)
                out["items"] += len(chunk)
                self.record_session(acc, nick, report)
                if report.get("items") or files:
                    self.q.put(("downloaded", {
                        "nickname": nick,
                        "target": acc.get("target"),
                        "items": report.get("items") or [],
                        "files": files,
                    }))
                log.info("【%s】核账补下载：%d/%d 个作品，新增 %d 个文件",
                         nick, min(i + 30, len(missing)), len(missing), len(files))
            # 增量核账的进度只在本轮没有缺口时前移；有缺口就保持旧时间点，下一轮继续从头核对
            if not missing:
                _mark_audit_top()
            return out
        except core.RateLimited as exc:
            # 接口限流是全局状态，不是这个账号的故障；每个账号都刷 ERROR 会淹没日志
            out["skipped"] = "作品列表接口限流中"
            if not quiet:
                log.warning("【%s】历史核账暂停：%s（自动降频，冷却后恢复）", nick, exc)
            return out
        except Exception as exc:  # noqa: BLE001
            msg = core.classify_error(exc, platform)
            if quiet:
                log.debug("【%s】每轮核账没跑成：%s", nick, msg)
            else:
                log.error("【%s】历史核账失败：%s", nick, msg)
                log.debug("%s", traceback.format_exc())
            return out
    def refresh_account_count(self):
        """账号表下方副标题：数量统计 + 右键提示"""
        total = len(self.accounts)
        on = sum(1 for a in self.accounts if a.get("enabled", True))
        live = sum(1 for a in self.accounts if a.get("live"))
        text = "共 %d 个 · 启用 %d 个" % (total, on)
        if live:
            text += " · 直播中 %d" % live
        text += " · 右键行可操作"
        self.count_var.set(text)

    def clear_log_view(self):
        """清空界面上的日志显示（不动 monitor.log 文件）"""
        self._log_key, self._log_n = None, 0
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def copy_account_text(self, acc):
        nick = acc.get("nickname") or ""
        target = acc.get("target") or ""
        uid = acc.get("unique_id") or ""
        text = "\t".join(x for x in (nick, target, uid) if x)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set("已复制：%s" % text)
        log.info("已复制账号信息：%s", text)

    def copy_profile_url(self, acc):
        url = self.profile_url(acc)
        if not url:
            self.status_var.set("这个账号还没有解析出主页链接")
            log.warning("【%s】还没有主页链接，先「重新解析」", acc.get("nickname") or acc.get("target"))
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.status_var.set("已复制主页链接：%s" % url)
        log.info("已复制主页链接：%s", url)

    def log_diagnosis(self, nick, info):
        """把诊断结果写成几行日志：两个数、差额性质、时间范围、分布"""
        declared = int(info.get("declared") or 0)
        fetched = int(info.get("fetched") or 0)
        raw = int(info.get("raw") or 0)
        diff = declared - fetched
        log.info("【%s】作品计数诊断：%s", nick, self.audit_count_text(declared, fetched))
        old_ts, old_id = info.get("oldest") or (0, "")
        new_ts, new_id = info.get("newest") or (0, "")
        fmt = lambda ts: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "—"
        log.info("  公开列表：翻 %d 页，原始 %d 条，去重 %d 条，末页还有更多=%s，置顶 %d 条",
                 int(info.get("pages") or 0), raw, fetched,
                 "是" if info.get("has_more") else "否（已翻到底）", int(info.get("tops") or 0))
        if fetched:
            log.info("  时间范围：%s ~ %s（最新 %s、最旧 %s）", fmt(old_ts), fmt(new_ts),
                     new_id, old_id)
        years = info.get("by_year") or {}
        if years:
            log.info("  按年份：%s", "，".join("%s 年 %d 条" % (y, years[y])
                                              for y in sorted(years, reverse=True)))
        kinds = info.get("by_kind") or {}
        if kinds:
            extra = "（其中实况 %d 条）" % int(info.get("live") or 0) if info.get("live") else ""
            log.info("  按类型：%s%s", "，".join("%s %d 条" % (k, kinds[k]) for k in kinds), extra)
        if diff > 0 and fetched:
            if raw == fetched and not info.get("has_more"):
                log.info("  结论：接口已翻到底且没有重复页，差的 %d 条是**不对外公开**的作品"
                         "（作者设了私密、仅好友可见，或仍在审核中）→ 任何公开接口都拿不到，"
                         "等它公开后下一轮核账会自动补上", diff)
            else:
                log.info("  结论：原始 %d 条 / 去重 %d 条 / 末页还有更多=%s，"
                         "说明分页或接口有异常，把这几行发给开发者", raw, fetched,
                         "是" if info.get("has_more") else "否")
        elif declared == fetched:
            log.info("  结论：资料页计数与公开列表一致，没有缺口")


    def on_tree_context(self, event):
        """右键账号行弹出操作菜单：打开/复制、刷新/核账、下载、直播录制、启停、删除。

        右键在选区内 → 保留多选（操作对全部选中行生效）；右键在选区外 → 先选中这一行。
        """
        row = self.tree.identify_row(event.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            self.tree.focus(row)
        sel = self.selected_accounts()
        if not sel:
            return "break"
        multi = len(sel) > 1
        first = sel[0]
        nick = first.get("nickname") or first.get("target") or "?"
        menu = tk.Menu(self.root, tearoff=0)
        if multi:
            menu.add_command(label="已选 %d 个账号（操作对它们一起生效）" % len(sel),
                             state="disabled")
            menu.add_separator()
        # --- 打开 / 复制 ---
        menu.add_command(label="打开主页", state="disabled" if multi else "normal",
                         command=lambda: self.open_profile(first))
        menu.add_command(label="复制主页链接", command=lambda: self.copy_profile_url(first))
        menu.add_command(label="复制昵称与账号", command=lambda: self.copy_account_text(first))
        menu.add_separator()
        # --- 刷新 / 核账 ---
        menu.add_command(label=("刷新这 %d 个账号（直播 + 新作品）" % len(sel)) if multi
                         else "刷新（直播 + 新作品）",
                         command=lambda: [self.refresh_account(a) for a in sel[:5]])
        menu.add_command(label="核账历史作品（只报告缺什么，不下载）",
                         command=lambda: self.start_audit(sel, trigger="右键核账", dry_run=True))
        menu.add_separator()
        # --- 下载 ---
        menu.add_command(label="下载历史作品…", command=self.download_history)
        menu.add_command(label="补下载最近 3 天的漏项…", command=self.backfill_missing)
        menu.add_command(label="设置保存位置…", command=self.set_save_dir)
        menu.add_command(label="打开作品目录",
                         command=lambda: [self.open_account_save_dir(a) for a in sel])
        menu.add_separator()
        # --- 监控内容（账号级，当前值打勾）---
        cur_scope = first.get("scope") if not multi else "__multi__"
        if cur_scope not in ("both", "posts", "live"):
            cur_scope = None
        menu.add_command(label="监控这个账号的内容：", state="disabled")
        for val, text in self.SCOPE_CHOICES:
            mark = "✓ " if (not multi and cur_scope == val) else "    "
            menu.add_command(label=mark + text,
                             command=lambda v=val: self.set_account_scope(v, sel))
        menu.add_separator()
        # --- 启停 / 删除 ---
        running = all(a.get("enabled", True) for a in sel)
        menu.add_command(label="暂停监控" if running else "启用监控",
                         command=self.toggle_enabled)
        menu.add_command(label="删除账号…", command=self.remove_accounts)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def show_from_tray(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:  # noqa: BLE001
            pass

    def on_refresh_done(self, payload):
        """手动刷新结束：复位按钮/状态，并把这一行重画出来。

        以前 worker 里虽然把 refresh_busy 置回 False，但界面没有对应的事件分支，
        行数据没人刷新，界面会一直停在「刷新中…」。"""
        acc = payload.get("acc") or {}
        if not isinstance(acc, dict):
            return
        acc["refresh_busy"] = False
        nick = payload.get("nickname") or acc.get("nickname") or acc.get("target") or ""
        err = str(payload.get("error") or "")
        if err:
            if not str(acc.get("state") or "").startswith("出错"):
                acc["state"] = "出错: %s" % err[:18]
            self.status_override = ("【%s】刷新失败：%s" % (nick, err), time.time() + 8)
        else:
            if str(acc.get("state") or "") in ("刷新中", ""):
                acc["state"] = "监控中"
            self.status_override = ("【%s】刷新完成" % nick, time.time() + 4)
        try:
            self.update_row(acc)
        except Exception:  # noqa: BLE001
            pass
        if getattr(self, "log_summary_only", False) and not err:
            return
        log.info("【%s】手动刷新完成%s", nick, ("：" + err) if err else "")

    def on_downloaded(self, payload):
        """本轮确实下到了文件：在「本次下载」里追加行"""
        payload = payload or {}
        # 之前下载过、这次直接跳过的作品不进行；只列真正落到磁盘的作品
        items = [it for it in (payload.get("items") or []) if it.get("files")]
        files = payload.get("files") or []
        if not items and not files:
            return
        nickname = payload.get("nickname") or payload.get("target") or "账号"
        if self.dl_tree is None:
            # 三栏未建出时兜底建隐藏表格，保证统计照常累计
            self.dl_tree = ttk.Treeview(self.root, columns=("when", "nick", "kind", "desc"),
                                        show="")
        if self.dl_sum_var is None:
            self.dl_sum_var = tk.StringVar(value="")
        size = 0
        for f in files:
            try:
                size += os.path.getsize(f)
            except Exception:  # noqa: BLE001
                continue
        self.dl_total_items += len(items)
        self.dl_total_files += len(files)
        self.dl_total_size += size
        when = time.strftime("%H:%M:%S")
        for it in items[:20]:
            kind = it.get("kind") or "作品"
            desc = (it.get("desc") or "").replace("\n", " ")[:44] or "（无标题）"
            iid = self.dl_tree.insert("", 0, values=(when, nickname, kind, desc),
                                      tags=("new",))
            # 每行都登记映射：点行就能打开对应的文件（open_download_file 用）；
            # 被跳过的作品（没有文件）也留一行，点它时给解释，不静默吞掉。
            self.dl_meta[iid] = {"aweme_id": str(it.get("aweme_id") or ""),
                                 "kind": kind, "desc": desc,
                                 "nickname": nickname, "when": when}
            self.dl_files[iid] = [f for f in (it.get("files") or []) if f]
        kids = self.dl_tree.get_children()
        while len(kids) > 60:
            old = kids[-1]
            self.dl_tree.delete(old)
            self.dl_meta.pop(old, None)
            self.dl_files.pop(old, None)
            kids = self.dl_tree.get_children()
        for it in items:
            k = it.get("kind") or "作品"
            self.dl_kinds[k] = self.dl_kinds.get(k, 0) + 1
        parts = " · ".join("%s %d 个" % (k, v) for k, v in self.dl_kinds.items())
        self.dl_sum_var.set("本次已下载 %d 个作品%s · %d 个文件 · %s" % (
            self.dl_total_items,
            ("（%s）" % parts) if parts else "",
            self.dl_total_files, self.size_text(self.dl_total_size)))
        if not self.dl_visible and not self.dl_hidden:
            self.dl_visible = True
            self.layout_right_panels()

    def on_full_audit_enabled_change(self):
        want = bool(self.full_audit_enabled_var.get())
        self.cfg["full_audit_enabled"] = want
        self.save_config()
        log.info("自动全量核账：%s", "开启（按间隔定期全量扫描）" if want
                 else "关闭（自动轮次只做增量，手动核账仍可用）")

    def on_auditstart_change(self):
        want = bool(self.audit_start_var.get())
        self.cfg["audit_on_start"] = want
        try:
            self.save_config()
        except Exception as exc:  # noqa: BLE001
            log.warning("保存配置失败：%s", exc)
        log.info("开始监控时核账历史作品：%s", "开启" if want else "关闭")

    def diagnose_count(self):
        """菜单：诊断作品计数（只读，不下载）"""
        targets = self.selected_accounts()
        if not targets:
            targets = [a for a in self.accounts if a.get("enabled", True)][:1]
        if not targets:
            messagebox.showinfo("提示", "先在列表里选中要诊断的账号", parent=self.root)
            return
        for acc in targets[:3]:
            threading.Thread(target=self._diagnose_worker, args=(acc,),
                             daemon=True).start()
        self.status_override = ("正在诊断作品计数…", time.time() + 8)

    def _diagnose_worker(self, acc):
        nick = acc.get("nickname") or acc.get("target") or "?"
        platform = core.platform_of(acc)
        if not self.cookie_ready(platform):
            log.warning("【%s】没有 %s 的 Cookie，无法诊断", nick,
                        core.platform_label(platform))
            return
        try:
            dy = self.get_client(platform)
            try:
                dy.reload_cookie()
            except Exception as exc:  # noqa: BLE001
                log.debug("诊断前重读 Cookie 失败：%s", exc)
            sec = acc.get("sec_user_id") or ""
            if not sec and platform != "tiktok":
                sec = dy.resolve_sec_uid(acc.get("target") or "")
                acc["sec_user_id"] = sec
            if not sec:
                log.warning("【%s】还没拿到 sec_user_id，先「重新解析」再诊断", nick)
                return
            cfg = dict(self.cfg)
            cfg.update({"platform": platform, "sec_user_id": sec,
                        "unique_id": acc.get("unique_id") or "",
                        "save_dir": self.account_save_dir(acc),
                        "uid": acc.get("uid") or ""})
            with self.check_lock:
                info = core.diagnose_posts(cfg, dy, max_pages=400)
            self.log_diagnosis(nick, info or {})
            self.status_override = ("诊断完成：%s" % nick, time.time() + 8)
        except Exception as exc:  # noqa: BLE001
            log.error("【%s】诊断作品计数失败：%s", nick,
                      core.classify_error(exc, platform))

    def on_auditevery_change(self):
        want = bool(self.auditevery_var.get())
        self.cfg["audit_every_round"] = want
        try:
            self.save_config()
        except Exception as exc:  # noqa: BLE001
            log.warning("保存配置失败：%s", exc)
        if want:
            log.info("每轮检查都核账历史作品：开启（每轮 + 手动刷新都会把主页所有作品和下载记录比一遍，"
                     "单轮耗时会长一点）")
        else:
            log.info("每轮检查都核账历史作品：关闭（只按增量翻页，隐藏作品后来放出来可能漏抓）")

    def start_audit(self, targets=None, trigger="手动核账", dry_run=False):
        """全量核账：账号主页上的**所有**作品和下载记录比一遍。

        为什么需要：增量翻页用「上轮时间」和「整页无新」判停，而「发布时隐藏/审核中、
        过几天才公开」的作品发布时间是**旧的** —— 它出现在列表里也不会被当成新作品。
        核账不看 seen、只看下载记录，所以能把这类补回来。
        """
        if self.audit_running:
            log.info("历史核账还在跑，等它跑完再试")
            return False
        left = core.rate_limit_remaining()
        if left > 0:
            log.info("抖音作品列表接口限流中（约剩 %d 分钟），这次历史核账先跳过；"
                     "冷却结束会自动恢复，无需操作", int(left / 60) + 1)
            return False
        pool = [a for a in (targets if targets is not None else self.accounts)
                if a.get("enabled", True)]
        if not pool:
            log.info("历史核账：没有启用的账号")
            return False
        self.audit_running = True
        threading.Thread(target=self._audit_worker, args=(list(pool), trigger, bool(dry_run)),
                         daemon=True).start()
        return True

    def auto_start_monitor(self):
        """启动后自动开始监控（`auto_monitor`，默认开）。

        跳过的情况：设置里关掉了、已经在跑、没有启用的账号、还没选平台或没登录 Cookie
        （这几种强行开跑只会刷一屏「Cookie 失效」，不如等用户处理完再点）。
        """
        if not bool(self.cfg.get("auto_monitor", True)):
            return
        if self.job_running or (self.monitor_thread and self.monitor_thread.is_alive()):
            return
        if not self.cfg.get("platform_chosen", False):
            log.info("启动后自动监控：还没选平台，先跳过（设置 → 默认平台）")
            return
        enabled = [a for a in self.accounts if a.get("enabled", True)]
        if not enabled:
            log.info("启动后自动监控：还没有启用的账号，先跳过（添加账号后点「开始监控」）")
            return
        plat = self.default_platform()
        try:
            ready = self.cookie_ready(plat)
        except Exception as exc:  # noqa: BLE001
            log.warning("启动后自动监控：检查 Cookie 失败（%s），先跳过", exc)
            return
        if not ready:
            log.info("启动后自动监控：还没有登录 %s 的 Cookie，先跳过（登录后点「开始监控」）",
                     core.platform_label(plat))
            return
        log.info("启动后自动开始监控（设置 → 启动软件后自动开始监控）")
        self.toggle_monitor("启动自动")
        try:
            if getattr(self, "tray", None):
                self.tray.notify("抖音监控", "已自动开始监控（设置 → 启动软件后自动开始监控）")
        except Exception:  # noqa: BLE001
            pass

    def close_action(self):
        """关闭窗口的行为：minimize=隐藏到托盘（默认），exit=退出程序"""
        val = str(self.cfg.get("close_action") or "minimize").strip().lower()
        return "exit" if val in ("exit", "quit", "close") else "minimize"

    def minimize_to_background(self):
        """关闭按钮 = 隐藏到托盘：窗口消失，监控线程、定时器、录制全部照旧"""
        try:
            self.cfg["window_geometry"] = self.root.geometry()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.save_config()
        except Exception:  # noqa: BLE001
            pass
        has_tray = bool(self.tray is not None)
        if not has_tray and self.cfg.get("tray_enabled", True):
            # 托盘还没起来（例如启动早期）：先试一次，成功就真隐藏到托盘
            try:
                has_tray = bool(self.setup_tray())
            except Exception:  # noqa: BLE001
                has_tray = False
        try:
            if has_tray:
                self.root.withdraw()
            else:
                self.root.iconify()
        except Exception as exc:  # noqa: BLE001
            log.warning("隐藏窗口失败：%s", exc)
            return
        note = ("已隐藏到托盘，监控继续在后台跑（双击托盘图标恢复界面）" if has_tray
                else "已最小化到任务栏（托盘未启用），监控继续在后台跑")
        log.info("关闭窗口：%s", note)
        try:
            if has_tray and self.job_running:
                self.tray.set_tooltip("抖音账号监控 — 后台监控中（%d 个账号）" % len(self.accounts))
        except Exception:  # noqa: BLE001
            pass

    def on_automon_change(self):
        want = bool(self.automon_var.get())
        self.cfg["auto_monitor"] = want
        try:
            self.save_config()
        except Exception as exc:  # noqa: BLE001
            log.warning("保存配置失败：%s", exc)
        log.info("启动软件后自动开始监控：%s", "开启（下次启动直接开跑）" if want
                 else "关闭（启动后手动点「开始监控」）")

    def on_close_action_change(self):
        try:
            val = str(self.close_var.get() or "minimize").strip().lower()
        except Exception:  # noqa: BLE001
            val = "minimize"
        self.cfg["close_action"] = "exit" if val in ("exit", "quit", "close") else "minimize"
        self.cfg["close_action_v2"] = True
        self.save_config()
        log.info("关闭窗口时：%s", "隐藏到托盘（监控与录制继续跑）"
                 if self.cfg["close_action"] == "minimize" else "退出程序")

    def reset_default_save_dir(self):
        """恢复默认保存位置（回到数据目录的下载目录）"""
        had = self.default_save_dir()
        if not had and not any(a.get("save_dir") for a in self.accounts):
            log.info("当前已经是默认保存位置（下载目录）")
            messagebox.showinfo("提示", "当前已经是默认设置：作品存在数据目录的下载目录里。")
            return
        if not messagebox.askyesno(
                "恢复默认保存位置",
                "将清除默认保存位置%s，作品改回存在数据目录的下载目录里。\n\n继续吗？"
                % ("（%s）" % had if had else ""), parent=self.root):
            return
        self.cfg["default_save_dir"] = ""
        for acc in self.accounts:
            acc["save_dir"] = ""
        self.save_config()
        self.refresh_tree()
        log.info("已恢复默认保存位置（下载目录）")

    def open_download_file(self, iid):
        """打开某一行对应的本地文件；图集/实况有多个文件时开所在目录"""
        paths = [p for p in (getattr(self, "dl_files", {}).get(iid) or []) if p]
        meta = getattr(self, "dl_meta", {}).get(iid) or {}
        title = meta.get("desc") or "这条作品"
        if not paths:
            messagebox.showinfo(
                "没有文件",
                "「%s」这一条本地没有文件。\n\n"
                "常见原因：\n"
                "· 下载时被跳过（之前下过了，或设置里关掉了视频/图集下载）\n"
                "· 这次下载失败，下一轮会重试\n\n"
                "可以点「打开作品目录」到文件夹里找找。" % title,
                parent=self.root)
            return
        first = paths[0]
        stamp, last = getattr(self, "_dl_open_stamp", ("", 0.0))
        now = time.time()
        if stamp == first and now - last < 1.5:      # 双击 = 两次 click，别开两个窗口
            return
        self._dl_open_stamp = (first, now)
        existing = [p for p in paths if os.path.isfile(p)]
        if not existing:      # 文件被手动搬走了：按同名在数据目录里找回来
            existing = [p for p in (self._relocate_download(x) for x in paths) if p]
        if not existing:
            log.warning("本地找不到文件：%s", "、".join(paths[:3]))
            messagebox.showwarning(
                "文件不在了",
                "本地找不到这一条的文件：\n%s\n\n可能被删除或被移到了别处。"
                % "\n".join(paths[:5]), parent=self.root)
            return
        if len(existing) == 1:
            target = existing[0]
            ok, err = self.shell_open_file(target)
            if ok:
                log.info("打开已下载文件：%s", target)
                self.status_override = ("已打开：%s" % os.path.basename(target), time.time() + 6)
            else:
                messagebox.showerror("打开失败", "打不开这个文件：\n%s\n\n%s" % (target, err),
                                     parent=self.root)
            return
        folder = os.path.dirname(existing[0])
        ok, err = self.shell_open_dir(folder)
        log.info("这一条共 %d 个文件，已打开所在目录：%s", len(existing), folder)
        if ok:
            self.status_override = ("已打开文件夹（这一条有 %d 个文件）" % len(existing),
                                    time.time() + 6)
        else:
            messagebox.showwarning("打开失败", "%s" % err, parent=self.root)

    def open_download_selected(self):
        """「打开所选」按钮 / 回车：打开当前选中那一行的文件"""
        try:
            sel = self.dl_tree.selection()
        except Exception:  # noqa: BLE001
            sel = ()
        if not sel:
            messagebox.showinfo("提示", "先在「本次下载」里点一条，再点「打开所选」。",
                                parent=self.root)
            return
        self.open_download_file(sel[0])

    def _dl_click(self, event):
        """「本次下载」单击一行：选中并直接打开对应文件（双击有两秒防抖，不会开两次）"""
        try:
            iid = self.dl_tree.identify_row(event.y)
        except Exception:  # noqa: BLE001
            return
        if not iid:
            return
        try:
            self.dl_tree.selection_set(iid)
        except Exception:  # noqa: BLE001
            pass
        self.open_download_file(iid)

    def clear_download_list(self):
        """「本次下载」卡片：清空列表与本轮统计（不影响磁盘上已下载的文件）"""
        if getattr(self, "dl_tree", None) is not None:
            try:
                for iid in self.dl_tree.get_children():
                    self.dl_tree.delete(iid)
            except Exception:  # noqa: BLE001
                pass
        self.dl_files.clear()
        self.dl_meta.clear()
        self.dl_kinds.clear()
        self.dl_total_items = 0
        self.dl_total_files = 0
        self.dl_total_size = 0
        if self.dl_sum_var is not None:
            self.dl_sum_var.set("")
        self.status_override = ("已清空本次下载列表（磁盘文件未动）", time.time() + 6)

    def on_log_warn_toggle(self):
        """日志面板：只看提醒/错误（后续新日志按这个过滤）"""
        self.log_warn_only = bool(self.log_warn_only_var.get())
        log.info("日志显示：%s", "只看提醒和错误" if self.log_warn_only else "全部")

    def on_log_detail_toggle(self):
        """日志面板：详细日志 = 显示全部技术细节；默认只看检测/下载/直播事件。"""
        self.log_summary_only = not bool(self.log_detail_var.get())
        self.status_override = (
            "日志：显示全部细节" if not self.log_summary_only else "日志：只显示检测/下载/直播结果",
            time.time() + 6)

    def backfill_missing(self):
        """补下载最近 3 天没落盘的作品（以前漏掉的也能补回来）"""
        if getattr(self, "backfill_busy", False):
            log.info("补下载还在跑，等它跑完再试")
            return
        targets = [a for a in self.accounts if a.get("enabled", True)]
        if not targets:
            messagebox.showinfo("提示", "没有启用的账号", parent=self.root)
            return
        chosen = [a for a in self.selected_accounts() if a.get("enabled", True)]
        if chosen:
            targets = chosen[:3]
        names = "、".join(a.get("nickname") or a.get("target") or "?" for a in targets[:5])
        # 这里必须用 messagebox 直接弹：菜单回调跑在主线程，ask_main 会自己等自己（卡死）
        if not messagebox.askyesno("补下载遗漏作品",
                                   "将检查 %d 个账号（%s）最近 3 天的作品，\n"
                                   "把没有下载到本地的补下来。\n\n继续？" % (len(targets), names),
                                   parent=self.root):
            return
        self.backfill_busy = True
        self.status_var.set("补下载：正在核对最近 3 天的作品…")
        log.info("补下载开始：%d 个账号（%s），只补最近 3 天没落盘的作品", len(targets), names)
        threading.Thread(target=self._backfill_worker, args=(targets,), daemon=True).start()


    def _backfill_worker(self, targets):
        total = 0
        for acc in targets:
            platform = acc.get("platform") or self.cfg.get("default_platform") or "douyin"
            sec = acc.get("sec_user_id") or acc.get("target") or ""
            nickname = acc.get("nickname") or sec
            if not sec:
                continue
            cfg = dict(self.cfg)
            cfg["platform"] = platform
            cfg["sec_user_id"] = sec
            cfg["unique_id"] = acc.get("unique_id") or ""
            cfg["save_dir"] = self.account_save_dir(acc)
            cfg["uid"] = acc.get("uid") or ""
            try:
                dy = self.make_client(platform)
                missing = core.find_missing(cfg, dy, days=3)
            except Exception as exc:  # noqa: BLE001
                log.warning("【%s】补查失败：%s", nickname, core.classify_error(exc, platform))
                continue
            if not missing:
                log.info("【%s】最近 3 天没有漏掉的作品", nickname)
                continue
            log.info("【%s】发现 %d 个没下载的作品，开始补下载…", nickname, len(missing))
            try:
                dest = (cfg.get('save_dir') or '').strip() or core.history_account_dir(cfg, acc)
                alt = [core.history_account_dir(cfg, acc)]
                custom = self.account_save_dir(acc)
                if custom:
                    alt.append(custom)
                alt.append(os.path.join(core.douk_volume_dir(cfg), 'Download'))
                stats, files, text = core.douk_download(cfg, [core.post_url(i) for i in missing],
                                                         platform=platform, dy=dy, dest=dest, alt_dirs=alt)
                core._finish_download(cfg, files, stats, text, missing, None)
                total += len(files)
            except Exception as exc:  # noqa: BLE001
                log.error("【%s】补下载失败：%s", nickname, core.classify_error(exc, platform))
        self.q.put(("backfill_done", total))

    def on_backfill_done(self, total):
        self.backfill_busy = False
        msg = "补下载完成，新增 %d 个文件" % total if total else "补下载完成：没有需要补的作品"
        log.info(msg)
        try:
            self.status_var.set(msg)
            self.status_override = (msg, time.time() + 8)
        except Exception:  # noqa: BLE001
            pass
    def on_audit_done(self, info):
        info = info or {}
        if info.get("dry_run"):
            msg = "核账完成：%d 个作品没落盘（未下载）" % int(info.get("missing") or 0)
        elif int(info.get("already") or 0) and not int(info.get("files") or 0):
            msg = ("核账完成：%d 个作品的视频已经在账号文件夹里（已补下载记录，未重复下载）"
                   % int(info.get("already") or 0))
        elif int(info.get("files") or 0):
            msg = "核账完成：补下 %d 个作品 / %d 个文件" % (int(info.get("items") or 0),
                                                    int(info.get("files") or 0))
        elif info.get("missing"):
            msg = "核账完成：%d 个作品没落盘，下载器没给出新文件" % int(info.get("missing") or 0)
        else:
            msg = "核账完成：未检测到发布新作品"
        log.info(msg)
        try:
            self.status_override = (msg, time.time() + 8)
            running = bool(self.monitor_thread and self.monitor_thread.is_alive())
            self.status_var.set("监控中" if running else msg)
        except Exception:  # noqa: BLE001
            pass



    def set_tiktok_proxy(self):
        """TikTok 在本机经常需要代理才能访问（带连通性测试，避免填了不能用的代理）"""
        if getattr(self, "proxy_win", None) is not None:
            try:
                self.proxy_win.deiconify()
                self.proxy_win.lift()
                return
            except Exception:
                self.proxy_win = None
        win = tk.Toplevel(self.root)
        self.proxy_win = win
        win.title("TikTok 代理")
        win.transient(self.root)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="TikTok 需要本机能访问 tiktok.com",
                  font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        ttk.Label(frm, wraplength=430, justify="left", foreground="#555",
                  text="连不上就填代理，填完点「测试连接」确认。\n"
                       "格式：http://127.0.0.1:7890、socks5://127.0.0.1:1080，"
                       "不带协议会自动按 http 处理。留空表示直连。"
                  ).pack(anchor="w", pady=(6, 12))
        var = tk.StringVar(value=str(self.cfg.get("tiktok_proxy") or ""))
        row = ttk.Frame(frm)
        row.pack(fill="x")
        ttk.Label(row, text="代理地址").pack(side="left")
        ent = ttk.Entry(row, textvariable=var, width=38, style="Data.TEntry")
        ent.pack(side="left", padx=8, fill="x", expand=True)
        result = tk.StringVar(value="")
        lbl = ttk.Label(frm, textvariable=result, foreground="#6b7280", wraplength=430,
                        justify="left")
        lbl.pack(anchor="w", pady=(10, 0))
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(14, 0))
        state = {"busy": False}

        def show(text, color="#6b7280"):
            result.set(text)
            try:
                lbl.configure(foreground=color)
            except Exception:
                pass

        def test():
            if state["busy"]:
                return
            state["busy"] = True
            show("正在测试，请稍候…", "#6b7280")
            proxy = var.get().strip()

            def worker():
                ok, msg = core.probe_proxy(proxy, platform="tiktok", timeout=8)
                self.q.put(("proxy_result", (ok, msg)))

            threading.Thread(target=worker, daemon=True).start()

        def save():
            self.cfg["tiktok_proxy"] = var.get().strip()
            self.save_config()
            self.reset_clients()
            log.info("TikTok 代理已设置为：%s", self.cfg["tiktok_proxy"] or "（不用代理）")
            self.status_override = ("TikTok 代理已保存", time.time() + 8)
            win.destroy()
            self.proxy_win = None

        def clear():
            var.set("")
            show("已清空，点「保存」后生效", "#6b7280")

        ttk.Button(btns, text="测试连接", command=test).pack(side="left")
        ttk.Button(btns, text="清空", command=clear).pack(side="left", padx=6)
        ttk.Button(btns, text="关闭", command=lambda: (win.destroy(),
                                                       setattr(self, "proxy_win", None))
                   ).pack(side="right")
        ttk.Button(btns, text="保存", command=save, style="Primary.TButton"
                   ).pack(side="right", padx=6)
        win.protocol("WM_DELETE_WINDOW", lambda: (win.destroy(),
                                                  setattr(self, "proxy_win", None)))
        ent.focus_set()

    def on_proxy_result(self, payload):
        ok, msg = payload
        win = getattr(self, "proxy_win", None)
        for child in (win.winfo_children() if win else []):
            for sub in child.winfo_children():
                if isinstance(sub, ttk.Label) and "textvariable" in sub.keys():
                    try:
                        if str(sub.cget("textvariable")):
                            sub.configure(foreground="#1f7a33" if ok else "#c0392b")
                    except Exception:
                        pass
        self.proxy_busy = False
        log.info("TikTok 代理测试：%s", msg)
        self.status_override = (("代理可用：" if ok else "代理不可用：") + msg,
                                time.time() + 12)

    def on_notify_change(self):
        on = bool(self.notify_var.get())
        self.cfg["notify_enabled"] = on
        try:
            self.save_config()
        except Exception as exc:  # noqa: BLE001
            log.warning("保存配置失败：%s", exc)
        log.info("桌面通知已%s（有新作品/开播时%s弹气泡）", "开启" if on else "关闭",
                 "会" if on else "不再")

    def _check_live_account(self, acc):
        """独立直播轮：只查账号资料（profile/other，带 live_status）+ 直播拉流地址，
        完全不碰作品列表 POST 接口。
        这样作品列表接口被风控冷却时，开播检测照样能按自己的间隔跑。"""
        platform = core.platform_of(acc)
        sec = acc.get("sec_user_id") or ""
        with self.check_lock:
            dy = self.get_client(platform)
            try:
                dy.reload_cookie()
            except Exception as exc:  # noqa: BLE001
                log.debug("直播检测前重读 Cookie 失败：%s", exc)
            if not sec:
                if platform == "tiktok":
                    spec = acc.get("unique_id") or acc.get("target") or ""
                    p0 = dy.fetch_profile(spec) if spec else {}
                    sec = str(p0.get("sec_uid") or "").strip() or (
                        "tiktok:%s" % (acc.get("unique_id") or acc.get("target") or "unknown"))
                else:
                    sec = dy.resolve_sec_uid(acc["target"])
                acc["sec_user_id"] = sec
            st = self.state["accounts"].setdefault(
                sec, {"seen": {}, "last_live_room": "", "live_active": False,
                      "first_run_done": False})
            cfg = dict(self.cfg)
            cfg["platform"] = platform
            cfg["sec_user_id"] = sec
            cfg["unique_id"] = acc.get("unique_id") or ""
            cfg["save_dir"] = self.account_save_dir(acc)
            # TikTok 的资料接口按 @用户名 取；抖音按 sec_uid 取
            pspec = (acc.get("unique_id") or sec or acc.get("target") or "") \
                if platform == "tiktok" else sec
            profile = dy.fetch_profile(pspec)
            nickname = profile.get("nickname") or acc.get("nickname") or acc.get("target") or ""
            live_key = str(sec or acc.get("target") or "")
            was_live = bool(st.get("live_active"))
            was_room = str(st.get("last_live_room") or "")
            report = {}
            try:
                core.check_live(cfg, dy, st, profile, report=report)
            except Exception as exc:  # noqa: BLE001
                log.warning("【%s】直播检测失败：%s", nickname, core.classify_error(exc))
            now_room = str(st.get("last_live_room") or "")
        # sync_live / 状态更新放锁外：只碰队列和录制管理器（各自有锁），缩短持锁时间
        self.sync_live(acc, live_key, nickname, report.get("live"), was_live)
        now_live = bool(st.get("live_active"))
        # 只有开播/下播翻转、或换了一场（room_id 变）才需要写盘；
        # 同一场直播 check_live 照样回填 report["live"]，但状态没变，不必刷盘。
        changed = (was_live != now_live) or (was_room != now_room)
        acc["live"] = now_live
        # 独立直播轮结束时也要把账号状态改回来，否则左侧「状态」列一直停在「直播中」
        if now_live:
            acc["state"] = "直播中"
        elif str(acc.get("state") or "") == "直播中":
            acc["state"] = "监控中"
        if report.get("live") and self.session is not None:
            self.session["lives"].append({
                "time": time.time(), "account": nickname,
                "title": report["live"].get("title") or "",
                "room": report["live"].get("room") or "",
                "quality": report["live"].get("quality") or "",
                "quality_label": report["live"].get("quality_label") or "",
                "txt": report["live"].get("txt") or "",
            })
            del self.session["lives"][:-self.MAX_SESSION_ITEMS]
        self.q.put(("row", acc))
        return changed

    def live_cycle(self):
        """跑一轮独立直播检测（所有启用账号），只打 profile/other，不碰作品列表接口。"""
        if not bool(self.run_cfg.get("live", self.cfg["live_check"])):
            return
        if str(self.run_cfg.get("scope") or self.cfg.get("monitor_scope") or "both") == "posts":
            return
        accounts = [a for a in self.accounts if a.get("enabled", True)][
            : int(self.cfg["max_accounts_per_round"])]
        changed = False
        for acc in accounts:
            if self.stop_event.is_set():
                break
            try:
                changed = bool(self._check_live_account(acc)) or changed
            except Exception as exc:  # noqa: BLE001
                log.debug("【%s】直播检测轮出错：%s", acc.get("nickname"), exc)
        self.live_cycle_count += 1
        log.debug("直播检测第 %d 轮：%d 个账号，状态变化=%s",
                  self.live_cycle_count, len(accounts), changed)
        if changed:
            # 只有开播/下播状态变了才写盘：live_poll_interval 最快 10 秒，
            # 每轮无条件写会把 state 文件刷得没必要地勤。
            try:
                self.save_state()
            except Exception as exc:  # noqa: BLE001
                log.debug("直播检测后存状态失败：%s", exc)

    def _idle(self, end):
        """睡到下一次作品检测（end），期间按「直播检测间隔」独立插入直播轮。

        作品列表接口被风控冷却（rate_wait）时作品轮整轮跳过，但这里照样按
        live_poll_interval 触发 live_cycle —— 开播检测不会因为作品接口限流而停摆。
        """
        separate = bool(self.run_cfg.get("live_separate",
                                         self.cfg.get("live_separate", True)))
        live_on = bool(self.run_cfg.get("live", self.cfg["live_check"]))
        live_gap = max(MIN_LIVE_INTERVAL, int(
            self.run_cfg.get("live_interval") or self.cfg.get("live_poll_interval") or 300))
        use_live = separate and live_on
        while not self.stop_event.is_set():
            now = time.time()
            if use_live and now >= self.next_live_ts:
                fired_at = now
                try:
                    self.live_cycle()
                except Exception as exc:  # noqa: BLE001
                    log.debug("直播检测轮出错：%s", exc)
                # 以触发时刻为基准，避免 live_cycle 自身耗时让间隔越拉越长
                self.next_live_ts = fired_at + live_gap
                if self.next_live_ts <= now:
                    self.next_live_ts = now + live_gap
                continue
            if now >= end:
                return
            deadline = min(end, self.next_live_ts) if use_live else end
            time.sleep(min(0.5, max(0.05, deadline - time.time())))

    def log_round_summary(self, stats, cost, wait):
        """一轮结束只写一行：哪个都没更新时不再每账号刷一句「暂无更新」"""
        if stats.get("rate_wait"):
            log.info("第 %d 轮：抖音作品列表接口限流中，暂停约 %.0f 分钟后自动恢复"
                     "（已下载内容和单独下载都不受影响，无需操作）",
                     self.cycle_count, float(stats["rate_wait"]) / 60.0)
            return
        if stats.get("rate_hit"):
            log.info("第 %d 轮：抖音作品列表接口触发风控，程序自动降频进入冷却，"
                     "冷却结束后自动恢复（无需操作）", self.cycle_count)
            return
        if not stats:
            log.info("第 %d 轮没能跑（原因见上面的错误行），下一轮继续（用时 %.0f 秒，下一轮 %s）",
                     self.cycle_count, cost,
                     time.strftime("%H:%M:%S", time.localtime(time.time() + wait)))
            return
        checked = int(stats.get("checked") or 0)
        new_n = int(stats.get("new") or 0)
        files = int(stats.get("files") or 0)
        failed = int(stats.get("failed") or 0)
        nxt = time.strftime("%H:%M:%S", time.localtime(time.time() + wait))
        parts = ["检查 %d 个账号" % checked]
        parts.append("新作品 %d 个" % new_n if new_n else "没有新作品")
        if files:
            parts.append("下载 %d 个文件" % files)
        if failed:
            parts.append("%d 个账号出错" % failed)
        live_n = sum(1 for a in self.accounts if a.get("live"))
        if live_n:
            parts.append("直播中 %d 个" % live_n)
        deep_n = int(stats.get("deep") or 0)
        if deep_n:
            if int(stats.get("deep_missing") or 0):
                parts.append("历史核账补了 %d 个" % int(stats.get("deep_missing") or 0))
            elif int(stats.get("deep_full") or 0) >= deep_n:
                parts.append("全量核账无缺失")
            else:
                parts.append("未检测到发布新作品")
        log.info("第 %d 轮检查完成：%s（用时 %.0f 秒，下一轮 %s）",
                 self.cycle_count, "，".join(parts), cost, nxt)
        try:
            self.status_var.set("监控中 · 第 %d 轮完成 · 下一轮 %s" % (self.cycle_count, nxt))
        except Exception:  # noqa: BLE001
            pass
    def default_platform(self):
        """设置里的「默认平台」：抖音 / TikTok"""
        return core.platform_of(self.cfg.get("default_platform") or core.DEFAULT_PLATFORM)

    def set_default_save_dir(self):
        """设置「默认保存位置」：所有没有单独设置的账号都用它，新的账号也用它"""
        current = self.default_save_dir() or os.path.join(
            self.current_douk_dir(), "Volume", "Download")
        path = filedialog.askdirectory(
            parent=self.root, title="选择默认保存位置（所有账号通用）",
            initialdir=current if os.path.isdir(current) else BASE_DIR, mustexist=False)
        if not path:
            return
        path = os.path.abspath(path)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("无法使用该目录", "创建目录失败：\n%s\n\n%s" % (path, exc))
            return
        self.cfg["default_save_dir"] = path
        overrides = [a for a in self.accounts if a.get("save_dir")]
        cleared = 0
        if overrides:
            if messagebox.askyesno(
                    "默认保存位置",
                    "默认保存位置已设为：\n%s\n\n"
                    "当前有 %d 个账号单独设置过保存位置。\n"
                    "是否让这些账号也改用默认位置？（选“否”保留它们的单独设置）"
                    % (path, len(overrides)), parent=self.root):
                for acc in overrides:
                    acc["save_dir"] = ""
                cleared = len(overrides)
        self.save_config()
        self.refresh_tree()
        log.info("默认保存位置已设置：%s（清除单独设置 %d 个账号）", path, cleared)
        self.status_override = ("默认保存位置：%s" % path, time.time() + 8)

    def _audit_worker(self, targets, trigger, dry_run=False):
        # 账号名单在「开始监控」那行已经列过，这里不重复
        log.info("历史核账开始：%d 个账号（%s）%s", len(targets), trigger,
                 "，只报告不下载" if dry_run else "")
        total_items = total_files = total_missing = total_already = 0
        try:
            for acc in targets:
                if trigger == "开始监控" and self.stop_event.is_set():
                    log.info("监控已停止，历史核账中断")
                    break
                if core.rate_limit_remaining() > 0:
                    log.info("历史核账：作品列表接口限流中，剩余账号先跳过（冷却结束会自动恢复）")
                    break
                res = self.audit_account(acc, trigger=trigger, dry_run=dry_run, quiet=False) or {}
                total_items += int(res.get("items") or 0)
                total_files += int(res.get("files") or 0)
                total_missing += int(res.get("missing") or 0)
                total_already += int(res.get("already") or 0)
        finally:
            self.audit_running = False
            self.q.put(("audit_done", {"trigger": trigger, "missing": total_missing,
                                       "items": total_items, "files": total_files,
                                       "already": total_already,
                                       "dry_run": dry_run}))
    def _relocate_download(self, path):
        """文件被挪走时按文件名在数据目录 Volume 下找回来（只找两层，够用且快）"""
        name = os.path.basename(str(path or ""))
        if not name:
            return ""
        try:
            root = os.path.join(core.data_root(self.cfg), "Volume")
        except Exception:  # noqa: BLE001
            return ""
        if not os.path.isdir(root):
            return ""
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath[len(root):].count(os.sep)
            if depth >= 2:
                dirnames[:] = []
            if name in filenames:
                return os.path.join(dirpath, name)
        return ""

    def get_client(self, platform=None):
        """按平台取数据接口对象（抖音 DouYin / TikTok TikTok），同一平台复用"""
        platform = core.platform_of(platform or self.default_platform())
        with self.dy_lock:
            client = self.clients.get(platform)
            if client is None:
                client = core.make_client(self.cfg, platform,
                                          timeout=int(self.cfg.get("request_timeout") or 20))
                self.clients[platform] = client
            return client

    SASH_LEFT_MIN = 460     # 96 DPI 下的参考值；实际夹取时按 DPI 缩放（见 on_sash_changed）
    SASH_RIGHT_MIN = 480    # 同上

    def on_sash_motion(self, event=None):
        """拖动过程中一旦靠边就立刻夹住，避免把一侧拖成 0 宽后再反弹。"""
        self.on_sash_changed(event)

    def on_sash_changed(self, event=None):
        """拖动分隔栏后：把位置夹在两边都能看清的范围里，并让两侧重新布局。"""
        try:
            width = self.pane.winfo_width()
            # 夹取范围按 DPI 缩放：高 DPI 下字体/列宽都放大了，若还用 96 DPI 的
            # 固定像素值，左侧会被夹得比列宽总和还窄 → 横向滚动条 → 看着像"不能缩放"。
            lo = hi_dpi.scale_px(self.SASH_LEFT_MIN)
            hi = max(lo, width - hi_dpi.scale_px(self.SASH_RIGHT_MIN))
            pos = self.pane.sashpos(0)
            target = min(max(int(pos), lo), hi)
            if target != pos:
                self.pane.sashpos(0, target)
            self.root.after_idle(self.repaint_sides)
        except Exception:  # noqa: BLE001
            pass

    def repaint_sides(self):
        """分隔栏拖动后重绘账号表和右侧面板，避免残留旧宽度/空白。"""
        for w in (getattr(self, "tree", None), getattr(self, "live_tree", None),
                  getattr(self, "dl_tree", None), getattr(self, "log_text", None)):
            if w is None:
                continue
            try:
                w.update_idletasks()
            except Exception:  # noqa: BLE001
                pass

    def layout_right_panels(self):
        """右侧三块按顺序排：直播录制 → 本次下载 → 运行日志"""
        if getattr(self, "dl_outer", None) is None:
            return
        for w in (self.live_outer, self.dl_outer, self.log_outer):
            try:
                w.pack_forget()
            except Exception:  # noqa: BLE001
                pass
        if self.live_visible:
            self.live_outer.pack(fill="x", pady=(0, 8))
        if self.dl_visible:
            self.dl_outer.pack(fill="x", pady=(0, 8))
        self.log_outer.pack(fill="both", expand=True)

    def close_live_panel(self):
        """「直播录制」右上角 ✕：只收起面板，录制继续。"""
        self.live_hidden = True
        self.live_visible = False
        self.layout_right_panels()
        self.status_override = ("已收起「直播录制」面板（录制不受影响，可从 设置→界面与窗口 重新显示）",
                                time.time() + 6)

    def close_download_panel(self):
        """「本次下载」右上角 ✕：收起面板，磁盘文件不动。"""
        self.dl_hidden = True
        self.dl_visible = False
        self.layout_right_panels()
        self.status_override = ("已收起「本次下载」面板（可从 设置→界面与窗口 重新显示）",
                                time.time() + 6)

    def show_live_panel(self):
        self.live_hidden = False
        self.live_visible = True
        self.layout_right_panels()

    def show_download_panel(self):
        self.dl_hidden = False
        self.dl_visible = True
        self.layout_right_panels()

    def make_client(self, platform=None, timeout=None):
        """按当前配置构造 抖音/TikTok 接口客户端（补下载、核账用）"""
        return core.make_client(self.cfg,
                                core.platform_of(platform or self.default_platform()),
                                timeout=timeout or self.cfg.get("request_timeout", 20))

    def rebuild_ui(self):
        """销毁并重建整个界面（换主题用），保留账号/录制器/定时器"""
        if getattr(self, "live_tick_id", None):
            try:
                self.root.after_cancel(self.live_tick_id)
            except Exception:  # noqa: BLE001
                pass
            self.live_tick_id = None
        for w in list(self.root.winfo_children()):
            if isinstance(w, tk.Toplevel):
                continue
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                pass
        self.live_rows = {}
        self.live_visible = False
        self.cookie_badges = {}
        self.setup_styles()
        self.build_ui()
        self.refresh_tree()
        self.refresh_cookie_badges()

    def refresh_account(self, acc):
        """单个账号立即刷新：查直播状态 + 主页有没有新作品，有就马上下载"""
        nick = acc.get("nickname") or acc.get("target") or ""
        if acc.get("refresh_busy"):
            log.info("【%s】正在刷新中，稍等…", nick)
            return
        plat = core.platform_of(acc)
        if not self.cookie_ready(plat):
            log.warning("【%s】还没有登录 %s 的 Cookie，先在「设置 → 扫码登录」登录", nick,
                        core.platform_label(plat))
            messagebox.showinfo("需要登录",
                                "还没有登录 %s 的 Cookie，先登录再刷新。" % core.platform_label(plat),
                                parent=self.root)
            return
        acc["refresh_busy"] = True
        acc["state"] = "刷新中"
        self.update_row(acc)
        log.info("【%s】手动刷新：检查直播状态与主页更新…", nick)
        threading.Thread(target=self._refresh_worker, args=(acc,), daemon=True).start()


    def _refresh_worker(self, acc):
        nick = acc.get("nickname") or acc.get("target") or ""
        left = core.rate_limit_remaining()
        if left > 0:
            acc["state"] = "接口限流"
            acc["refresh_busy"] = False
            self.q.put(("refresh_done", {"acc": acc, "nickname": nick,
                                         "items": 0, "live": False,
                                         "error": "接口限流中，约 %d 分钟后自动恢复" % (left / 60 + 1)}))
            return
        try:
            dy = self.get_client(core.platform_of(acc))
            try:
                dy.reload_cookie()
            except Exception as exc:  # noqa: BLE001
                log.debug("刷新前重读 Cookie 失败：%s", exc)
            with self.check_lock:
                # 手动刷新不弹历史下载询问：只查直播 + 新作品，历史作品走「下载历史视频」
                res = self.check_account(dy, acc, ask_history=False) or {}
            # 手动刷新 = 和「开始监控」一样：先把主页所有作品和下载记录核一遍
            deep = self.audit_account(acc, trigger="手动刷新", quiet=True) or {}
            acc["last_check"] = time.strftime("%H:%M:%S")
            acc["state"] = "监控中"
            self.q.put(("refresh_done", {"acc": acc, "nickname": nick,
                                        "items": int(res.get("items") or 0) + int(deep.get("items") or 0),
                                        "deep_posts": int(deep.get("posts") or 0),
                                        "deep_missing": int(deep.get("missing") or 0),
                                        "live": bool(res.get("live"))}))
        except Exception as exc:  # noqa: BLE001
            msg = core.classify_error(exc)
            acc["state"] = "出错: %s" % str(exc)[:18]
            self.q.put(("refresh_done", {"acc": acc, "nickname": nick,
                                        "items": 0, "live": False, "error": msg}))
        finally:
            acc["refresh_busy"] = False
    def live_column_tag(self, name, default="#7"):
        try:
            return "#%d" % (list(self.live_tree["columns"]).index(name) + 1)
        except Exception:  # noqa: BLE001
            return default

    def refresh_live_panel(self):
        """刷新「直播录制」卡片：录制中的、直播中但没录的，都显示一行。"""
        if getattr(self, "live_tree", None) is None:
            return
        snaps = {}
        if self.live_mgr:
            for s in self.live_mgr.snapshots():
                snaps[s["key"]] = s
        rows = []
        for key, info in list(self.live_info.items()):
            if key in snaps:
                continue      # 已有录制记录的行，以录制记录为准（含已暂停的）
            rows.append({
                "key": key, "nickname": info.get("nickname") or key,
                "quality": info.get("quality") or "",
                "quality_label": info.get("quality_label") or "",
                "resolution": "", "duration": "", "size_text": "",
                "state": "直播中（未录制）", "running": False,
                "error": "", "live": True, "_pseudo": True,
            })
        for key, snap in snaps.items():
            row = dict(snap)
            row["live"] = bool((self.live_info.get(key) or {}).get("live")) \
                or bool(snap.get("running"))
            rows.append(row)
        if not rows:
            if self.live_rows:
                for iid in list(self.live_rows.values()):
                    try:
                        self.live_tree.delete(iid)
                    except Exception:  # noqa: BLE001
                        pass
                self.live_rows = {}
            self.live_sum_var.set("")
            if self.live_visible and not self.live_hidden:
                self.live_visible = False
                self.layout_right_panels()
            return
        if not self.live_visible and not self.live_hidden:
            self.live_visible = True
            self.layout_right_panels()
        running = [r for r in rows if r.get("running")]
        live_n = sum(1 for r in rows if r.get("live"))
        if running:
            total = sum(int(r.get("seconds") or 0) for r in running)
            self.live_sum_var.set("%d 个直播间录制中 · 合计 %s · %s" % (
                len(running), live_record.format_seconds(total),
                running[0].get("quality_label") or ""))
        elif live_n:
            self.live_sum_var.set("%d 个账号正在直播（未录制）" % live_n)
        else:
            self.live_sum_var.set("没有正在录制的直播（下面的记录已结束）")
        keys = set()
        for row in rows:
            key = row["key"]
            keys.add(key)
            iid = self.live_rows.get(key)
            if row.get("running"):
                tag, state, act = ("rec",), "录制中", "暂停"
            elif str(row.get("state") or "").startswith("出错"):
                tag = ("err",)
                state = "出错" + (("：" + row["error"][:20]) if row.get("error") else "")
                act = "开始" if row.get("live") else ""
            elif row.get("live"):
                tag = ("done",)
                state = row.get("state") or "已暂停"
                act = "开始" if row.get("_pseudo") else "继续"
            else:
                tag, state, act = ("done",), row.get("state") or "", ""
            qtxt = ("%s（%s）" % (row.get("quality_label") or "-", row.get("quality") or "-")
                    if row.get("quality") else (row.get("quality_label") or "-"))
            values = (row.get("nickname") or "", qtxt, row.get("resolution") or "—",
                      row.get("duration") or "", row.get("size_text") or "", state, act)
            if iid:
                self.live_tree.item(iid, values=values, tags=tag)
            else:
                iid = self.live_tree.insert("", "end", values=values, tags=tag)
                self.live_rows[key] = iid
        for key, iid in list(self.live_rows.items()):
            if key not in keys:
                try:
                    self.live_tree.delete(iid)
                except Exception:  # noqa: BLE001
                    pass
                self.live_rows.pop(key, None)

    def on_live_click(self, event):
        """点「操作」列：录制中 → 暂停；已暂停但还在直播 → 继续/开始。"""
        if self.live_tree.identify_column(event.x) != self.live_column_tag("act"):
            return None
        iid = self.live_tree.identify_row(event.y)
        if not iid:
            return None
        key = ""
        for k, v in self.live_rows.items():
            if v == iid:
                key = k
                break
        if key:
            self.toggle_live_recording(key)
            return "break"
        return None

    def toggle_live_recording(self, key):
        info = self.live_info.get(key) or {}
        nick = info.get("nickname") or key
        rec = self.live_mgr.get(key) if self.live_mgr else None
        if rec is not None and rec.running:
            if self.live_mgr:
                self.live_mgr.stop(key, reason="手动暂停录制")
            self.status_override = ("已暂停录制：%s" % nick, time.time() + 5)
            self.refresh_live_panel()
            return
        url = info.get("flv") or info.get("hls") or ""
        if not url or not info.get("live"):
            log.warning("【%s】现在没有可用的直播地址（可能已下播），等下一轮检测再试", nick)
            self.status_override = ("等下一轮检测到开播后再开始录制", time.time() + 5)
            return
        if not self.live_mgr:
            log.warning("直播录制器不可用，无法开始录制")
            return
        self.live_mgr.start(
            key, nickname=nick, platform=info.get("platform") or "douyin",
            url=url, quality=info.get("quality") or "",
            quality_label=info.get("quality_label") or "", title=info.get("title") or "")
        self.refresh_live_panel()

    def reset_clients(self):
        """Cookie / 代理改了之后丢弃旧的接口对象"""
        with self.dy_lock:
            self.clients = {}
            self.dy = None

    def run_auto_clean(self, force=False):
        """自动清理：配置了 auto_clean_days 就每 24 小时清一次"""
        days = int(self.cfg.get("auto_clean_days") or 0)
        if days <= 0:
            return
        now = time.time()
        last = float(self.cfg.get("last_clean_ts") or 0)
        if not force and now - last < 86400:
            return
        try:
            res = core.cleanup_data(self.cfg, days=days)
        except Exception as exc:  # noqa: BLE001
            log.warning("自动清理失败：%s", exc)
            return
        self.cfg["last_clean_ts"] = now
        self.save_config()
        if res["files"] or res["records"]:
            log.info("自动清理：删除 %d 个超过 %d 天的旧文件（%s），清理 %d 条失效记录",
                     res["files"], days, human_size(res["freed"]), res["records"])

    def set_autostart(self, enabled):
        """开机自启：写 HKCU 的 Run 键"""
        import winreg

        exe = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(sys.argv[0])
        cmd = '"%s" --minimized' % exe
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    winreg.SetValueEx(key, "DouYinMonitor", 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, "DouYinMonitor")
                    except FileNotFoundError:
                        pass
        except OSError as exc:
            messagebox.showerror("设置开机自启失败", str(exc), parent=self.root)
            self.autostart_var.set(not enabled)
            return False
        self.cfg["auto_start"] = bool(enabled)
        self.save_config()
        log.info("开机自启已%s（%s）", "开启" if enabled else "关闭", cmd)
        return True

    def setup_tray(self):
        """启动托盘图标（失败只记日志，不影响主窗口）"""
        if not self.cfg.get("tray_enabled", True):
            self.tray_note = "已关闭"
            return False
        try:
            import tray as tray_mod
        except Exception as exc:  # noqa: BLE001
            self.tray_note = "托盘模块加载失败：%s" % exc
            log.warning("托盘模块加载失败：%s", exc)
            return False
        if not tray_mod.available():
            self.tray_note = "当前系统不支持托盘"
            return False
        if getattr(self, "tray", None) is not None:
            return True
        try:
            t = tray_mod.Tray(
                tooltip="抖音账号监控 — 监控中",
                on_command=lambda cmd: self.q.put(("tray", cmd)))
        except Exception as exc:  # noqa: BLE001
            self.tray_note = "托盘创建失败：%s" % exc
            log.warning("托盘创建失败：%s", exc)
            return False
        if not t.start():
            self.tray_note = "托盘启动失败：%s" % (t.error or "未知原因")
            log.warning("托盘启动失败：%s", self.tray_note)
            return False
        self.tray = t
        self.tray_note = "托盘已在运行"
        log.info("托盘图标已启动（双击显示主界面，右键有菜单）")
        return True

    def on_tray_command(self, cmd):
        """托盘菜单动作（托盘线程 → q 队列 → 主线程执行）"""
        cmd = str(cmd or "")
        if cmd == "show":
            self.show_from_tray()
        elif cmd == "toggle":
            self.toggle_monitor("托盘菜单")
        elif cmd == "quit":
            self.on_close(force=True)

    def shell_open_file(self, path):
        """用系统默认程序打开文件，返回 (ok, err)；失败退回资源管理器定位"""
        err = ""
        try:
            os.startfile(path)  # noqa: S606  (Windows：交给文件关联)
            return True, ""
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            log.warning("startfile 打开文件失败（%s），改用 explorer 定位：%s", exc, path)
        explorer = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")
        if not os.path.isfile(explorer):
            explorer = "explorer.exe"
        try:
            subprocess.Popen('%s /select,"%s"' % (explorer, os.path.normpath(path)),
                             env=core.child_env(), close_fds=True)
            return True, ""
        except OSError as exc:  # noqa: PERF203
            log.error("explorer 定位失败（%s）：%s", exc, path)
            return False, "%s；%s" % (err, exc)

    @staticmethod
    def size_text(n):
        n = float(n or 0)
        if n < 1024:
            return "%d B" % n
        for unit in ("KB", "MB", "GB", "TB"):
            n /= 1024.0
            if n < 1024 or unit == "TB":
                return "%.1f %s" % (n, unit)
        return "%.1f TB" % n

    # ------------------------------------------------------ 直播录制目录
    def record_dir(self):
        """录像保存目录（配置优先，否则 数据目录/直播录像）"""
        try:
            return core.live_record_dir(self.cfg)
        except Exception:  # noqa: BLE001
            return os.path.join(BASE_DIR, "直播录像")

    # ------------------------------------------------ 历史作品全量核账
    def audit_limit(self):
        try:
            return max(1, int(self.cfg.get("audit_limit_per_run") or 200))
        except Exception:  # noqa: BLE001
            return 200

    @staticmethod
    def audit_count_text(declared, fetched):
        """核账里的「主页多少个作品」：以资料页 aweme_count 为准，接口差额写清楚。"""
        declared = int(declared or 0)
        fetched = int(fetched or 0)
        if not declared:
            return "公开作品 %d 个（资料页没给计数）" % fetched
        if declared == fetched:
            return "作品 %d 个（资料页计数与公开列表一致）" % fetched
        if declared > fetched:
            return ("资料页计数 %d 个，公开作品 %d 个（差 %d 条**不对外公开**：私密 / 仅好友可见 / "
                    "审核中，公开接口翻到底也没有）" % (declared, fetched, declared - fetched))
        return "资料页计数 %d 个，公开作品 %d 个（比计数还多，可能有置顶或计数未更新）" % (
            declared, fetched)

    def audit_extra_text(self, declared, fetched, pages=None):
        """核账那行末尾的括号补充：页数 + 接口计数差；没有可写的就不加括号"""
        bits = []
        if pages:
            bits.append("%d 页翻到底" % int(pages))
        if declared and int(declared) != int(fetched):
            bits.append("接口计数 %d，%d 条不公开" % (int(declared), int(declared) - int(fetched)))
        return "（%s）" % "；".join(bits) if bits else ""

    def resolve_account_dir(self, acc):
        """这个账号最该打开的目录：有文件的账号目录 > 已存在的目录 > 第一个候选"""
        if not acc:
            return ""
        try:
            cands = self.account_download_dirs(acc)
        except Exception:  # noqa: BLE001
            cands = []
        existing = [d for d in cands if os.path.isdir(d)]
        nonempty = [d for d in existing if self._dir_weight(d)[0] > 0]
        if nonempty:
            return max(nonempty, key=self._dir_weight)
        if existing:
            return existing[0]
        return cands[0] if cands else ""

    def open_account_save_dir(self, acc):
        """点「保存位置」那一格：打开这个账号的作品目录"""
        if not acc:
            return
        target = self.resolve_account_dir(acc)
        name = acc.get("nickname") or acc.get("target") or ""
        if not target:
            messagebox.showwarning("打不开", "没有找到【%s】的保存目录。" % name)
            return
        log.info("【%s】保存位置：%s", name, target)
        self.open_path(target, create=True)

    def set_status(self, text, secs=8):
        """状态栏临时提示（走 status_override 机制，几秒后自动恢复）"""
        self.status_override = (str(text or ""), time.time() + float(secs or 8))

    def selected_account(self):
        """当前选中的单个账号；没有选中或选中多个时返回 None"""
        accs = self.selected_accounts()
        return accs[0] if len(accs) == 1 else None

    def setup_styles(self):
        """扁平 + 白卡片的整体风格（clam 主题才能改按钮颜色）"""
        c = getattr(self, "colors", None) or self.COLORS
        font = ("Microsoft YaHei UI", 9)
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        self.root.configure(bg=c["bg"])

        style.configure(".", font=font, background=c["bg"], foreground=c["text"])
        style.configure("TFrame", background=c["bg"])
        style.configure("Card.TFrame", background=c["card"])
        style.configure("Header.TFrame", background=c["header"])
        style.configure("Status.TFrame", background=c["border"])
        style.configure("TSeparator", background=c["border"])

        style.configure("Title.TLabel", background=c["card"], foreground=c["text"],
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Hint.TLabel", background=c["card"], foreground=c["muted"])
        style.configure("Body.TLabel", background=c["card"], foreground=c["text"])
        style.configure("HeaderTitle.TLabel", background=c["header"],
                        foreground=c["header_text"],
                        font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("HeaderSub.TLabel", background=c["header"],
                        foreground=c["header_sub"])
        style.configure("HeaderBadge.TLabel", background=c["header"],
                        foreground=c["header_sub"])
        style.configure("Status.TLabel", background=c["border"], foreground=c["muted"])

        style.configure("TButton", padding=(10, 5), relief="flat", borderwidth=1,
                        background=c["card"], foreground=c["text"],
                        bordercolor=c["border"], focusthickness=0,
                        focuscolor=c["card"], font=font)
        style.map("TButton",
                  background=[("active", "#e9eef8"), ("disabled", c["card"])],
                  foreground=[("disabled", "#a8b0bd")],
                  bordercolor=[("active", "#c9d5ea")])
        style.configure("Tool.TButton", padding=(10, 5))
        style.configure("Ghost.TButton", padding=(7, 3), borderwidth=0,
                        background=c["card"], foreground=c["muted"])
        style.map("Ghost.TButton", background=[("active", "#eef2f8")])
        style.configure("Primary.TButton", padding=(20, 8), borderwidth=0,
                        background=c["accent"], foreground="#ffffff",
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton",
                  background=[("active", c["accent_active"]), ("disabled", "#a9c4f6")],
                  foreground=[("disabled", "#ffffff")])
        style.configure("HeaderBtn.TButton", padding=(8, 3), borderwidth=1,
                        background=c["header"], foreground="#dbe4f2",
                        bordercolor="#3a4763", font=font)
        style.map("HeaderBtn.TButton",
                  background=[("active", "#27344e")],
                  bordercolor=[("active", "#4d5f85")])

        style.configure("TEntry", fieldbackground=c["card"], bordercolor=c["border"],
                        padding=4, insertcolor=c["text"], relief="flat")
        style.configure("Data.TEntry", fieldbackground="#f4f6fa", foreground=c["muted"],
                        bordercolor=c["border"], padding=4, relief="flat")
        style.configure("TCombobox", fieldbackground=c["card"], background=c["card"],
                        bordercolor=c["border"], arrowcolor=c["muted"], padding=3)
        style.map("TCombobox", fieldbackground=[("readonly", c["card"])],
                  bordercolor=[("active", c["accent"])])
        style.configure("Header.TCombobox", fieldbackground="#27344e",
                        background="#27344e", foreground="#ffffff",
                        bordercolor="#3a4763", arrowcolor="#c9d5ea", padding=1)
        style.map("Header.TCombobox",
                  fieldbackground=[("readonly", "#27344e")],
                  foreground=[("readonly", "#ffffff")])

        style.configure("TCheckbutton", background=c["card"], foreground=c["text"],
                        focuscolor=c["card"])
        style.map("TCheckbutton", background=[("active", c["card"])])
        style.configure("TSpinbox", arrowcolor=c["muted"], padding=3,
                        fieldbackground=c["card"], bordercolor=c["border"],
                        insertcolor=c["text"])

        _row_h = hi_dpi.scale_px(int(self.cfg.get("row_height", 30) or 30))
        style.configure("Treeview", background=c["card"], fieldbackground=c["card"],
                        foreground=c["text"], rowheight=_row_h, borderwidth=0,
                        font=font)
        style.configure("Treeview.Heading", background="#f4f6fa",
                        foreground="#4a5568", relief="flat", borderwidth=0,
                        padding=(8, 8), font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Treeview.Heading", background=[("active", "#e9eef8")])
        style.map("Treeview", background=[("selected", c["select"])],
                  foreground=[("selected", "#12305c")])
        style.configure("TScrollbar", background="#e6eaf1", troughcolor=c["card"],
                        bordercolor=c["card"], arrowcolor=c["muted"], relief="flat")

    # ------------------------------------------------------ 状态指示器
    def cookie_state(self, platform=None):
        """ok=已登录 / partial=有 Cookie 但没有 sessionid / none=没有登录"""
        platform = core.platform_of(platform or self.default_platform())
        try:
            jar = core.load_cookie_map(self.cfg, platform)
        except Exception:
            jar = {}
        if not jar:
            return "none"
        if str(jar.get("sessionid") or "").strip():
            return "ok"
        return "partial"

    def refresh_cookie_badges(self):
        """顶部「抖音 Cookie / TikTok Cookie」指示灯"""
        badges = getattr(self, "cookie_badges", None) or {}
        verify = getattr(self, "_cookie_verify", {}) or {}
        for plat, lbl in badges.items():
            st = self.cookie_state(plat)
            name = core.platform_label(plat)
            if st == "ok":
                ok_v = verify.get(plat, (None, ""))[0]
                if ok_v is False:
                    text, color = "%s Cookie：验证未通过" % name, "#b45309"
                elif ok_v is True:
                    text, color = "%s Cookie：已登录 ✔" % name, "#1f7a33"
                else:
                    text, color = "%s Cookie：已登录" % name, "#1f7a33"
            elif st == "partial":
                text, color = "%s Cookie：缺 sessionid" % name, "#b45309"
            else:
                text, color = "%s Cookie：未登录" % name, "#c0392b"
            try:
                lbl.configure(text=text, foreground=color)
            except Exception:
                pass




def _extract_with_splash():
    """首次启动：准备数据目录（登录信息、下载记录、作品都放这里）"""
    import douk_rt

    win = tk.Tk()
    win.title("正在准备数据目录")
    win.resizable(False, False)
    tk.Label(win, text="首次启动需要准备数据目录，请稍候…",
             font=("Microsoft YaHei UI", 10)).pack(padx=24, pady=(20, 6))
    lab = tk.Label(win, text="", foreground="#666")
    lab.pack(padx=24, pady=(0, 8))
    bar = ttk.Progressbar(win, length=340, mode="determinate")
    bar.pack(padx=24, pady=(0, 20))
    win.update_idletasks()
    x = max(0, (win.winfo_screenwidth() - win.winfo_width()) // 2)
    y = max(0, (win.winfo_screenheight() - win.winfo_height()) // 3)
    win.geometry("+%d+%d" % (x, y))
    win.update()

    def prog(text, done=1, total=1):
        if isinstance(text, (int, float)):
            text, done, total = "正在准备…", int(text), int(total)
        bar["maximum"] = max(total, 1)
        bar["value"] = done
        lab.config(text="%s（%d/%d）" % (text, done, total))
        try:
            win.update()
        except tk.TclError:
            pass

    try:
        saved = core.read_json(CONFIG_FILE, None) or {}
        legacy = str(saved.get("douk_dir") or "") if isinstance(saved, dict) else ""
        if not legacy and os.path.isdir(os.path.join(BASE_DIR, "DouK", "Volume")):
            legacy = os.path.join(BASE_DIR, "DouK")
        lib, data = douk_rt.ensure(custom=str(saved.get("data_dir") or "") if isinstance(saved, dict) else "",
                                   legacy_douk_dir=legacy, progress=prog)
        old_vol = os.path.join(legacy, "Volume") if legacy else ""
        if old_vol and os.path.isdir(old_vol):
            info = douk_rt.migrate_works(old_vol, os.path.join(data, "Volume"), move=True)
            if info.get("moved"):
                log.info("已把 %d 个作品文件夹搬到数据目录", len(info["moved"]))
            if info.get("skipped"):
                log.warning("这些作品文件夹没搬（可能在别的盘或已被占用）：%s",
                            "、".join(info["skipped"][:6]))
        return data
    finally:
        try:
            win.destroy()
        except tk.TclError:
            pass


def _rss_mb():
    """当前进程占用的物理内存（MB），查内存用；拿不到就返回 0"""
    try:
        import ctypes
        import ctypes.wintypes as wt

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.windll.kernel32
        fn = getattr(k32, "K32GetProcessMemoryInfo", None) or ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_ulong]
        fn.restype = ctypes.c_int
        fn(k32.GetCurrentProcess(), ctypes.byref(pmc), ctypes.sizeof(PMC))
        return pmc.WorkingSetSize / 1024 / 1024
    except Exception:  # noqa: BLE001
        return 0.0


def run_meminfo():
    """--meminfo：把启动各阶段的内存占用打到控制台/文件，用于定位内存大头"""
    lines = ["阶段\tRSS(MB)\t模块数"]

    def mark(name):
        lines.append("%s\t%.1f\t%d" % (name, _rss_mb(), len(sys.modules)))

    mark("导入完")
    import tkinter as tk
    mark("导入 tkinter")
    root = tk.Tk()
    mark("建 Tk 根窗口")
    app = App(root)
    root.update()
    mark("建界面")
    if "--meminfo-full" in sys.argv:
        mark("开始跑一轮之前")
        app.monitor_cycle()
        root.update()
        mark("跑完一轮")
        app.save_state()
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(BASE_DIR, "meminfo.log"), "w", encoding="utf-8") as f:
        f.write(text)


def run_selftest():
    """打包后自检：确认内置签名模块可用、能取到作品列表，结果写 selftest.log"""
    out = os.path.join(BASE_DIR, "selftest.log")
    lines = []
    code = 0

    def w(text):
        lines.append(str(text))

    try:
        w("time=%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
        w("frozen=%s base_dir=%s" % (FROZEN, BASE_DIR))
        cfg = dict(DEFAULT_CONFIG)
        saved = core.read_json(CONFIG_FILE, None)
        if isinstance(saved, dict):
            cfg.update(saved)
        cfg["douk_dir"] = str(cfg.get("douk_dir") or "")
        cfg["data_dir"] = str(cfg.get("data_dir") or "")
        lib = core.resolve_lib_dir("")
        data = core.data_root(cfg)
        w("signlib=%s exists=%s" % (lib, os.path.isdir(lib)))
        w("直播录制=内置 FLV 直录，录完自动转 MP4（不需要 ffmpeg）")
        w("data_dir=%s exists=%s" % (data, os.path.isdir(data)))
        dy = core.DouYin("", timeout=int(cfg.get("request_timeout") or 20),
                         data_dir=cfg["data_dir"])
        w("cookie_keys=%d sessionid=%s" % (len(dy.cookie_map),
                                           "yes" if dy.cookie_map.get("sessionid") else "no"))
        try:
            import browser_login
            w("browser=%s" % (browser_login.find_browser() or "none"))
        except Exception as exc:
            w("browser=import_failed:%s" % exc)
        accs = [a for a in (cfg.get("accounts") or []) if a.get("sec_user_id")]
        w("accounts=%d" % len(accs))
        for acc in accs[:2]:
            sec = acc["sec_user_id"]
            prof = dy.fetch_profile(sec)
            posts = dy.fetch_posts(sec, count=3)
            w("account=%s nickname=%s posts=%d live_status=%s" % (
                acc.get("target"), prof.get("nickname"), len(posts), prof.get("live_status")))
        if accs:
            sec = accs[0]["sec_user_id"]
            t0 = time.time()
            page, more, nxt = dy.fetch_posts_page(sec, count=18, cursor="0")
            w("batch_page=%.1fs items=%d more=%s" % (time.time() - t0, len(page), more))
        w("RESULT=OK")
    except Exception as exc:
        code = 1
        w("RESULT=FAIL %s" % exc)
        w(traceback.format_exc())
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass
    sys.exit(code)


def run_liveprobe():
    """--liveprobe：打包版验证「作品接口冷却时直播检测照常」的真实接口链路。

    v1.5.16 把直播检测拆成独立轮询，依据就是：抖音只对
    /aweme/v1/web/aweme/post/ 限流，profile/other 与 webcast/room/enter 正常。
    这里强制把网络层置成冷却态，用真实客户端跑一遍，确认：
      ① profile/other（live_status 来源）能正常返回；
      ② 作品列表请求被冷却闸门拦住、一个包都不发；
      ③ 作品详情 / 直播间入口不受冷却影响。
    结果写 liveprobe.log；不建界面、不改正式配置、不下载任何文件。
    """
    out = os.path.join(BASE_DIR, "liveprobe.log")
    lines = []

    def w(text):
        lines.append(str(text))
        try:
            print(text, flush=True)
        except Exception:
            pass

    try:
        w("time=%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
        w("frozen=%s base_dir=%s" % (FROZEN, BASE_DIR))
        cfg = dict(DEFAULT_CONFIG)
        saved = core.read_json(CONFIG_FILE, None)
        if isinstance(saved, dict):
            cfg.update(saved)
        w("intervals: work=%s live=%s separate=%s live_check=%s" % (
            cfg.get("poll_interval"), cfg.get("live_poll_interval", 300),
            cfg.get("live_separate", True), cfg.get("live_check", True)))
        dy = core.DouYin("",
                         timeout=int(cfg.get("request_timeout") or 20),
                         data_dir=cfg.get("data_dir") or "")
        w("cookie_keys=%d sessionid=%s" % (
            len(dy.cookie_map), "yes" if dy.cookie_map.get("sessionid") else "no"))
        accs = [a for a in (cfg.get("accounts") or []) if a.get("sec_user_id")]
        if not accs:
            w("no_account=skip network part")
            w("RESULT=OK")
            return 0
        sec = accs[0]["sec_user_id"]

        # 强制进入作品列表冷却态（模拟 13:50 那次风控），随后验证分流
        core._RATE_UNTIL = time.time() + 600
        w("forced_cooldown_remaining=%d" % int(core.rate_limit_remaining()))

        # ① 直播状态来源：profile/other 必须正常
        prof = dy.fetch_profile(sec)
        w("profile=OK live_status=%s room_id=%s nickname=%s" % (
            prof.get("live_status"), prof.get("room_id"), prof.get("nickname")))
        ok_profile = isinstance(prof, dict) and "live_status" in prof

        # ② 作品列表必须被冷却闸门拦住（零请求），这是双时钟设计的前提
        try:
            dy.fetch_posts_page(sec, count=3)
            w("post=UNEXPECTED_OK")
            ok_post = False
        except core.RateLimited as exc:
            w("post=BLOCKED_BY_COOLDOWN (%s)" % exc)
            ok_post = True
        except Exception as exc:  # noqa: BLE001
            w("post=OTHER_ERROR %s" % exc)
            ok_post = False

        # ③ 作品详情（单独下载用）不受冷却影响
        try:
            aid = "7554734706693393710"
            d = dy.fetch_detail(aid)
            w("detail=OK has_aweme=%s" % bool(d.get("aweme_id")))
            ok_detail = True
        except Exception as exc:  # noqa: BLE001
            w("detail=SKIP %s" % exc)
            ok_detail = True   # 详情接口偶发 403 与本验证无关，不算失败

        # ④ 直播间入口（不同域名）不受冷却影响
        web_rid = str(prof.get("short_id") or prof.get("unique_id") or "douyin")
        try:
            dy.fetch_live(web_rid)
            w("live_enter=OK web_rid=%s" % web_rid)
            ok_live = True
        except Exception as exc:  # noqa: BLE001
            # 没开播时接口可能返回空 data，只要不是冷却错误就算通
            msg = str(exc)
            w("live_enter=OK_NO_ROOM %s" % msg[:120])
            ok_live = "冷却" not in msg and "RateLimited" not in msg

        w("RESULT=OK" if (ok_profile and ok_post and ok_detail and ok_live)
          else "RESULT=FAIL")
    except Exception as exc:  # noqa: BLE001
        w("RESULT=FAIL %s" % exc)
        w(traceback.format_exc())
    finally:
        core._RATE_UNTIL = 0.0
        core._RATE_STRIKES = 0
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass
    return 0


def main():
    # 高分屏防糊：先声明 DPI 感知，再建任何窗口（Tk 窗口按真实像素渲染）
    hi_dpi.enable()
    # 只允许一个实例：已经在跑就把那个窗口叫到前台，本进程直接退出
    if not ({"--selftest", "--probe", "--meminfo", "--linktest", "--liveprobe"} & set(sys.argv)):
        import single_instance
        if not single_instance.ensure_single(quiet="--minimized" in sys.argv):
            return
    if sys.version_info[:2] != (3, 12):
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, "本客户端需要 Python 3.12（内置签名模块的字节码版本）。\n"
               f"当前版本：{sys.version.split()[0]}\n\n请安装 Python 3.12 后重试。",
            "版本不匹配", 0x10)
        return
    if FROZEN:
        try:
            data_dir = _extract_with_splash()
            log.info("数据目录：%s", data_dir)
            saved = core.read_json(CONFIG_FILE, None)
            if isinstance(saved, dict) and str(saved.get("douk_dir") or "").strip():
                saved["douk_dir"] = ""      # 不再依赖外部 DouK 安装
                core.write_json(CONFIG_FILE, saved)
        except Exception as exc:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, "内置下载器准备失败：\n%s\n\n请重新下载本程序。" % exc,
                "启动错误", 0x10)
            return
    if "--liveprobe" in sys.argv:
        return run_liveprobe()
    if "--meminfo" in sys.argv:
        run_meminfo()
        return
    if "--selftest" in sys.argv:
        run_selftest()
        return
    if "--probe" in sys.argv:
        # 打包后自检：python 解释器不在时用它确认「多平台解析」可用
        try:
            url = sys.argv[sys.argv.index("--probe") + 1]
        except IndexError:
            url = "https://www.bilibili.com/video/BV1GJ411x7h7"
        import link_resolver
        out = ["url=" + url, "platform=%s" % (link_resolver.detect(url),),
               "yt_dlp=%s" % link_resolver.available()]
        try:
            info = link_resolver.probe(url)
            out.append("title=" + info["title"])
            out.append("uploader=" + info["uploader"])
            out.append("RESULT=OK")
        except Exception as exc:  # noqa: BLE001
            out.append("error=" + str(exc))
            out.append("RESULT=FAIL")
        io_path = os.path.join(BASE_DIR, "probe.log")
        with open(io_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        return
    root = tk.Tk()
    try:
        app = App(root)
        if "--minimized" in sys.argv:
            root.after(600, root.iconify)
            if getattr(app, "tray", None):
                log.info("已按 --minimized 参数最小化启动（双击托盘图标可显示主界面）")
    except Exception as exc:  # 启动阶段异常：日志 + 弹窗，避免"双击没反应"
        import ctypes
        import traceback as _tb
        log.error("客户端启动失败：%s", exc)
        log.debug("%s", _tb.format_exc())
        try:
            with open(os.path.join(BASE_DIR, "启动错误.log"), "w", encoding="utf-8") as fh:
                fh.write(_tb.format_exc())
        except Exception:
            pass
        ctypes.windll.user32.MessageBoxW(
            0, "客户端启动失败：\n%s\n\n详细信息见 启动错误.log" % exc,
            "启动错误", 0x10)
        return
    root.mainloop()


if __name__ == "__main__":
    main()
