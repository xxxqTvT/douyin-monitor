# -*- coding: utf-8 -*-
"""直播录制（纯 Python，不依赖 ffmpeg）

从直播拉流地址（FLV）直接读 HTTP 流，按 FLV tag 边界增量写盘：
    - 每写完一个完整 tag 就是一段合法 FLV，随时中断都能播放（最多丢最后一帧）；
    - 解析 onMetaData / 音视频 tag 得到分辨率与编码，用 tag 时间戳计算录制时长；
    - 不需要 ffmpeg，也不需要额外解码器，CPU 几乎为零。

对外接口：
    LiveRecorder(...)       一个录制任务：.start() .stop() .snapshot() .running
    RecorderManager(...)    多个录制：.start() .stop() .stop_all() .snapshots() .prune()
    format_seconds(n)       "1:02:03" / "2:03"
    human_size(n)           "12.3 MB"
    kill_orphans()/list_ffmpeg()/find_ffmpeg()  保留的空实现，兼容旧调用
"""

import os
import re
import socket
import struct
import threading
import time
import urllib.request

log = None  # 由客户端注入 logging.Logger

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

AUDIO_CODECS = {0: "pcm", 1: "adpcm", 2: "mp3", 3: "pcm-le", 4: "nellymoser",
                5: "speex", 6: "pcm-be", 7: "g711a", 8: "g711u", 10: "aac"}
VIDEO_CODECS = {2: "sorenson", 3: "vp6", 4: "vp6a", 5: "vp6f", 6: "screen",
                7: "h264", 12: "hevc"}


# ------------------------------------------------------------------ 小工具
def find_ffmpeg():
    """已不再需要 ffmpeg，保留函数仅为兼容旧调用。"""
    return ""


def list_ffmpeg():
    """已不再使用 ffmpeg，保留空实现兼容旧调用。"""
    return []


def kill_orphans(dest_dir="", logger=None):
    """不再有外挂进程需要清理，保留空实现兼容旧调用。"""
    return []


def format_seconds(sec):
    try:
        sec = int(round(float(sec or 0)))
    except (TypeError, ValueError):
        sec = 0
    if sec < 0:
        sec = 0
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%d:%02d" % (m, s)


def human_size(size):
    try:
        size = float(size or 0)
    except (TypeError, ValueError):
        size = 0.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return ("%d B" % int(size)) if unit == "B" else "%.1f %s" % (size, unit)
        size /= 1024.0


def safe_name(name, limit=60):
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" .")
    return (text or "直播")[:limit]


def map_codec(value, table):
    try:
        return table.get(int(value), "") or ""
    except (TypeError, ValueError):
        return ""


# ------------------------------------------------------------------ FLV 解析
class _AmfReader(object):
    """够用的 AMF0 读取器（只取 onMetaData 里的数值字段）"""

    def __init__(self, data):
        self.data = data
        self.pos = 0

    def byte(self):
        if self.pos >= len(self.data):
            raise ValueError("AMF 越界")
        v = self.data[self.pos]
        self.pos += 1
        return v

    def read(self, n):
        if self.pos + n > len(self.data):
            raise ValueError("AMF 越界")
        v = self.data[self.pos:self.pos + n]
        self.pos += n
        return v

    def u16(self):
        return int.from_bytes(self.read(2), "big")

    def u32(self):
        return int.from_bytes(self.read(4), "big")

    def double(self):
        return struct.unpack(">d", self.read(8))[0]

    def string(self):
        return self.read(self.u16()).decode("utf-8", "replace")

    def value(self, depth=0):
        if depth > 6:
            return None
        t = self.byte()
        if t == 0x00:
            return self.double()
        if t == 0x01:
            return bool(self.byte())
        if t == 0x02:
            return self.string()
        if t == 0x03:
            obj = {}
            while self.pos < len(self.data):
                key = self.string()
                if not key:
                    break
                obj[key] = self.value(depth + 1)
            if self.pos < len(self.data):
                self.byte()
            return obj
        if t == 0x08:
            self.u32()
            obj = {}
            while self.pos < len(self.data):
                key = self.string()
                if not key:
                    break
                obj[key] = self.value(depth + 1)
            return obj
        if t == 0x0A:
            n = self.u32()
            return [self.value(depth + 1) for _ in range(min(n, 256))]
        if t == 0x0B:
            return self.double()
        if t == 0x0C:
            return self.read(self.u32()).decode("utf-8", "replace")
        return None


