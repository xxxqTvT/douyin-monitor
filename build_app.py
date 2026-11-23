# -*- coding: utf-8 -*-
r"""
打包脚本：把「抖音账号监控客户端」打成一个可直接运行的成品。

用法：
    python build_app.py            # 文件夹版（默认，推荐）：dist\抖音监控\
    python build_app.py --onefile  # 单文件版（备选）：dist\抖音监控.exe
    python build_app.py --console  # 带黑窗，便于看报错
    python build_app.py --keep     # 保留 _build 中间文件

产物：
    dist\抖音监控\抖音监控.exe  ← 双击即用

依赖：只有 PyInstaller。签名算法在 signlib/（纯 Python，随包带走），
不再需要外部 DouK 安装目录、douk_lib 缓存、curl_cffi 或 ffmpeg。
"""

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(ROOT, "_build")
DIST = os.path.join(ROOT, "dist")
APP_NAME = "DouyinMonitor"
FINAL_NAME = "抖音监控.exe"
SIGNLIB = os.path.join(ROOT, "signlib")

# 打包时剔除用不到的模块（减小体积；漏剔了也只是变大，不会出错）
EXCLUDE_MODULES = (
    "numpy", "pandas", "matplotlib", "PIL", "scipy",
    "setuptools", "pkg_resources", "pip", "wheel", "distutils",
    "lib2to3", "idlelib", "unittest", "pydoc_data", "pydoc",
    "tkinter.test", "test", "xmlrpc", "pytest", "IPython",
)


def log(msg):
    print("[build] %s" % msg, flush=True)


# 这些标准库模块在运行期会被间接用到，PyInstaller 静态分析看不到
COLLECT_SUBMODULES = ["http", "email", "xml", "urllib", "html", "json", "logging",
                      "csv", "concurrent", "asyncio", "importlib", "encodings"]


def collect_args():
    args = []
    for name in COLLECT_SUBMODULES:
        args += ["--collect-submodules", name]
    for name in sorted(sys.stdlib_module_names):
        if name.startswith("_") and name not in ("_ssl", "_socket", "_sqlite3", "_ctypes"):
            continue
        args += ["--hidden-import", name]
    return args


EXCLUDES = []
for _m in EXCLUDE_MODULES:
    EXCLUDES.extend(("--exclude-module", _m))


def run_pyinstaller():
    onedir = "--onefile" not in sys.argv
    log("PyInstaller 打包本程序（%s）…" % ("文件夹版" if onedir else "单文件版"))
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        ("--onedir" if onedir else "--onefile"),
        ("--console" if "--console" in sys.argv else "--windowed"),
        "--name", APP_NAME,
        "--optimize", "2",          # 去掉 docstring/assert，字节码更小
        *EXCLUDES,
        "--hidden-import", "browser_login",
        "--hidden-import", "signlib",
        "--hidden-import", "live_remux",
    "--hidden-import", "link_resolver",
    "--hidden-import", "douk_dl",
    "--hidden-import", "douk_rt",
        "--hidden-import", "hi_dpi",
        # 签名模块是 .pyc（字节码，和打包用的 Python 3.12 一致），按数据文件原样带上
        "--add-data", SIGNLIB + os.pathsep + "signlib",
        *collect_args(),
        "--distpath", DIST,
        "--workpath", os.path.join(BUILD, "pyi"),
        "--specpath", BUILD,
        os.path.join(ROOT, "douyin_client.py"),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)
    if onedir:
        exe = os.path.join(DIST, APP_NAME, APP_NAME + ".exe")
    else:
        exe = os.path.join(DIST, APP_NAME + ".exe")
    if not os.path.isfile(exe):
        raise SystemExit("PyInstaller 没有生成 exe：%s" % exe)
    log("PyInstaller 完成：%.1f MB" % (os.path.getsize(exe) / 1024 / 1024))
    return exe


def finalize(exe):
    if "--onefile" not in sys.argv:
        log("整理文件夹版成品…")
        folder = os.path.join(DIST, "抖音监控")
        if os.path.isdir(folder):
            shutil.rmtree(folder)
        os.rename(os.path.dirname(exe), folder)
        final = os.path.join(folder, FINAL_NAME)
        os.rename(os.path.join(folder, os.path.basename(exe)), final)
        total = 0
        for root, _dirs, files in os.walk(folder):
            total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
        log("文件夹版成品：%s（%.1f MB）" % (folder, total / 1024 / 1024))
        return final
    log("整理单文件成品…")
    final = os.path.join(DIST, FINAL_NAME)
    shutil.copy2(exe, final)
    log("单文件成品：%s（%.1f MB）" % (final, os.path.getsize(final) / 1024 / 1024))
    return final


def cleanup(final):
    for path in (os.path.join(DIST, APP_NAME + ".exe"),):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


def main():
    t0 = time.time()
    exe = run_pyinstaller()
    final = finalize(exe)
    if "--keep" not in sys.argv:
        cleanup(final)
    log("完成，用时 %.1f 秒" % (time.time() - t0))


if __name__ == "__main__":
    main()