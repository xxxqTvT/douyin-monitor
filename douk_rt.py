"""用户数据目录与内置签名模块（signlib）的定位。

登录信息、下载记录、作品、录像都放在独立的「数据目录」里；签名算法在
signlib/（纯 Python，随程序分发），不再依赖 DouK 安装目录、douk_min 缓存
或 curl_cffi。

数据目录的选择顺序（第一个可写者胜出）：
  1. 环境变量 DOUK_DATA（调试用）
  2. 程序目录\\data（绿色版：整个文件夹拷走就能用）
  3. %LOCALAPPDATA%\\DouYinMonitor\\data（程序目录不可写时，例如装在 Program Files）
"""

import json
import logging
import os
import shutil
import sys

log = logging.getLogger("monitor")

APP_NAME = "DouYinMonitor"
LIB_DIR_NAME = "signlib"
DATA_DIR_NAME = "data"

_frozen = bool(getattr(sys, "frozen", False))


# --------------------------------------------------------------------------
# 基础路径
# --------------------------------------------------------------------------
def app_dir():
    """程序所在目录（打包版是 exe 目录，源码运行是脚本目录）。"""
    if _frozen:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _meipass():
    """PyInstaller 解包目录（源码运行时为空字符串）。"""
    return str(getattr(sys, "_MEIPASS", "") or "")


# --------------------------------------------------------------------------
# 内置签名模块（signlib/，随程序分发，不再需要 douk_min 缓存）
# --------------------------------------------------------------------------
def lib_candidates(douk_dir=""):
    """兼容旧调用：返回内置签名模块目录。"""
    return [signlib_dir()]


def signlib_dir():
    """signlib 包所在目录（源码运行是 源代码/signlib，打包后是 _MEIPASS/signlib）。"""
    base = _meipass() or app_dir()
    return os.path.join(base, "signlib")


def lib_dir(douk_dir=""):
    """返回内置签名模块目录；正常情况永远存在。"""
    path = signlib_dir()
    return path if os.path.isdir(os.path.join(path, "enc")) else ""


# --------------------------------------------------------------------------
# 数据目录
# --------------------------------------------------------------------------
def _writable(path):
    """能不能在 path 里创建/写入文件。

    探测文件名必须唯一（进程 + 线程 + 随机串）：早期版本用固定的 `.write_test`，
    监控线程和核账线程同时探测时会把对方刚建的文件删掉，导致程序目录偶发被判
    「不可写」而回退到 %LOCALAPPDATA%，后续读写就落到另一个数据目录（下载记录读成 0 条）。
    """
    try:
        import threading
        import uuid
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test_%d_%d_%s" % (
            os.getpid(), threading.get_ident(), uuid.uuid4().hex[:8]))
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:  # noqa: BLE001
        return False


def data_candidates(custom=""):
    out = []
    if custom:
        out.append(str(custom))
    env = os.environ.get("DOUK_DATA", "").strip()
    if env:
        out.append(env)
    out.append(os.path.join(app_dir(), DATA_DIR_NAME))
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    out.append(os.path.join(base, APP_NAME, DATA_DIR_NAME))
    return out


_DIR_CACHE = {}
_DIR_LOCK = None


def data_dir(custom=""):
    """数据目录（登录信息 / 下载记录 / 作品 / 录像都在这下面）。

    结果按 custom 缓存：一旦选定就固定下来，避免中途因一次探测失败换到另一个目录，
    造成「记录写在 A、检查读 B」。
    """
    global _DIR_LOCK
    import threading
    if _DIR_LOCK is None:
        _DIR_LOCK = threading.Lock()
    key = str(custom or "")
    with _DIR_LOCK:
        if key in _DIR_CACHE:
            return _DIR_CACHE[key]
        chosen = ""
        for path in data_candidates(custom):
            if path and _writable(path):
                chosen = os.path.abspath(path)
                break
        if not chosen:
            fallback = data_candidates()[-1]
            os.makedirs(fallback, exist_ok=True)
            chosen = fallback
        _DIR_CACHE[key] = chosen
        return chosen


def volume_dir(custom=""):
    return os.path.join(data_dir(custom), "Volume")


def settings_path(custom=""):
    """配置文件（沿用 DouK 的 settings.json 结构，方便迁移旧登录信息）。"""
    return os.path.join(volume_dir(custom), "settings.json")


def download_dir(custom=""):
    return os.path.join(volume_dir(custom), "Download")


# --------------------------------------------------------------------------
# 配置文件
# --------------------------------------------------------------------------
MINIMAL_SETTINGS = {
    "cookie": {},
    "cookie_tiktok": "",
    "proxy_tiktok": "",
    "browser_info_tiktok": {},
    "root": "",
    "folder_name": "Download",
    "name_format": "发布时间_作品类型_作者昵称_作品描述",
    "name_length": 128,
    "download": False,
    "max_size": 0,
    "chunk": 1048576,
    "timeout": 10,
    "max_retry": 3,
    "proxy": "",
    "run_command": "",
    "download_record": True,
    "user_agent": "",
    "twitch": False,
    "tiktok": False,
    "douyin": True,
    "log": False,
    "script_server": False,
    "health_check": False,
    "language": "zh_CN",
}


