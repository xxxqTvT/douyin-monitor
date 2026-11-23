"""进程内下载作品（视频 / 图集 / 实况）。

来源说明：
    本文件为本项目自行实现，**不含 DouK-Downloader 的代码**。
    沿用其文件命名规则（`时间-类型-昵称-标题`）只是为了兼容老用户已有的文件，
    详见 NOTICE.md 第 2 节。
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

log = logging.getLogger("monitor")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")

STAT_KEYS = ("下载视频作品", "跳过视频作品", "下载图集作品", "跳过图集作品",
             "下载实况作品", "跳过实况作品")

IMAGE_SUFFIX = {"image/jpeg": ".jpeg", "image/jpg": ".jpeg", "image/png": ".png",
                "image/webp": ".webp", "image/gif": ".gif", "image/heic": ".heic"}


# ------------------------------------------------------------------ 工具
def aweme_id(url):
    """从作品链接里取作品 ID；纯数字也认"""
    text = (url or "").strip()
    for pat in (r"/(?:video|note|slides|photo)/(\d{6,})",
                r"modal_id=(\d{6,})",
                r"/(\d{15,25})(?:[/?#]|$)"):
        m = re.search(pat, text)
        if m:
            return m.group(1)
    if re.fullmatch(r"\d{15,25}", text):
        return text
    return ""


def safe_filename(text, limit=64):
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip().strip(".")
    return text[:limit] if text else ""


def where_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


# ------------------------------------------------------------ 下载记录（json）
def records_path(data_root):
    return os.path.join(data_root, "下载记录.json")


def load_records(data_root):
    path = records_path(data_root)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        log.warning("下载记录损坏，已忽略：%s", path)
        return {}
    return data if isinstance(data, dict) else {}


def save_records(data_root, records):
    path = records_path(data_root)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("保存下载记录失败：%s", exc)


# ---------------------------------------------------------------- 媒体挑选
def gear_rank(name):
    """从清晰度档位名猜短边分辨率：normal_1080_0 / adapt_lowest_4_1（4K）等。"""
    s = str(name or "")
    nums = [int(n) for n in re.findall(r"\d{3,4}", s)]
    best = max(nums) if nums else 0
    # adapt_lowest_4_1 / adapt_lowest_2_1 这种把 4K/2K 写成单数字的档位（其它名字里的 _2_ 是变体序号）
    if "adapt" in s:
        for tok, short in (("4", 2160), ("2", 1440)):
            if re.search(r"(?:^|_)%s(?:_|$)" % tok, s):
                best = max(best, short)
    return best


def _pick_image_url(url_list):
    """同一张图常给多份格式（webp / jpeg）：优先 JPEG —— 质量更高，也能写成实况照片。"""
    urls = [u for u in (url_list or []) if isinstance(u, str) and u.strip()]
    for u in urls:
        ext = u.split("?")[0].rsplit(".", 1)[-1].lower()
        if ext in ("jpeg", "jpg"):
            return u
    return urls[0] if urls else ""


def pick_media(item):
    """返回 (类型, [(url, 后缀)])；类型是 video / image / live"""
    images = item.get("images") or []
    if images:
        urls = []
        for im in images:
            if not isinstance(im, dict):
                continue
            u = _pick_image_url(im.get("url_list"))
            if u:
                urls.append((u, ""))
        if urls:
            return "image", urls
    video = item.get("video") or {}
    # 优先码率最高的地址；同码率再比分辨率（避免选到低分辨率高码率的转码档）
    best = None
    for br in (video.get("bit_rate") or []):
        addr = (br.get("play_addr") or {}).get("url_list") or []
        if not addr:
            continue
        # 先按分辨率档位（1440/2160 常常码率反而比 1080 低），再按码率、分辨率字段
        score = (gear_rank(br.get("gear_name")),
                 int(br.get("bit_rate") or 0),
                 int(br.get("width") or 0) * int(br.get("height") or 0))
        if best is None or score > best[0]:
            best = (score, addr[0])
    if best:
        return "video", [(best[1], ".mp4")]
    addr = (video.get("play_addr") or {}).get("url_list") or []
    if addr:
        return "video", [(addr[0], ".mp4")]
    return "", []


def live_clips(item):
    """图集里带「实况」的图片：返回 [(图片序号, 视频 url, 时长毫秒)]，序号与静图 _N 一致。"""
    out = []
    for i, im in enumerate(item.get("images") or [], 1):
        if not isinstance(im, dict):
            continue
        video = im.get("video") or {}
        if not isinstance(video, dict):
            continue
        best = None
        for br in (video.get("bit_rate") or []):
            addr = (br.get("play_addr") or {}).get("url_list") or []
            if not addr:
                continue
            score = (int(br.get("bit_rate") or 0),
                     int(br.get("width") or 0) * int(br.get("height") or 0))
            if best is None or score > best[0]:
                best = (score, addr[0])
        if best is None:
            addr = (video.get("play_addr") or {}).get("url_list") or []
            if addr:
                best = (None, addr[0])
        if best:
            out.append((i, best[1], int(video.get("duration") or 0)))
    return out


def item_title(item):
    title = item.get("desc") or ""
    if not title and item.get("preview_title"):
        title = item.get("preview_title")
    return safe_filename(title, 64)


def item_time(item):
    ts = item.get("create_time") or 0
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        ts = 0
    if ts <= 0:
        ts = int(time.time())
    return time.strftime("%Y-%m-%d %H.%M.%S", time.localtime(ts))


def item_author(item):
    return safe_filename(((item.get("author") or {}).get("nickname")) or "", 40)


def build_name(item, kind, index=0):
    """2026-11-02 00.35.12-视频-昵称-标题.mp4 / ..._1.jpeg"""
    stamp = item_time(item)
    label = {"video": "视频", "image": "图集", "live": "实况"}.get(kind, "作品")
    author = item_author(item)
    title = item_title(item)
    parts = [stamp, label]
    if author:
        parts.append(author)
    if title:
        parts.append(title)
    stem = "-".join(parts)
    if kind == "image" and index:
        stem = "%s_%d" % (stem, index)
    return stem


# ------------------------------------------------------------------ 下载
def _headers(item, platform, cookie_str):
    referer = "https://www.douyin.com/"
    if (platform or "douyin") == "tiktok":
        referer = "https://www.tiktok.com/"
    return {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": referer,
        "Cookie": cookie_str or "",
    }


def disk_free(path):
    """目标目录所在磁盘剩余字节数（目录不存在就退到它的父目录）"""
    import shutil as _shutil

    probe = path or "."
    for _ in range(20):
        if os.path.isdir(probe):
            break
        parent = os.path.dirname(os.path.abspath(probe))
        if parent == probe:
            break
        probe = parent
    try:
        return _shutil.disk_usage(probe).free
    except OSError:
        return 1 << 62


def download_file(url, path, headers, timeout=60, retries=3):
    """带重试的下载；先写 .part 再改名，并核对 Content-Length，避免留下半个文件。"""
    tmp = path + ".part"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                try:
                    expected = int(resp.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    expected = 0
                written = 0
                with open(tmp, "wb") as f:
                    while True:
                        chunk = resp.read(262144)
                        if not chunk:
                            break
                        f.write(chunk)
                        written += len(chunk)
            if expected and written != expected:
                raise ValueError("文件不完整：收到 %d / 应为 %d 字节" % (written, expected))
            if written <= 0:
                raise ValueError("服务器返回了空文件")
            os.replace(tmp, path)
            return os.path.getsize(path), ctype
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = exc
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            time.sleep(1.0 + attempt)
    raise RuntimeError("下载失败：%s（%s）" % (url[:80], last))


def download_item(item, kind, dest, headers, timeout=60, save_live=True):
    """下载单个作品的全部文件，返回 (文件列表, 说明)

    图集里带「实况」的图片，除了静图还会存一份同名 `.mov`（Apple 实况照片配对），
    静图是 JPEG 时同时写入相同标识，导入 iPhone/macOS「照片」即为实况照片。
    """
    plan = pick_media(item)
    if not plan[1]:
        return [], "没有找到可下载的地址"
    kind, urls = plan
    files = []
    for i, (url, suffix) in enumerate(urls, 1):
        name = build_name(item, kind, i if kind == "image" else 0)
        guess = suffix or os.path.splitext(url.split("?")[0])[1] or ""
        target = os.path.join(dest, (name + (guess if guess.lower() in
                                             (".mp4", ".jpeg", ".jpg", ".png", ".webp", ".gif", ".heic")
                                             else "")) if guess else name)
        try:
            size, ctype = download_file(url, target, headers, timeout=timeout)
            # 图片后缀可能是 webp，按 Content-Type 纠正一下
            real = IMAGE_SUFFIX.get(ctype) if kind == "image" else None
            if real and not target.lower().endswith(real):
                fixed = target + real
                try:
                    os.replace(target, fixed)
                    target = fixed
                except OSError:
                    pass
            files.append(target)
            log.info("已下载：%s（%.1f MB）", os.path.basename(target), size / 1048576.0)
        except RuntimeError as exc:
            log.warning("%s", exc)
    if save_live and kind == "image":
        files.extend(_download_live_pairs(item, dest, headers, timeout, files))
    return files, ""


def _download_live_pairs(item, dest, headers, timeout, stills):
    """把图集里的实况短片存成与静图同名的 .mov，并给 JPEG 写入 Apple 标识。"""
    import live_photo

    got = []
    by_index = {}
    for path in stills:
        base, _ext = os.path.splitext(os.path.basename(path))
        m = re.search(r"_(\d+)$", base)
        if m:
            by_index[int(m.group(1))] = path
    for idx, url, _dur in live_clips(item):
        stem = build_name(item, "image", idx)
        mov = os.path.join(dest, stem + ".mov")
        if os.path.isfile(mov) and os.path.getsize(mov) > 0:
            got.append(mov)
            continue
        tmp = os.path.join(dest, stem + ".live.mp4")
        try:
            download_file(url, tmp, headers, timeout=timeout)
            ident = live_photo.content_id("%s:%s" % (item.get("aweme_id") or "", idx))
            size = live_photo.write_mov(tmp, mov, ident)
            still = by_index.get(idx) or ""
            if still and still.lower().endswith((".jpg", ".jpeg")):
                live_photo.add_xmp_to_jpeg(still, ident)
            got.append(mov)
            log.info("已保存实况：%s（%.1f MB，静图+短片配对）",
                     os.path.basename(mov), size / 1048576.0)
        except (RuntimeError, ValueError, OSError) as exc:
            log.warning("实况短片没保存成功（%s）：%s", os.path.basename(mov), exc)
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
    return got


def media_targets(item, kind, urls, dest):
    """按 download_item 的命名规则算出每个文件的目标路径（用于判断是否已经下过）。"""
    out = []
    for i, entry in enumerate(urls, 1):
        url, suffix = (entry[0], entry[1] or "") if isinstance(entry, (tuple, list)) else (entry, "")
        name = build_name(item, kind, i if kind == "image" else 0)
        guess = suffix or os.path.splitext(url.split("?")[0])[1] or ""
        if guess.lower() not in (".mp4", ".jpeg", ".jpg", ".png", ".webp", ".gif", ".heic"):
            guess = ""
        out.append(os.path.join(dest, name + guess))
    return out


def existing_files(item, kind, urls, dirs):
    """在候选目录里找这组作品是否已经完整落盘（文件名按 build_name 规则）。

    只看文件名不看记录：记录丢了/换过文件夹时，也能认出「其实已经下过」，
    避免重新下载一遍再生成「名字 (2)」副本。返回文件列表（按 urls 顺序），不完整则空。
    """
    if not urls:
        return []
    stems = []
    for i in range(1, len(urls) + 1):
        stems.append(build_name(item, kind, i if kind == "image" else 0))
    need = len(stems)
    hits = []
    for folder in dirs:
        if not folder or not os.path.isdir(folder):
            continue
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        found = {}
        for name in names:
            if name.lower().endswith(".part"):
                continue
            base, _ext = os.path.splitext(name)
            for idx, stem in enumerate(stems):
                if base == stem:
                    found[idx] = os.path.join(folder, name)
        if len(found) >= need:
            return [found[i] for i in range(need)]
        if len(found) > len(hits):
            hits = [found[i] for i in sorted(found)]
    return []


def backfill_live_item(item, dest, headers, timeout=60):
    """给「已经下过静图」的图集补存实况：确保 <stem>_N.jpeg + <stem>_N.mov 成对。

    返回 (补的实况数, 补的静图数, 清掉的旧 webp 数)。可重复执行，已有的不重下。
    """
    import live_photo

    movs = stills = cleaned = 0
    images = item.get("images") or []
    for idx, url, _dur in live_clips(item):
        stem = build_name(item, "image", idx)
        mov = os.path.join(dest, stem + ".mov")
        jpg = os.path.join(dest, stem + ".jpeg")
        old_webp = os.path.join(dest, stem + ".webp")
        ident = live_photo.content_id("%s:%s" % (item.get("aweme_id") or "", idx))

        if not os.path.isfile(jpg):
            im = images[idx - 1] if 0 < idx <= len(images) else {}
            still_url = _pick_image_url(im.get("url_list") if isinstance(im, dict) else None)
            if still_url:
                tmp = os.path.join(dest, stem + ".still.tmp")
                try:
                    download_file(still_url, tmp, headers, timeout=timeout)
                    with open(tmp, "rb") as fh:
                        magic = fh.read(3)
                    if magic[:2] == b"\xff\xd8":      # JPEG
                        os.replace(tmp, jpg)
                        stills += 1
                    else:                              # 服务器给了 webp 等，退回原名
                        os.replace(tmp, old_webp)
                except (RuntimeError, OSError) as exc:
                    log.warning("补存静图失败（%s）：%s", os.path.basename(jpg), exc)
                finally:
                    if os.path.exists(tmp):
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass

        if not os.path.isfile(mov) or os.path.getsize(mov) <= 0:
            tmp = os.path.join(dest, stem + ".live.mp4")
            try:
                download_file(url, tmp, headers, timeout=timeout)
                live_photo.write_mov(tmp, mov, ident)
                movs += 1
            except (RuntimeError, ValueError, OSError) as exc:
                log.warning("补存实况失败（%s）：%s", os.path.basename(mov), exc)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass

        if os.path.isfile(jpg):
            live_photo.add_xmp_to_jpeg(jpg, ident)
            if os.path.isfile(old_webp):          # 有 JPEG 了，旧 webp 去掉免重复
                try:
                    os.remove(old_webp)
                    cleaned += 1
                except OSError:
                    pass
    return movs, stills, cleaned


def download_items(cfg, items, platform, dest, cookie_str="", timeout=60, records=None,
                   data_root="", stats=None, key_prefix="", progress=None, alt_dirs=()):
    """批量下载若干作品条目（item 里至少有 aweme_id）

    progress(done, total)：每处理完一个作品回调一次（含跳过），用于界面上显示进度。
    下载记录里记录的旧文件如果已经不在了，会重新下载，而不是一直当成「已下载」跳过。
    """
    stats = stats if stats is not None else {}
    for key in STAT_KEYS:
        stats.setdefault(key, 0)
    records = records if records is not None else {}
    try:
        tiers = [len((it.get("video") or {}).get("bit_rate") or []) for it in items
                 if isinstance(it, dict)]
        if tiers and max(tiers) > 0:
            gears = []
            for it in items:
                brs = ((it.get("video") or {}).get("bit_rate") or []) if isinstance(it, dict) else []
                if brs:
                    best = max(brs, key=lambda b: (int(b.get("bit_rate") or 0),
                                                   int(b.get("width") or 0) * int(b.get("height") or 0)))
                    g = str(best.get("gear_name") or best.get("bit_rate") or "")
                    if g and g not in gears:
                        gears.append(g)
            log.info("视频画质：按接口最高码率/分辨率下载（最多 %d 档；选中 %s）",
                     max(tiers), "、".join(gears[:3]) or "最高档")
    except Exception:  # noqa: BLE001
        pass
    files = []
    where_dir(dest)
    free = disk_free(dest)
    floor = int(cfg.get("disk_free_min_mb") or 500) * 1024 * 1024
    if floor and free < floor:
        log.error("磁盘剩余空间不足：%.1f GB（低于 %d MB），这一轮先不下载",
                  free / 1073741824.0, floor // (1024 * 1024))
        stats["空间不足"] = 1
        return stats, []
    dirs = [dest] + [d for d in (alt_dirs or ()) if d and os.path.abspath(d) != os.path.abspath(dest)]
    headers = _headers(None, platform, cookie_str)
    total = len(items)
    done = 0

    def _tick():
        if progress is not None:
            try:
                progress(done, total)
            except Exception:  # noqa: BLE001 - 进度回调不能影响下载
                pass

    for item in items:
        aid = str(item.get("aweme_id") or item.get("id") or "")
        if not aid:
            continue
        key = "%s:%s" % (key_prefix or platform, aid)
        old = records.get(key)
        if old:
            rel = str(old.get("file") or old.get("path") or "") if isinstance(old, dict) else ""
            full = rel
            if rel and not os.path.isabs(rel) and data_root:
                full = os.path.join(data_root, rel)
            if not rel or not os.path.exists(full):
                old = None          # 记录还在但文件没了：重新下载
        if old:
            kind = "image" if (item.get("images") or []) else "video"
            stats["跳过图集作品" if kind == "image" else "跳过视频作品"] += 1
            done += 1
            _tick()
            continue
        plan = pick_media(item)
        kind = plan[0]
        if not kind:
            done += 1
            _tick()
            continue
        found = existing_files(item, kind, plan[1], dirs)
        if found:
            if records is not None:
                try:
                    shown = os.path.relpath(found[0], data_root) if data_root else found[0]
                except ValueError:
                    shown = found[0]
                records[key] = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "file": shown,
                                "title": item_title(item)}
            stats["跳过图集作品" if kind == "image" else "跳过视频作品"] += 1
            done += 1
            _tick()
            continue
        targets = media_targets(item, kind, plan[1], dest)
        if targets and all(os.path.exists(x) for x in targets):
            # 目标位置已经有同名文件（例如历史视频早就在账号文件夹里），不再重下一遍
            if records is not None:
                try:
                    shown = os.path.relpath(targets[0], data_root) if data_root else targets[0]
                except ValueError:
                    shown = targets[0]
                records[key] = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "file": shown,
                                "title": item_title(item)}
            stats["跳过图集作品" if kind == "image" else "跳过视频作品"] += 1
            done += 1
            _tick()
            continue
        # 图集默认就把实况存成「静图 + 同名 .mov」配对，不需要额外开关
        got, note = download_item(item, kind, dest, headers, timeout=timeout)
        got = [f for f in got if os.path.isfile(f) and os.path.getsize(f) > 0]
        live_n = sum(1 for f in got if f.lower().endswith(".mov"))
        if live_n:
            stats["下载实况作品"] += live_n
        if not got and not note:
            note = "下载器没有生成文件（地址失效或服务器拒绝）"
        if got:
            label = "下载图集作品" if kind == "image" else "下载视频作品"
            stats[label] += 1
            files.extend(got)
            if records is not None:
                try:
                    shown = os.path.relpath(got[0], data_root) if data_root else got[0]
                except ValueError:      # 保存目录和作品不在同一个盘，relpath 会失败
                    shown = got[0]
                records[key] = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "file": shown,
                                "title": item_title(item)}
        else:
            log.warning("作品 %s 没下载成功：%s", aid, note or "未知原因")
        done += 1
        _tick()
    return stats, files


def match_name_files(item, kind, dirs):
    """按作品元数据（时间/昵称/标题）在目录里找同名文件，不需要先拿到真实下载地址。

    核账时作品列表里已经有标题、时间、作者，先用文件名判断「其实早就下过」，
    就不用为了下 0 个文件去请求 200 次详情接口（会被风控拦）。
    视频/实况匹配完整文件名；图集匹配 `..._1.webp` 这类同前缀文件。
    """
    if kind == "image":
        stem = build_name(item, "image")
        match = lambda base: base == stem or base.startswith(stem + "_")
    else:
        stem = build_name(item, kind)
        match = lambda base: base == stem
    out = []
    for folder in dirs:
        if not folder or not os.path.isdir(folder):
            continue
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if name.lower().endswith(".part"):
                continue
            base, _ext = os.path.splitext(name)
            if match(base):
                out.append(os.path.join(folder, name))
    return out


def stats_text(stats, extra=""):
    if stats.get("空间不足"):
        return "磁盘剩余空间不足，这一轮没有下载（清理一下磁盘，或调低配置里的最小剩余空间）"
    lines = []
    for key in STAT_KEYS:
        if stats.get(key):
            lines.append("%s %d 个" % (key, stats[key]))
    if extra:
        lines.append(extra)
    return "\n".join(lines) if lines else "没有需要下载的作品（都已下载过）"