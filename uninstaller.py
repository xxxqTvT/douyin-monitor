# -*- coding: utf-8 -*-
"""卸载：删快捷方式、注册表项，可选保留 data，最后清掉安装目录。

由 `抖音监控.exe --uninstall` 调用（安装程序会把这条命令写进 Windows 的
「应用和功能」里）。也可以直接给用户双击用。

安全线：只在「程序目录里确实有 抖音监控.exe / _internal」时才动手，
避免误删开发目录。
"""

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time

MB_YESNO = 0x04
MB_ICONQUESTION = 0x20
MB_ICONINFORMATION = 0x40
MB_ICONWARNING = 0x30
IDYES = 6

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINST_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\DouYinMonitor"
SHORTCUT_NAME = "抖音监控.lnk"

EXE_NAME = "抖音监控.exe"


def say(text, title="抖音监控", flags=MB_ICONINFORMATION):
    return ctypes.windll.user32.MessageBoxW(0, text, title, flags)


def ask(text):
    return ctypes.windll.user32.MessageBoxW(0, text, "抖音监控", MB_YESNO | MB_ICONQUESTION) == IDYES


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def looks_installed(path):
    return (os.path.isfile(os.path.join(path, EXE_NAME))
            and os.path.isdir(os.path.join(path, "_internal")))


def shortcut_paths():
    home = os.path.expanduser("~")
    paths = [
        os.path.join(home, "Desktop", SHORTCUT_NAME),
        os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop", SHORTCUT_NAME),
        os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                     "Start Menu", "Programs", SHORTCUT_NAME),
        os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                     "Start Menu", "Programs", "抖音监控", SHORTCUT_NAME),
    ]
    return [p for p in paths if p and os.path.isfile(p)]


def reg_remove():
    removed = []
    try:
        import winreg
        for root, path in ((winreg.HKEY_CURRENT_USER, RUN_KEY),
                           (winreg.HKEY_CURRENT_USER, UNINST_KEY)):
            try:
                with winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE) as k:
                    if root == winreg.HKEY_CURRENT_USER and path == RUN_KEY:
                        try:
                            winreg.DeleteValue(k, "DouYinMonitor")
                            removed.append("开机自启项")
                        except FileNotFoundError:
                            pass
            except FileNotFoundError:
                pass
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINST_KEY)
            removed.append("卸载信息")
        except FileNotFoundError:
            pass
    except Exception:
        pass
    return removed


def kill_running():
    """把其它正在跑的客户端关掉（托盘里的那个也算）。注意排除自己：
    卸载就是由本程序自己执行的，按镜像名杀会把卸载进程一起杀掉。"""
    me = os.getpid()
    ps = ("Get-Process -Name $n -ErrorAction SilentlyContinue | "
          "Where-Object { $_.Id -ne %d } | ForEach-Object { $_.Id }" % me)
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$n = [IO.Path]::GetFileNameWithoutExtension('%s'); " % EXE_NAME + ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=0x08000000, timeout=30).stdout
        pids = [x.strip() for x in out.splitlines() if x.strip().isdigit()]
        for pid in pids:
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True,
                           creationflags=0x08000000, timeout=15)
        if pids:
            time.sleep(0.8)
    except Exception:
        pass


def schedule_delete(path):
    """自己删不了自己：交给一个分离的 cmd，等 2 秒后删目录"""
    bat = os.path.join(tempfile.gettempdir(), "_uninstall_douyinmonitor.cmd")
    with open(bat, "w", encoding="gbk", errors="replace") as f:
        f.write("@echo off\r\n")
        f.write("timeout /t 2 /nobreak >nul\r\n")
        f.write('rmdir /s /q "%s"\r\n' % path)
        f.write('del /q "%%~f0"\r\n')
    subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000, close_fds=True)


def run(silent=False):
    path = base_dir()
    if not looks_installed(path):
        if not silent:
            say("这里看起来不是安装目录：\n%s\n\n没有找到 %s 和 _internal，"
                "为安全起见不做任何改动。" % (path, EXE_NAME), "卸载", MB_ICONWARNING)
        return 1

    if not silent:
        if not ask("要卸载「抖音监控」吗？\n\n安装目录：%s\n\n"
                   "点「是」开始卸载，点「否」取消。" % path):
            return 0

    data_dir = os.path.join(path, "data")
    keep = False
    if silent:
        keep = os.path.isdir(data_dir)      # 静默卸载默认保留数据，搬到旁边去
    elif os.path.isdir(data_dir):
        keep = ask("要把 data 文件夹（登录信息、下载记录、作品、直播录像）留下来吗？\n\n"
                   "点「是」= 保留：会搬到安装目录旁边的「抖音监控数据」文件夹；\n"
                   "点「否」= 一起删掉。")

    kill_running()
    gone = reg_remove()
    lnks = shortcut_paths()
    for p in lnks:
        try:
            os.remove(p)
        except OSError:
            pass
    parent = os.path.dirname(path.rstrip("\\/")) or path
    moved_to = ""
    if keep:
        target = os.path.join(parent, "抖音监控数据")
        n = 1
        while os.path.isdir(target):
            n += 1
            target = os.path.join(parent, "抖音监控数据%d" % n)
        try:
            shutil.move(data_dir, target)
            moved_to = target
        except Exception:
            keep = False

    schedule_delete(path)
    if silent:
        print("uninstalled=%s keep_data=%s data=%s" % (path, keep, moved_to))
        return 0

    msg = "已卸载。\n\n删除目录：%s" % path
    if moved_to:
        msg += "\n数据已保留在：%s" % moved_to
    if gone:
        msg += "\n已清理：" + "、".join(gone)
    if lnks:
        msg += "\n已删除 %d 个快捷方式" % len(lnks)
    say(msg, "卸载完成")
    return 0


if __name__ == "__main__":
    sys.exit(run(silent="--silent" in sys.argv))