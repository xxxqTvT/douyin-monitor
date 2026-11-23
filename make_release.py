# -*- coding: utf-8 -*-
r"""打发布包：便携版 zip + 安装程序 exe

用法：
    python make_release.py            # 生成 发布\ 下的 zip（并准备安装程序的 payload）
    python make_release.py --installer  # 连安装程序一起编译（需要 PyInstaller）
"""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist", "抖音监控")
OUT_ROOT = r"D:\dshzijian\发布"
VERSION = "1.7.38"
APP_EXE = "抖音监控.exe"

# 绝对不能进包的东西（都是本地个人数据）
DENY_DIRS = {"data", "Volume", "直播地址", "直播录像", "__pycache__"}
DENY_FILES = {"client_config.json", "client_state.json", "config.json", "state.json",
              "monitor.log", "selftest.log", "download_data"}

README = r"D:\dshzijian\README.md"
PREVIEWS = ["预览_布局.png", "预览_直播录制面板.png", "预览_本次下载.png", "预览_深色界面.png"]


def log(msg):
    print("[release] %s" % msg, flush=True)


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0


QUICKSTART = """抖音监控 v%(ver)s —— 使用说明
==========================================

一、免安装（绿色版）
   1. 把整个「抖音监控」文件夹放到任意位置（建议 D:\\抖音监控）；
   2. 双击「抖音监控.exe」；
   3. 第一次启动会让你选平台（抖音 / TikTok），然后自动打开浏览器扫码登录；
   4. 点「添加账号」，填抖音号 / 主页链接 / 整段分享文案都行；
   5. 点「▶ 开始监控」就不用管了。

二、安装版
   运行「抖音监控_v%(ver)s_安装程序.exe」，选好目录一路下一步即可。
   安装完成会自动建桌面和开始菜单快捷方式，可在「设置」里开/关开机自启。

三、装在哪儿、数据在哪儿
   程序：安装目录（免安装版就是解压出来的文件夹）
   数据：安装目录\\data\\（程序目录不可写时自动改用
         %%LOCALAPPDATA%%\\DouYinMonitor\\data\\）
   里面包含：登录 Cookie、下载记录、按账号存放的作品、直播录像。
   换电脑或备份时，整个 data 文件夹拷走就行。

四、常用功能
   - 单独下载链接：支持抖音 / TikTok / B 站 / 微博 / 小红书 / YouTube / X 等
     （快手暂不支持），也支持任意 mp4 直链。抖音 App 复制出来的
     v.douyin.com 短链会自动展开，粘贴整段分享文案也能识别。
   - 直播录制：检测到开播自动录成 FLV 回放（程序自己录，不用 ffmpeg），
     右栏置顶显示时长 / 画质 / 大小；关软件时会先把录像保存好再退出。
     每个账号可以单独开关录制。
   - 两个检测间隔可分别设置：控制区「作品检测间隔」管翻主页找新作品，
     「直播检测间隔」管查开播；勾「独立轮询」后两者各按自己的秒数跑，
     作品接口被限流冷却时开播检测也照常（默认都是 300 秒）。
   - 托盘 / 通知：关闭窗口不退出，缩到托盘继续监控；有新作品或开播会弹通知。
   - 静默模式：所有弹窗都不弹，只写日志。

五、需要知道的
   - 需要 Windows 10/11 64 位；不需要装 Python、不需要装 DouK，也不需要装 ffmpeg。
   - TikTok 需要本机能访问 tiktok.com，否则在「设置 → Cookie → TikTok 代理」里填代理。
   - 只允许开一个实例：重复启动会把已有窗口叫到前台。
   - 日志：安装目录\\monitor.log（超过 2 MB 自动轮转）。

六、卸载
   「设置 → 控制面板 → 应用」里卸载，或直接运行安装目录下的
   「卸载抖音监控.exe」。卸载时问你要不要保留 data（作品和登录信息）。

版本：v%(ver)s    打包时间：%(date)s
""" 

MANIFEST_EXTRA = """版本号：v%(ver)s
打包时间：%(date)s
适用系统：Windows 10 / 11 64 位
内容：
  抖音监控.exe          主程序（8.4 MB）
  _internal\\            运行库 + 内置签名模块 signlib（纯 Python）
  README.md             完整文档
  使用说明.txt           这份快速上手
  预览_新界面.png        界面截图
  预览_直播录制面板.png   直播录制面板截图
预览_本次下载.png       本次下载面板截图
说明：
  首次运行会在程序目录创建 data\\（登录信息、下载记录、作品、直播录像）。
  不需要管理员权限；不需要安装 Python、DouK 或 ffmpeg（全部内置，完全自包含）。
"""