def parse_on_meta_data(payload):
    """返回 {width, height, videodatarate, audiodatarate, videocodecid, audiocodecid}"""
    info = {}
    if not payload or payload[0] != 0x02:
        return info
    try:
        rd = _AmfReader(payload)
        rd.value()  # onMetaData
        meta = rd.value()
    except Exception:
        return info
    if not isinstance(meta, dict):
        return info
    for key in ("width", "height", "framerate", "videodatarate", "audiodatarate",
                "videocodecid", "audiocodecid", "duration"):
        val = meta.get(key)
        if isinstance(val, (int, float)):
            info[key] = val
    return info


class LiveRecorder(object):
    """单路直播录制：HTTP 拉 FLV，按 tag 边界写盘"""

    def __init__(self, key, nickname="", platform="douyin", url="", quality="",
                 quality_label="", dest_dir="", title="", tag="", frag=True):
        self.key = str(key or "")
        self.nickname = str(nickname or "直播")
        self.platform = platform or "douyin"
        self.url = url or ""
        self.quality = str(quality or "")
        self.quality_label = str(quality_label or self.quality or "")
        self.dest_dir = dest_dir or ""
        self.title = str(title or "")
        self.tag = tag or ""
        self.frag = bool(frag)   # 兼容旧参数（FLV 本身就是随时可播的）

        self.state = "准备中"
        self.error = ""
        self.file = ""
        self.started = time.time()
        self.ended = None
        self.last_ts = 0          # FLV tag 时间戳（毫秒）
        self.first_ts = 0         # 第一个 tag 的时间戳（拉流可能从任意时刻开始，时长要减掉）
        self.width = 0
        self.height = 0
        self.vcodec = ""
        self.acodec = ""
        self.vrate = 0.0
        self.arate = 0.0
        self._bytes = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._resp = None
        self._thread = None
        self._last_data = time.time()

    # -------------------------------------------------------------- 对外
    @property
    def running(self):
        return self.state in ("连接中", "录制中") and not self._stop.is_set()

    def start(self):
        if self.running:
            return False
        if not self.url:
            self.state = "出错"
            self.error = "没有可用的拉流地址"
            return False
        self.started = time.time()
        self._stop.clear()
        self.state = "连接中"
        self.error = ""
        self.ended = None
        self.last_ts = 0
        self._bytes = 0
        name = "%s_%s_%s.flv" % (
            safe_name(self.nickname),
            time.strftime("%Y%m%d_%H%M%S"),
            safe_name(self.quality_label or self.quality or "原画", 20),
        )
        try:
            if self.dest_dir:
                os.makedirs(self.dest_dir, exist_ok=True)
            self.file = os.path.join(self.dest_dir, name) if self.dest_dir else name
        except Exception as exc:
            self.state = "出错"
            self.error = "创建录像目录失败：%s" % exc
            return False
        self._thread = threading.Thread(target=self._run, name="live-%s" % self.key, daemon=True)
        self._thread.start()
        return True

    def stop(self, wait=10, reason=""):
        self._stop.set()
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:
            pass
        th = self._thread
        if th is not None and th.is_alive() and wait:
            th.join(timeout=wait)
        if self.state != "出错":
            self.state = "已结束"
        self.ended = self.ended or time.time()
        return {"file": self.file, "state": self.state, "error": self.error}

    def snapshot(self):
        seconds = self.seconds()
        size = self._size()
        bitrate = (size * 8.0 / seconds / 1000.0) if (seconds > 0 and size > 0) else 0.0
        res = "%dx%d" % (self.width, self.height) if (self.width and self.height) else "—"
        return {
            "key": self.key,
            "nickname": self.nickname,
            "platform": self.platform,
            "quality": self.quality,
            "quality_label": self.quality_label,
            "resolution": res,
            "seconds": seconds,
            "duration": format_seconds(seconds),
            "size": size,
            "size_text": human_size(size),
            "bitrate": bitrate,
            "speed": "—",
            "audio": self.acodec or "—",
            "state": self.state,
            "error": self.error,
            "file": self.file,
            "running": self.running,
            "title": self.title,
        }

    def seconds(self):
        if self.last_ts:
            return max(0.0, (self.last_ts - (self.first_ts or 0)) / 1000.0)
        if self.state in ("已结束", "出错") and self.ended:
            return max(0.0, self.ended - self.started)
        if self.running:
            return max(0.0, time.time() - self.started)
        return 0.0

    # -------------------------------------------------------------- 内部
    def _size(self):
        with self._lock:
            if self.file and os.path.isfile(self.file):
                try:
                    return os.path.getsize(self.file)
                except OSError:
                    pass
            return self._bytes

    def _log(self, level, msg, *args):
        if log is None:
            return
        try:
            getattr(log, level, log.info)("[%s] " + msg, self.nickname, *args)
        except Exception:
            pass

    def _run(self):
        try:
            self._stream()
        except Exception as exc:
            if not self._stop.is_set():
                self.state = "出错"
                self.error = "%s: %s" % (type(exc).__name__, exc)
                self._log("warning", "录制中断：%s（%s）", self.nickname, self.error)
        finally:
            self.ended = time.time()
            if self.state != "出错":
                self.state = "已结束"
            self._finalize()
            self._log("info", "录制结束：%s（时长 %s，大小 %s）",
                      self.nickname, format_seconds(self.seconds()), human_size(self._size()))

    def _finalize(self):
        """录完把 FLV 转成 MP4（Windows 自带播放器打不开 FLV）。失败就保留 FLV。"""
        if not self.file or not self.file.lower().endswith(".flv"):
            return
        if not os.path.isfile(self.file):
            return
        try:
            if os.path.getsize(self.file) < 64 * 1024:
                return
            import live_remux
        except Exception:  # noqa: BLE001
            return
        dst = self.file[:-4] + ".mp4"
        try:
            live_remux.remux_flv_to_mp4(self.file, dst)
            if not os.path.getsize(dst):
                raise ValueError("MP4 是空文件")
            try:
                os.remove(self.file)
            except OSError:
                pass
            self.file = dst
            self._bytes = os.path.getsize(dst)
            self._log("info", "已转成 MP4：%s（可以直接播放）", os.path.basename(dst))
        except Exception as exc:  # noqa: BLE001
            try:
                if os.path.exists(dst) and os.path.getsize(dst) == 0:
                    os.remove(dst)
            except OSError:
                pass
            self._log("warning", "转 MP4 失败（保留 FLV）：%s", exc)

    def _open(self):
        referer = "https://live.douyin.com/" if self.platform == "douyin" else "https://www.tiktok.com/"
        req = urllib.request.Request(self.url, headers={
            "User-Agent": UA,
            "Accept": "*/*",
            "Referer": referer,
        })
        return urllib.request.urlopen(req, timeout=20)

    def _stream(self):
        self._resp = self._open()
        self.state = "录制中"
        self._log("info", "开始录制：%s%s（%s）",
                  self.nickname,
                  ("《%s》" % self.title) if self.title else "",
                  self.quality_label or self.quality or "原画")
        buf = bytearray()
        wrote_header = False
        fh = None
        try:
            fh = open(self.file, "wb")
            while not self._stop.is_set():
                try:
                    chunk = self._resp.read(65536)
                except (socket.timeout, TimeoutError):
                    if time.time() - self._last_data > 30:
                        self._log("warning", "直播卡住 30 秒没有数据，已停止录制")
                        break
                    continue
                except Exception:
                    if self._stop.is_set():
                        break
                    raise
                if not chunk:
                    self._log("info", "直播流已结束（服务器关闭了连接）")
                    break
                self._last_data = time.time()
                buf += chunk
                if not wrote_header:
                    if len(buf) < 13:
                        continue
                    if buf[0:3] != b"FLV":
                        raise ValueError("不是 FLV 流（开头不是 FLV 标记），这个地址可能不是 FLV 拉流")
                    fh.write(bytes(buf[:13]))
                    del buf[:13]
                    wrote_header = True
                    self._bytes = 13
                self._consume(buf, fh)
        finally:
            try:
                if fh is not None:
                    fh.flush()
                    fh.close()
            except Exception:
                pass
            try:
                if self._resp is not None:
                    self._resp.close()
            except Exception:
                pass

    def _consume(self, buf, fh):
        """切出完整 tag 写盘，同时解析分辨率/编码/时间戳"""
        while len(buf) >= 11:
            tag_type = buf[0] & 0x1F
            size = (buf[1] << 16) | (buf[2] << 8) | buf[3]
            ts = (buf[4] << 16) | (buf[5] << 8) | buf[6] | (buf[7] << 24)
            total = 11 + size + 4
            if len(buf) < total:
                return
            body = bytes(buf[11:11 + size])
            fh.write(bytes(buf[:total]))
            self._bytes += total
            del buf[:total]
            if ts:
                if not self.first_ts:
                    self.first_ts = ts
                self.last_ts = max(self.last_ts, ts)
            if tag_type == 18:
                info = parse_on_meta_data(body)
                if info:
                    w = int(info.get("width") or 0)
                    h = int(info.get("height") or 0)
                    if w and h and (w, h) != (self.width, self.height):
                        self.width, self.height = w, h
                        self._log("info", "画面分辨率 %dx%d", w, h)
                    if info.get("videodatarate"):
                        self.vrate = float(info["videodatarate"])
                    if info.get("audiodatarate"):
                        self.arate = float(info["audiodatarate"])
                    if info.get("videocodecid"):
                        self.vcodec = map_codec(info["videocodecid"], VIDEO_CODECS)
                    if info.get("audiocodecid"):
                        self.acodec = map_codec(info["audiocodecid"], AUDIO_CODECS)
            elif tag_type == 9 and body:
                codec = VIDEO_CODECS.get(body[0] & 0x0F, "")
                if codec and codec != self.vcodec:
                    self.vcodec = codec
            elif tag_type == 8 and body:
                codec = AUDIO_CODECS.get(body[0] >> 4, "")
                if codec and codec != self.acodec:
                    self.acodec = codec


