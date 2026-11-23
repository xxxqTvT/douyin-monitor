"""本地 FLV 直播源 + LiveRecorder 端到端冒烟（不联网、不用 ffmpeg）。

服务端按 FLV tag 边界持续发一段假视频（视频 tag，时间戳递增），
录制器应该：连上 → 写出 .flv → 停止后文件可解析、大小 > 0、时长 > 0。
"""
import os
import struct
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, r"D:\dshzijian\源代码")
import live_record


def flv_header():
    return b"FLV\x01\x05\x00\x00\x00\x09" + b"\x00\x00\x00\x00"


def video_tag(ts_ms, payload=b"\x17\x01\x00\x00\x00deadbeef"):
    data = payload
    head = b"\x09" + len(data).to_bytes(3, "big")
    ts = ts_ms.to_bytes(3, "big") + b"\x00"      # 时间戳低 24 位 + 扩展字节
    stream = b"\x00\x00\x00"
    return head + ts + stream + data + struct.pack(">I", 11 + len(data))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "video/x-flv")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(flv_header())
        ts = 0
        try:
            for i in range(20):
                self.wfile.write(video_tag(ts))
                self.wfile.flush()
                ts += 200
                time.sleep(0.2)
        except Exception:
            pass


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/live.flv" % srv.server_address[1]
    tmp = tempfile.mkdtemp(prefix="livetest_")
    rec = live_record.LiveRecorder("k1", nickname="测试主播", platform="douyin",
                                   url=url, quality="FULL_HD1", quality_label="原画",
                                   dest_dir=tmp)
    ok = rec.start()
    t0 = time.time()
    while time.time() - t0 < 3:
        time.sleep(0.2)
        if rec.running and rec._bytes > 0:
            break
    rec.stop(wait=5, reason="测试结束")
    time.sleep(0.5)
    snap = rec.snapshot()
    size = os.path.getsize(os.path.join(tmp, files[0])) if (files := os.listdir(tmp)) else 0
    srv.shutdown()
    files = os.listdir(tmp)
    flv = [f for f in files if f.endswith(".flv")]
    bad = []
    if not ok:
        bad.append("start 返回 False")
    if not flv:
        bad.append("没有生成 flv")
    if size <= 13:
        bad.append("文件为空")
    if snap.get("state") not in ("录制中", "连接中", "已停止", "已结束", "已保存"):
        bad.append("状态异常：%s" % snap.get("state"))
    tags = 0
    if flv:
        raw = open(os.path.join(tmp, flv[0]), "rb").read()
        off = 13
        while off + 11 <= len(raw):
            dlen = int.from_bytes(raw[off + 1:off + 4], "big")
            off += 11 + dlen + 4
            tags += 1
        if tags < 5:
            bad.append("tag 数太少：%d" % tags)
    print("tags", tags)
    print("url", url, "files", files, "size", size, "state", snap.get("state"),
          "seconds", snap.get("seconds"))
    print("RESULT_OK" if not bad else "RESULT_FAIL " + "; ".join(bad))


if __name__ == "__main__":
    main()