def _write_settings(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _merge_legacy_settings(target, legacy_dirs):
    """把旧 DouK 目录里的 settings.json（Cookie 等）合并进来。"""
    merged = dict(MINIMAL_SETTINGS)
    for legacy in legacy_dirs:
        if not legacy:
            continue
        cand = [os.path.join(legacy, "Volume", "settings.json"),
                os.path.join(legacy, "settings.json")]
        for path in cand:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    old = json.load(f)
            except Exception:  # noqa: BLE001
                continue
            for key, value in old.items():
                if key == "cookie" and isinstance(value, dict):
                    base = dict(merged.get("cookie") or {})
                    base.update({k: v for k, v in value.items() if v not in (None, "")})
                    merged["cookie"] = base
                elif value not in (None, "", {}, []):
                    merged[key] = value
            log.info("已从旧配置导入登录信息：%s", path)
            break
    return merged


# --------------------------------------------------------------------------
# 初始化
# --------------------------------------------------------------------------
def ensure(custom="", legacy_douk_dir="", progress=None):
    """准备数据目录与配置文件，返回 (signlib_dir, data_dir)。"""
    if progress:
        progress("准备数据目录…")
    data = data_dir(custom)
    vol = os.path.join(data, "Volume")
    os.makedirs(os.path.join(vol, "Download"), exist_ok=True)

    path = os.path.join(vol, "settings.json")
    if not os.path.isfile(path):
        legacy = [legacy_douk_dir, os.path.join(app_dir(), "DouK")] if legacy_douk_dir else \
                 [os.path.join(app_dir(), "DouK")]
        merged = _merge_legacy_settings(path, legacy)
        _write_settings(path, merged)
        log.info("已创建配置文件：%s", path)

    # 旧版 DouK 的下载记录（sqlite）搬到 下载记录.json
    records = os.path.join(data, "下载记录.json")
    db = os.path.join(vol, "download_data.db")
    if not os.path.isfile(records) and os.path.isfile(db):
        try:
            if progress:
                progress("导入旧的下载记录…")
            import_legacy_records(vol, records)
        except Exception as exc:  # noqa: BLE001
            log.warning("导入旧下载记录失败：%s", exc)

    return lib_dir(""), data


def import_legacy_records(volume_dir, data_path):
    """把 DouK 的 sqlite 下载记录转成 json（旧版本升级时用一次）。"""
    try:
        import sqlite3
    except ImportError:      # 打包时去掉了 sqlite3（省 4MB），旧记录迁移就跳过
        log.info("包里没有 sqlite3，跳过旧下载记录迁移（不影响新记录）")
        return 0

    db = os.path.join(volume_dir, "download_data.db")
    if not os.path.isfile(db):
        return 0
    rows = []
    try:
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("select name from sqlite_master where type='table'")
        tables = [r[0] for r in cur.fetchall()]
        table = "download_data" if "download_data" in tables else (tables[0] if tables else "")
        if table:
            cur.execute("select * from %s" % table)
            cols = [d[0] for d in cur.description]
            for row in cur.fetchall():
                item = dict(zip(cols, row))
                key = str(item.get("id") or item.get("aweme_id") or item.get("url") or "")
                if key:
                    rows.append((key, item))
        conn.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("读取旧下载记录失败：%s", exc)
        return 0

    if not rows:
        return 0
    try:
        with open(data_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        data = {}
    for key, item in rows:
        data.setdefault(key, item)
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    log.info("已导入 %d 条旧下载记录", len(rows))
    return len(rows)


def migrate_works(old_volume, new_volume, move=True):
    """把旧 Volume 下的作品文件夹搬到新数据目录。

    返回 {"moved": [...], "skipped": [...]}：
      moved   搬过去的文件夹名
      skipped 没敢动的（跨盘、被占用、同名已存在）
    """
    info = {"moved": [], "skipped": []}
    if not old_volume or not os.path.isdir(old_volume):
        return info
    os.makedirs(new_volume, exist_ok=True)
    skip_names = {"Download", "__pycache__"}
    for name in sorted(os.listdir(old_volume)):
        src = os.path.join(old_volume, name)
        if not os.path.isdir(src) or name in skip_names:
            continue
        dst = os.path.join(new_volume, name)
        try:
            if os.path.exists(dst):
                info["skipped"].append(name)
                continue
            if move:
                if os.path.splitdrive(os.path.abspath(src))[0].lower() != \
                        os.path.splitdrive(os.path.abspath(dst))[0].lower():
                    shutil.copytree(src, dst)
                    shutil.rmtree(src, ignore_errors=True)
                else:
                    shutil.move(src, dst)
            else:
                shutil.copytree(src, dst)
            info["moved"].append(name)
        except Exception:  # noqa: BLE001
            info["skipped"].append(name)
    return info