class RecorderManager(object):
    """管理多路直播录制（进程内线程，无外部进程）"""

    def __init__(self, dest_dir="", logger=None, ffmpeg="", **kwargs):
        self.dest_dir = dest_dir
        self.items = {}
        self._lock = threading.Lock()
        global log
        if logger is not None:
            log = logger

    def get(self, key):
        with self._lock:
            return self.items.get(str(key or ""))

    def start(self, key, nickname="", platform="douyin", url="", quality="",
              quality_label="", title="", frag=True, **kwargs):
        key = str(key or "")
        if not key:
            return None
        with self._lock:
            exist = self.items.get(key)
            if exist is not None and exist.running:
                return exist
            rec = LiveRecorder(key=key, nickname=nickname, platform=platform, url=url,
                               quality=quality, quality_label=quality_label,
                               dest_dir=self.dest_dir, title=title, frag=frag)
            self.items[key] = rec
        rec.start()
        if rec.state == "出错":
            self._log("warning", "【%s】直播录制没能开始：%s", rec.nickname, rec.error)
        return rec

    def stop(self, key, reason="", wait=10):
        rec = self.get(key)
        if rec is None:
            return None
        info = rec.stop(wait=wait, reason=reason)
        if reason:
            self._log("info", "【%s】%s", rec.nickname, reason)
        return info

    def stop_all(self, reason="", wait=20):
        snaps = []
        for key in list(self.items.keys()):
            rec = self.items.get(key)
            if rec is None:
                continue
            if rec.running:
                rec.stop(wait=wait, reason=reason)
            snaps.append(rec.snapshot())
        return snaps

    def snapshots(self):
        with self._lock:
            items = list(self.items.values())
        return [rec.snapshot() for rec in items]

    def prune(self, keep=12):
        now = time.time()
        with self._lock:
            for key, rec in list(self.items.items()):
                if rec.running:
                    continue
                ended = rec.ended or rec.started
                if now - ended > 180:
                    del self.items[key]
            if len(self.items) > keep:
                finished = sorted((rec.ended or rec.started, key)
                                  for key, rec in self.items.items() if not rec.running)
                for _ts, key in finished[:max(0, len(self.items) - keep)]:
                    self.items.pop(key, None)

    def _log(self, level, msg, *args):
        if log is None:
            return
        try:
            getattr(log, level, log.info)(msg, *args)
        except Exception:
            pass