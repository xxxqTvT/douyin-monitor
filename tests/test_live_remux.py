# -*- coding: utf-8 -*-
"""直播录像：FLV → MP4 重封装自检（不联网、不依赖 ffmpeg）。

构造一个最小 FLV（H.264 序列头 + 2 个关键帧/普通帧 + AAC 序列头 + 2 帧），
跑 live_remux.remux_flv_to_mp4，再把 MP4 的 box 结构读回来核对。
最后验证 LiveRecorder._finalize 会把 .flv 换成 .mp4。
"""
import io
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import live_record  # noqa: E402
import live_remux  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS", name)
    else:
        print("  FAIL", name, detail)
        FAILED.append(name)


def tag(tag_type, ts, body):
    return (bytes([tag_type]) +
            struct.pack(">I", len(body))[1:] +
            struct.pack(">I", ts)[1:] + bytes([(ts >> 24) & 0xFF]) +
            b"\x00\x00\x00" + body + struct.pack(">I", 11 + len(body)))


def amf_meta(width, height):
    body = bytearray(b"\x02")
    name = b"onMetaData"
    body += struct.pack(">H", len(name)) + name
    body += b"\x08" + struct.pack(">I", 2)
    for k, v in (("width", width), ("height", height)):
        body += struct.pack(">H", len(k)) + k.encode() + b"\x00" + struct.pack(">d", float(v))
    return bytes(body)


def build_flv(path):
    avcc = bytes([1, 100, 0, 40, 0xFF, 0xE1, 0x00, 0x05, 0x67, 0x64, 0x00, 0x28, 0xAC])
    out = bytearray(b"FLV\x01\x05\x00\x00\x00\x09\x00\x00\x00\x00")
    out += tag(18, 0, amf_meta(1920, 1080))
    out += tag(9, 0, b"\x17\x00\x00\x00\x00" + avcc)
    out += tag(8, 0, b"\xAF\x00" + b"\x11\x90")
    for i, (key, pts) in enumerate(((True, 0), (False, 40))):
        nalu = b"\x00\x00\x00\x03" + (b"\x65" if key else b"\x41") + b"\x88\x84\x00" + bytes([i])
        body = bytes([0x17 if key else 0x27, 0x01]) + pts.to_bytes(3, "big") + nalu
        out += tag(9, i * 40, body)
    for i in range(1400):
        out += tag(8, 80 + i * 21, b"\xAF\x01" + b"\x21\x10\x05" + bytes([i & 0xFF]) * 30)
    with open(path, "wb") as fh:
        fh.write(out)
    return path


def boxes(data, off=0, end=None, out=None):
    end = len(data) if end is None else end
    out = [] if out is None else out
    while off + 8 <= end:
        size, kind = struct.unpack(">I4s", data[off:off + 8])
        if size < 8:
            break
        out.append((kind, off, size))
        if kind in (b"moov", b"trak", b"mdia", b"minf", b"stbl", b"dinf", b"stsd"):
            boxes(data, off + 8, off + size, out)
        off += size
    return out


def main():
    tmp = tempfile.mkdtemp(prefix="live_remux_")
    src = build_flv(os.path.join(tmp, "a.flv"))
    dst = os.path.join(tmp, "a.mp4")
    ok = live_remux.remux_flv_to_mp4(src, dst)
    check("返回 True", ok is True)
    check("生成了 MP4 且非空", os.path.isfile(dst) and os.path.getsize(dst) > 100)
    data = io.open(dst, "rb").read()
    kinds = [b[0] for b in boxes(data)]
    for need in (b"ftyp", b"moov", b"mvhd", b"mdat", b"trak", b"stsd"):
        check("有 %s box" % need.decode(), need in kinds)
    for need in (b"avcC", b"mp4a", b"esds"):
        check("有 %s box" % need.decode(), need in data)
    counts = []
    for kind, off, size in boxes(data):
        if kind == b"stsz":
            counts.append(struct.unpack(">I", data[off + 16:off + 20])[0])
    check("stsz 两份（视频+音频）", len(counts) == 2, counts)
    check("视频样本 2 个", counts and counts[0] == 2, counts)
    check("音频样本 1400 个", len(counts) == 2 and counts[1] == 1400, counts)
    mdat = [b for b in boxes(data) if b[0] == b"mdat"][0]
    check("mdat 长度覆盖尾部", mdat[1] + mdat[2] == len(data), (mdat, len(data)))

    # LiveRecorder._finalize：录完自动把 flv 换成 mp4
    rec = live_record.LiveRecorder("k1", "测试主播", "http://example.invalid/live.flv",
                                   dest_dir=tmp, quality="FULL_HD1",
                                   quality_label="原画")
    shutil_copy = os.path.join(tmp, "rec.flv")
    with open(src, "rb") as a, open(shutil_copy, "wb") as b:
        b.write(a.read())
    rec.file = shutil_copy
    rec.state = "已结束"
    rec._finalize()
    check("_finalize 后 file 是 .mp4", rec.file.endswith(".mp4"), rec.file)
    check("_finalize 删掉了 .flv", not os.path.exists(shutil_copy))
    check("_finalize 产出的 MP4 存在", os.path.isfile(rec.file))


    # 音频丢包时：按 FLV tag 时间差算时长，而不是一律 1024 采样
    class _A:
        def __init__(self, dts):
            self.dts = dts

    durs = live_remux.audio_stts_durations([_A(0), _A(23), _A(1000)], 44100)
    check("音频首帧按 23ms 折算", durs[0] == round(23 * 44100 / 1000.0), durs)
    check("音频丢包空档按真实时间折算", durs[1] > 40000, durs)
    check("最后一帧按 1024 采样", durs[2] == 1024, durs)

    if FAILED:
        print("RESULT_FAIL", len(FAILED), FAILED)
        sys.exit(1)
    print("RESULT_OK")


if __name__ == "__main__":
    main()