def stage():
    if not os.path.isdir(DIST):
        raise SystemExit("找不到打包产物：%s\n先跑 python build_app.py" % DIST)
    stage_dir = os.path.join(OUT_ROOT, "抖音监控")
    if os.path.isdir(stage_dir):
        shutil.rmtree(stage_dir)
    os.makedirs(stage_dir)

    # 1) 主程序 + 运行库
    shutil.copy2(os.path.join(DIST, APP_EXE), os.path.join(stage_dir, APP_EXE))
    shutil.copytree(os.path.join(DIST, "_internal"), os.path.join(stage_dir, "_internal"))

    # 2) 文档
    if os.path.isfile(README):
        shutil.copy2(README, os.path.join(stage_dir, "README.md"))
    date = time.strftime("%Y-%m-%d %H:%M")
    io.open(os.path.join(stage_dir, "使用说明.txt"), "w", encoding="utf-8").write(
        QUICKSTART % {"ver": VERSION, "date": date})
    io.open(os.path.join(stage_dir, "发布说明.txt"), "w", encoding="utf-8").write(
        MANIFEST_EXTRA % {"ver": VERSION, "date": date})

    # 3) 截图
    src_dir = os.path.join(ROOT)
    for name in PREVIEWS:
        p = os.path.join(src_dir, name)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(stage_dir, name))

    # 4) 自检：确认没有夹带个人数据
    bad = []
    for base, dirs, files in os.walk(stage_dir):
        for d in list(dirs):
            if d in DENY_DIRS:
                bad.append(os.path.join(base, d))
                dirs.remove(d)
        for f in files:
            if f in DENY_FILES:
                bad.append(os.path.join(base, f))
    if bad:
        raise SystemExit("发布目录里混进了个人数据：\n%s" % "\n".join(bad))

    total = 0
    count = 0
    for base, _d, files in os.walk(stage_dir):
        for f in files:
            total += os.path.getsize(os.path.join(base, f))
            count += 1
    log("发布目录就绪：%d 个文件，%s" % (count, human(total)))
    return stage_dir


def make_zip(stage_dir):
    zip_path = os.path.join(OUT_ROOT, "抖音监控_v%s_免安装版.zip" % VERSION)
    if os.path.exists(zip_path):
        os.remove(zip_path)
    t0 = time.time()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for base, _dirs, files in os.walk(stage_dir):
            for f in files:
                full = os.path.join(base, f)
                rel = os.path.relpath(full, os.path.dirname(stage_dir))
                z.write(full, rel)
    log("免安装包：%s（%s，用时 %.1fs）" % (zip_path, human(os.path.getsize(zip_path)),
                                            time.time() - t0))
    return zip_path


def build_installer(zip_path):
    """把 payload.zip 打进安装程序"""
    payload = os.path.join(ROOT, "_setup", "payload.zip")
    os.makedirs(os.path.dirname(payload), exist_ok=True)
    shutil.copy2(zip_path, payload)
    exe_name = os.path.join(OUT_ROOT, "抖音监控_v%s_安装程序.exe" % VERSION)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "抖音监控安装程序",
        "--add-data", "%s;." % payload,
        "--distpath", os.path.join(ROOT, "_setup", "dist"),
        "--workpath", os.path.join(ROOT, "_setup", "build"),
        "--specpath", os.path.join(ROOT, "_setup"),
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.ttk",
        "--hidden-import", "tkinter.filedialog",
        "--hidden-import", "tkinter.messagebox",
        os.path.join(ROOT, "installer_app.py"),
    ]
    log("编译安装程序…")
    subprocess.run(cmd, check=True)
    built = os.path.join(ROOT, "_setup", "dist", "抖音监控安装程序.exe")
    if os.path.exists(exe_name):
        os.remove(exe_name)
    shutil.copy2(built, exe_name)
    log("安装程序：%s（%s）" % (exe_name, human(os.path.getsize(exe_name))))
    return exe_name


def main():
    os.makedirs(OUT_ROOT, exist_ok=True)
    stage_dir = stage()
    zip_path = make_zip(stage_dir)
    result = {"version": VERSION, "zip": zip_path, "zip_size": os.path.getsize(zip_path),
              "zip_sha256": sha256(zip_path)}
    if "--installer" in sys.argv:
        exe = build_installer(zip_path)
        result["installer"] = exe
        result["installer_size"] = os.path.getsize(exe)
        result["installer_sha256"] = sha256(exe)
        shutil.rmtree(os.path.join(ROOT, "_setup"), ignore_errors=True)
    io.open(os.path.join(OUT_ROOT, "发布信息.json"), "w", encoding="utf-8").write(
        json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
