# -*- coding: utf-8 -*-
"""链接解析：识别链接属于哪个平台。

- 抖音：交给 DouK 内置接口（core.download_links）解析下载；
- TikTok：和其它平台一样交给 yt-dlp；
- 其它平台（B 站、快手、小红书、微博、YouTube、X、Instagram、Vimeo…）：
  用随程序一起打包的 yt-dlp 解析，直接拿到视频/音频文件。

对外主要接口：
    detect(url)              -> (平台 key, 平台中文名)
    label(key)               -> 中文名
    is_native(key)           -> 是否走内置接口（只有抖音）
    available()              -> yt-dlp 是否可用
    probe(url, proxy)        -> dict(标题/作者/时长…)，只解析不下载
    download(url, dest, ...) -> dict(files=[...], title=..., uploader=...)
    download_many(urls, dest, ...) -> dict(files, results)
"""
import os
import re
import shutil
import time

# 平台顺序有讲究：「x.com」要放在「twitter.com」后面也行，但短链域名要排在主域名之后
PLATFORMS = [
    ("douyin", "抖音", (r"douyin\.com", r"iesdouyin\.com")),
    ("tiktok", "TikTok", (r"tiktok\.com",)),
    ("bilibili", "哔哩哔哩", (r"bilibili\.com", r"b23\.tv", r"acg\.tv")),
    ("kuaishou", "快手", (r"kuaishou\.com", r"chenzhongtech\.com", r"gifshow\.com")),
    ("xiaohongshu", "小红书", (r"xiaohongshu\.com", r"xhslink\.com")),
    ("weibo", "微博", (r"weibo\.com", r"weibo\.cn", r"t\.cn")),
    ("youtube", "YouTube", (r"youtube\.com", r"youtu\.be")),
    ("twitter", "X（推特）", (r"twitter\.com", r"x\.com")),
    ("instagram", "Instagram", (r"instagram\.com",)),
    ("tiktok_short", "TikTok", (r"vt\.tiktok\.com", r"vm\.tiktok\.com")),
    ("vimeo", "Vimeo", (r"vimeo\.com",)),
    ("facebook", "Facebook", (r"facebook\.com", r"fb\.watch")),
    ("zhihu", "知乎", (r"zhihu\.com",)),
    ("tieba", "贴吧", (r"tieba\.baidu\.com",)),
]

# 只有抖音走内置签名接口；TikTok 与其它平台一样交给 yt-dlp
# （内置 TikTok 客户端没有单个作品详情接口，单独下载必须走解析器）
NATIVE = {"douyin"}

LABELS = {key: name for key, name, _pats in PLATFORMS}
LABELS["generic"] = "其它站点"

URL_RE = re.compile(r"https?://[^\s<>\"'，。、；）)】]+", re.I)


def detect(url):
    """返回 (平台 key, 中文名)；认不出来的返回 ("generic", "其它站点")"""
    text = (url or "").strip()
    for key, name, patterns in PLATFORMS:
        for pat in patterns:
            if re.search(pat, text, re.I):
                if key == "tiktok_short":
                    return "tiktok", name
                return key, name
    return "generic", LABELS["generic"]


def label(key):
    return LABELS.get(key or "", LABELS["generic"])


def is_native(key_or_url):
    """是否走内置的抖音/TikTok 接口"""
    key = key_or_url
    if re.search(r"https?://", key or "", re.I):
        key, _ = detect(key_or_url)
    return key in NATIVE


def extract_urls(text):
    return URL_RE.findall(text or "")


def available():
    """yt-dlp 是否打包进来了"""
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


UNSUPPORTED = {
    "kuaishou": "快手暂不支持（它的接口需要签名校验），可以在浏览器里下载好再拖进来，"
                "或者把视频直链（.mp4 结尾的地址）粘进来。",
}


def support_note(key):
    """这个平台的支持情况：返回一句人话（None 表示可以正常解析）"""
    return UNSUPPORTED.get(key)


def _common_opts(proxy="", timeout=30):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "retries": 3,
        "socket_timeout": timeout,
        "nocheckcertificate": False,
        "ignoreerrors": False,
        "no_color": True,
    }
    if proxy:
        opts["proxy"] = proxy
    return opts


def _friendly_error(exc):
    text = str(exc) or exc.__class__.__name__
    low = text.lower()
    if "unsupported url" in low or "no suitable" in low:
        return "这个平台暂时解析不了（链接可能不是作品页，或站点需要登录）：%s" % text
    if "private" in low or "login" in low or "cookies" in low:
        return "这个作品需要登录才能看（站点限制了游客访问）：%s" % text
    if "not available" in low or "removed" in low:
        return "作品已删除或该地区不可播放：%s" % text
    if "timed out" in low or "timeout" in low:
        return "解析超时（网络慢或站点被墙，可在设置里配代理）：%s" % text
    if "ssl" in low or "certificate" in low:
        return "HTTPS 证书校验失败（代理证书问题？）：%s" % text
    if "403" in text or "401" in text:
        return "站点拒绝访问（可能需要 Cookie/登录，或者被风控）：%s" % text
    return "解析失败：%s" % text


