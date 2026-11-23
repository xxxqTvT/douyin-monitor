# -*- coding: utf-8 -*-
"""抖音监控 · 安装程序

模式：
    抖音监控_v1.0.0_安装程序.exe                  → 图形界面安装
    抖音监控_v1.0.0_安装程序.exe --silent --dir X  → 静默安装到 X
    抖音监控_v1.0.0_安装程序.exe --uninstall --dir X → 静默卸载
"""

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import zipfile

APP_EXE = "抖音监控.exe"
APP_NAME = "抖音监控"
VERSION = "1.7.48"
UNINST_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\DouYinMonitor"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
CREATE_NO_WINDOW = 0x08000000

BG = "#eef1f6"
CARD = "#ffffff"
BORDER = "#dfe4ec"
TEXT = "#1f2430"
MUTED = "#6b7280"
ACCENT = "#2f6fed"
HEADER = "#1b2437"


def payload_path():
    if getattr(sys, "_MEIPASS", None):
        for name in ("payload.zip", "抖音监控_v%s_免安装版.zip" % VERSION):
            p = os.path.join(sys._MEIPASS, name)
            if os.path.isfile(p):
                return p
    here = os.path.dirname(os.path.abspath(__file__))
    for name in sorted(os.listdir(here)):
        if name.lower().endswith(".zip"):
            return os.path.join(here, name)
    raise SystemExit("安装包里缺少 payload.zip")


def default_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Programs", APP_NAME)


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d B" % n) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def target_exe(path):
    return os.path.join(path, APP_EXE)


# ----------------------------- 安装动作 -----------------------------

def extract(zip_path, dest, progress=None):
    """zip 里第一层是「抖音监控/」，装的时候剥掉"""
    with zipfile.ZipFile(zip_path) as z:
        infos = z.infolist()
        total = len(infos)
        for i, info in enumerate(infos, 1):
            rel = info.filename
            parts = rel.split("/", 1)
            if len(parts) == 2 and parts[0] == APP_NAME:
                rel = parts[1]
            elif parts[0] == APP_NAME:
                continue
            if not rel or rel.endswith("/"):
                continue
            out = os.path.join(dest, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            if progress and i % 20 == 0:
                progress(i, total)


def ps(cmd):
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                   capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=60)


def make_shortcut(lnk, target, workdir, icon=None):
    t = target.replace("'", "''")
    w = workdir.replace("'", "''")
    ic = (icon or target).replace("'", "''")
    l = lnk.replace("'", "''")
    ps("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
       "$s.TargetPath='%s';$s.WorkingDirectory='%s';$s.IconLocation='%s,0';"
       "$s.Description='抖音账号监控客户端';$s.Save()" % (l, t, w, ic))


def shortcuts(path, desktop=True, startmenu=True):
    exe = target_exe(path)
    made = []
    home = os.path.expanduser("~")
    if desktop:
        p = os.path.join(home, "Desktop", APP_NAME + ".lnk")
        make_shortcut(p, exe, path)
        made.append(p)
    if startmenu:
        folder = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                              "Start Menu", "Programs", APP_NAME)
        os.makedirs(folder, exist_ok=True)
        p = os.path.join(folder, APP_NAME + ".lnk")
        make_shortcut(p, exe, path)
        made.append(p)
        u = os.path.join(folder, "卸载" + APP_NAME + ".lnk")
        make_shortcut(u, exe, path)
        made.append(u)
    return made


def write_registry(path, autostart=False):
    import winreg
    exe = target_exe(path)
    size_kb = 0
    for base, _d, files in os.walk(path):
        for f in files:
            try:
                size_kb += os.path.getsize(os.path.join(base, f)) // 1024
            except OSError:
                pass
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINST_KEY)
    vals = {
        "DisplayName": "抖音监控（抖音 / TikTok 账号监控客户端）",
        "DisplayVersion": VERSION,
        "Publisher": "本地自用工具",
        "InstallLocation": path,
        "DisplayIcon": exe,
        "UninstallString": '"%s" --uninstall' % exe,
        "QuietUninstallString": '"%s" --uninstall --silent' % exe,
        "NoModify": 1,
        "NoRepair": 1,
        "EstimatedSize": size_kb,
        "InstallDate": time.strftime("%Y%m%d"),
    }
    for k, v in vals.items():
        winreg.SetValueEx(key, k, 0, winreg.REG_SZ if isinstance(v, str) else winreg.REG_DWORD, v)
    winreg.CloseKey(key)
    set_autostart(path, autostart)


def set_autostart(path, on):
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
    try:
        if on:
            winreg.SetValueEx(key, "DouYinMonitor", 0, winreg.REG_SZ,
                              '"%s" --minimized' % target_exe(path))
        else:
            try:
                winreg.DeleteValue(key, "DouYinMonitor")
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)


def kill_running():
    subprocess.run(["taskkill", "/IM", APP_EXE, "/F"], capture_output=True,
                   creationflags=CREATE_NO_WINDOW)


def install(path, desktop=True, startmenu=True, autostart=False, progress=None, log=None):
    zip_path = payload_path()
    if os.path.isdir(path) and os.listdir(path):
        # 覆盖安装：先清掉旧程序文件，保留 data
        for name in os.listdir(path):
            if name in ("data",):
                continue
            full = os.path.join(path, name)
            if os.path.isdir(full):
                shutil.rmtree(full, ignore_errors=True)
            else:
                try:
                    os.remove(full)
                except OSError:
                    pass
    os.makedirs(path, exist_ok=True)
    if log:
        log("正在解压程序文件…")
    extract(zip_path, path, progress)
    if log:
        log("正在创建快捷方式…")
    shortcuts(path, desktop=desktop, startmenu=startmenu)
    if log:
        log("正在写入卸载信息…")
    write_registry(path, autostart=autostart)
    return target_exe(path)


def uninstall(path):
    exe = target_exe(path)
    if os.path.isfile(exe):
        subprocess.run([exe, "--uninstall", "--silent"], capture_output=True,
                       creationflags=CREATE_NO_WINDOW, timeout=120)
        return True
    kill_running()
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINST_KEY)
    except Exception:
        pass
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    return True


# ----------------------------- 图形界面 -----------------------------

def gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    root = tk.Tk()
    root.title("抖音监控 v%s 安装程序" % VERSION)
    root.configure(bg=BG)
    root.geometry("620x420")
    root.resizable(False, False)
    try:
        root.iconbitmap(default="")
    except Exception:
        pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("TLabel", background=BG, foreground=TEXT,
                    font=("Microsoft YaHei UI", 9))
    style.configure("Title.TLabel", background=HEADER, foreground="#ffffff",
                    font=("Microsoft YaHei UI", 13, "bold"))
    style.configure("Sub.TLabel", background=HEADER, foreground="#9fb0c9",
                    font=("Microsoft YaHei UI", 9))
    style.configure("Hint.TLabel", background=BG, foreground=MUTED,
                    font=("Microsoft YaHei UI", 8))
    style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"),
                    padding=(18, 7), background=ACCENT, foreground="#ffffff",
                    borderwidth=0)
    style.map("Primary.TButton",
              background=[("active", "#1f57c9"), ("disabled", "#9db8f6")])
    style.configure("TButton", font=("Microsoft YaHei UI", 9), padding=(10, 4))
    style.configure("TEntry", padding=4)
    style.configure("TCheckbutton", background=BG, foreground=TEXT,
                    font=("Microsoft YaHei UI", 9))
    style.configure("TProgressbar", background=ACCENT, troughcolor="#dfe4ec",
                    borderwidth=0, thickness=8)

    head = tk.Frame(root, bg=HEADER, height=76)
    head.pack(fill="x")
    head.pack_propagate(False)
    tk.Label(head, text="抖音监控  安装程序", bg=HEADER, fg="#ffffff",
             font=("Microsoft YaHei UI", 13, "bold")).place(x=22, y=16)
    tk.Label(head, text="抖音 / TikTok 账号监控 · 自动下载 · 直播录制      v%s" % VERSION,
             bg=HEADER, fg="#9fb0c9", font=("Microsoft YaHei UI", 9)).place(x=24, y=42)

    body = tk.Frame(root, bg=BG)
    body.pack(fill="both", expand=True, padx=22, pady=16)

    tk.Label(body, text="安装到", bg=BG, fg=TEXT,
             font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, sticky="w")
    dir_var = tk.StringVar(value=default_dir())
    entry = ttk.Entry(body, textvariable=dir_var, width=58)
    entry.grid(row=1, column=0, sticky="we", pady=(4, 0))

    def browse():
        d = filedialog.askdirectory(title="选择安装目录", initialdir=os.path.dirname(dir_var.get()))
        if d:
            dir_var.set(os.path.join(d.replace("/", os.sep), APP_NAME)
                        if os.path.basename(d.rstrip("\\/")) != APP_NAME else d)

    ttk.Button(body, text="浏览…", command=browse).grid(row=1, column=1, padx=(8, 0), pady=(4, 0))

    desktop_var = tk.BooleanVar(value=True)
    start_var = tk.BooleanVar(value=True)
    auto_var = tk.BooleanVar(value=False)
    opts = tk.Frame(body, bg=BG)
    opts.grid(row=2, column=0, columnspan=2, sticky="w", pady=(14, 0))
    ttk.Checkbutton(opts, text="创建桌面快捷方式", variable=desktop_var).pack(side="left")
    ttk.Checkbutton(opts, text="创建开始菜单快捷方式", variable=start_var).pack(side="left", padx=(16, 0))
    ttk.Checkbutton(opts, text="开机自动启动（最小化到托盘）", variable=auto_var).pack(side="left", padx=(16, 0))

    info = tk.Label(body, text="不需要管理员权限；登录信息与下载内容都放在安装目录的 data 文件夹里。",
                    bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8), justify="left")
    info.grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))

    bar = ttk.Progressbar(body, mode="determinate", maximum=100)
    bar.grid(row=4, column=0, columnspan=2, sticky="we", pady=(16, 4))
    status = tk.Label(body, text="准备就绪", bg=BG, fg=MUTED,
                      font=("Microsoft YaHei UI", 8), anchor="w")
    status.grid(row=5, column=0, columnspan=2, sticky="we")

    btns = tk.Frame(root, bg=BG)
    btns.pack(fill="x", padx=22, pady=(0, 18))
    install_btn = ttk.Button(btns, text="开始安装", style="Primary.TButton")
    install_btn.pack(side="right")

    def open_dir_btn(parent):
        b = ttk.Button(parent, text="打开安装目录", state="disabled")
        b.pack(side="right", padx=(0, 10))
        return b

    open_btn = open_dir_btn(btns)

    q = queue.Queue()

    def work(path):
        try:
            def prog(i, total):
                q.put(("p", int(i * 100 / max(total, 1))))
            install(path, desktop=desktop_var.get(), startmenu=start_var.get(),
                    autostart=auto_var.get(), progress=prog,
                    log=lambda m: q.put(("t", m)))
            q.put(("done", path))
        except Exception as exc:
            q.put(("err", str(exc)))

    def poll():
        try:
            while True:
                kind, val = q.get_nowait()
                if kind == "p":
                    bar["value"] = val
                elif kind == "t":
                    status.config(text=val)
                elif kind == "err":
                    status.config(text="安装失败")
                    install_btn.config(state="normal", text="重试")
                    messagebox.showerror("安装失败", val)
                elif kind == "done":
                    status.config(text="安装完成：%s" % val)
                    bar["value"] = 100
                    install_btn.config(text="完成", state="normal",
                                       command=lambda: (root.destroy(), launch(val)))
                    open_btn.config(state="normal",
                                    command=lambda: os.startfile(val))
                    messagebox.showinfo("安装完成",
                                        "抖音监控 v%s 已安装到：\n%s\n\n"
                                        "桌面和开始菜单都有快捷方式，双击即可启动。\n"
                                        "第一次启动会自动打开浏览器让你扫码登录。"
                                        % (VERSION, val))
        except queue.Empty:
            pass
        root.after(120, poll)

    def start():
        path = dir_var.get().strip().strip('"')
        if not path:
            messagebox.showwarning("请选择目录", "先选一个安装目录。")
            return
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".write_test")
            with open(probe, "w") as f:
                f.write("x")
            os.remove(probe)
        except Exception as exc:
            messagebox.showerror("目录不可写", "装不到这个目录：\n%s\n\n%s" % (path, exc))
            return
        kill_running()
        install_btn.config(state="disabled", text="安装中…")
        status.config(text="正在解压程序文件…")
        threading.Thread(target=work, args=(path,), daemon=True).start()

    install_btn.config(command=start)
    root.after(120, poll)

    # 居中
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry("+%d+%d" % ((sw - w) // 2, max(40, (sh - h) // 3)))
    root.mainloop()
    return 0


def launch(path):
    exe = target_exe(path)
    if os.path.isfile(exe):
        subprocess.Popen([exe], cwd=path, creationflags=CREATE_NO_WINDOW)


def main():
    argv = sys.argv[1:]
    if "--uninstall" in argv:
        path = default_dir()
        if "--dir" in argv:
            path = argv[argv.index("--dir") + 1]
        print("uninstall", uninstall(path))
        return 0
    if "--silent" in argv:
        path = default_dir()
        if "--dir" in argv:
            path = argv[argv.index("--dir") + 1]
        exe = install(path, desktop="--no-desktop" not in argv,
                      startmenu="--no-startmenu" not in argv,
                      autostart="--autostart" in argv,
                      log=lambda m: print("[setup] " + m, flush=True))
        print("installed=%s" % exe)
        return 0
    return gui()


if __name__ == "__main__":
    sys.exit(main())