def probe(url, proxy="", timeout=25):
    """只解析不下载，返回作品信息 dict；失败抛 RuntimeError（附人话说明）"""
    key, _name = detect(url)
    note = support_note(key)
    if note:
        raise RuntimeError(note)
    if not available():
        raise RuntimeError("程序里没有打包 yt-dlp，无法解析这个平台")
    import yt_dlp

    opts = _common_opts(proxy=proxy, timeout=timeout)
    opts["skip_download"] = True
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(_friendly_error(exc))
    if not info:
        raise RuntimeError("没有解析到作品信息（链接可能不是单个作品页）")
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise RuntimeError("这是合集/播放列表，但里面没有可下载的作品")
        info = entries[0]
    return {
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or info.get("creator") or "",
        "duration": info.get("duration") or 0,
        "extractor": info.get("extractor_key") or info.get("extractor") or "",
        "webpage": info.get("webpage_url") or url,
        "count": len(info.get("entries") or []) or 1,
    }


def download(url, dest, proxy="", timeout=60, quality="", progress=None):
    """下载一条链接到 dest，返回 dict(files, title, uploader, extractor)

    只下载站点提供的单个成品文件（不合并音视频，因此不需要 ffmpeg）；
    少数站点只提供分离的音视频轨时，会取其中含音轨的那一路。

    quality: "" 或 "best" = 最高画质；"720" / "480" 这类数字 = 限高；"audio" = 只要音频
    """
    key, _name = detect(url)
    note = support_note(key)
    if note:
        raise RuntimeError(note)
    if not available():
        raise RuntimeError("程序里没有打包 yt-dlp，无法解析这个平台")
    import yt_dlp

    os.makedirs(dest, exist_ok=True)
    opts = _common_opts(proxy=proxy, timeout=timeout)
    opts["outtmpl"] = os.path.join(
        dest, "%(uploader,channel,creator,uploader_id)s - %(title).80B [%(id)s].%(ext)s")
    # 不再依赖 ffmpeg：只挑现成的单文件格式（不做音视频合并）
    if quality == "audio":
        opts["format"] = "ba/b"
    elif quality and str(quality).isdigit():
        opts["format"] = ("b[height<=%s][ext=mp4]/b[height<=%s]/b" % (quality, quality))
    else:
        opts["format"] = "b[ext=mp4]/b"
    if progress:
        def _hook(d):
            try:
                if d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    got = d.get("downloaded_bytes") or 0
                    pct = (got * 100.0 / total) if total else 0
                    progress("下载中 %s %.0f%%（%s / %s）" % (
                        os.path.basename(d.get("filename") or ""), pct,
                        _human(got), _human(total)))
                elif d.get("status") == "finished":
                    progress("下载完成：%s" % os.path.basename(d.get("filename") or ""))
            except Exception:  # noqa: BLE001
                pass
        opts["progress_hooks"] = [_hook]

    started = time.time()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(_friendly_error(exc))
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        info = entries[0] if entries else {}
    files = []
    for item in (info.get("requested_downloads") or []):
        path = item.get("filepath") or item.get("filename")
        if path and os.path.isfile(path):
            files.append(os.path.abspath(path))
    if not files:
        guess = info.get("_filename") or info.get("filename")
        if guess and os.path.isfile(guess):
            files.append(os.path.abspath(guess))
    return {
        "files": files,
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "extractor": info.get("extractor_key") or "",
        "seconds": round(time.time() - started, 1),
    }


def download_many(urls, dest, proxy="", timeout=60, quality="", progress=None):
    """批量下载，单个失败不影响其它；返回 dict(files, results=[{url,ok,msg,...}])"""
    files, results = [], []
    for url in urls:
        key, name = detect(url)
        try:
            res = download(url, dest, proxy=proxy, timeout=timeout,
                           quality=quality, progress=progress)
            files.extend(res["files"])
            results.append({"url": url, "platform": name, "ok": True,
                            "title": res["title"], "files": len(res["files"]),
                            "seconds": res["seconds"]})
        except Exception as exc:  # noqa: BLE001
            results.append({"url": url, "platform": name, "ok": False, "msg": str(exc)})
    return {"files": files, "results": results}


def _human(size):
    try:
        size = float(size or 0)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%.1f %s" % (size, unit) if unit != "B" else "%d B" % size
        size /= 1024.0
    return "%.1f GB" % size