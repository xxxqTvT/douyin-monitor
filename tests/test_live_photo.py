# -*- coding: utf-8 -*-
"""实况照片保存（图集：静图 + 同名 .mov 配对）回归。"""
import io
import json
import os
import struct
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import douk_dl
import live_photo

checks = [0, 0]


def check(name, cond, extra=""):
    checks[0] += 1
    if cond:
        checks[1] += 1
        print("  ok  %s %s" % (name, extra))
    else:
        print("  FAIL %s %s" % (name, extra))


# ① 标识稳定且是大写 UUID
a1 = live_photo.content_id("123:7")
a2 = live_photo.content_id("123:7")
check("同一作品同一张图的标识稳定", a1 == a2, a1)
check("标识是 36 位大写 UUID", len(a1) == 36 and a1 == a1.upper(), a1)
check("不同图片标识不同", live_photo.content_id("123:8") != a1)


# ② 合成一个最小 MP4（ftyp + moov[trak/stco] + udta + mdat），验证转 MOV 后偏移正确
def build_mp4(first_offset=200, chunk_count=2):
    def box(kind, payload):
        return struct.pack(">I", 8 + len(payload)) + kind + payload

    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 0) + b"isomiso2mp41")
    entries = b"".join(struct.pack(">I", first_offset + i * 16) for i in range(chunk_count))
    stco = box(b"stco", struct.pack(">I", 0) + struct.pack(">I", chunk_count) + entries)
    stbl = box(b"stbl", stco)
    minf = box(b"minf", stbl)
    mdia = box(b"mdia", minf)
    trak = box(b"trak", mdia)
    udta = box(b"udta", box(b"cprt", b"old"))
    moov = box(b"moov", trak + udta)
    mdat_payload = b"\x00" * 64
    mdat = box(b"mdat", mdat_payload)
    return ftyp + moov + mdat, first_offset, chunk_count


def stco_offsets(mov):
    """从生成的 mov 里读回所有 stco 偏移（简化：全文件搜 stco 盒）。"""
    out = []
    pos = mov.find(b"stco")
    while pos != -1:
        start = pos - 4
        size = struct.unpack_from(">I", mov, start)[0]
        if 12 <= size <= 4096:
            count = struct.unpack_from(">I", mov, start + 12)[0]
            if 16 + count * 4 <= start + size:
                out.append([struct.unpack_from(">I", mov, start + 16 + i * 4)[0]
                            for i in range(count)])
        pos = mov.find(b"stco", pos + 4)
    return out


mp4, first, count = build_mp4()
ident = live_photo.content_id("unit:1")
mov = live_photo.mp4_to_mov(mp4, ident)
check("MOV 主品牌改成 qt", mov[8:12] == b"qt  ", mov[8:12])
check("MOV 里写入 Apple 标识", live_photo.KEY_ID in mov)
check("原有 udta 内容保留", b"cprt" in mov)
delta = len(mov) - len(mp4)
offs = stco_offsets(mov)
check("偏移按 moov 增量修正", bool(offs) and offs[0][0] == first + delta,
      "first=%s delta=%s got=%s" % (first, delta, offs[0][:2] if offs else None))
check("第二条偏移也 +delta", bool(offs) and offs[0][1] == first + 16 + delta,
      offs[0][:2] if offs else None)


# ③ 静图是 JPEG 时写入 XMP
tmp = tempfile.mkdtemp(prefix="livephoto_")
jpg = os.path.join(tmp, "a.jpeg")
io.open(jpg, "wb").write(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 40 + b"\xff\xd9")
ok = live_photo.add_xmp_to_jpeg(jpg, ident)
data = io.open(jpg, "rb").read()
check("JPEG 插入 XMP 成功", ok and data[:2] == b"\xff\xd8", data[:2])
check("XMP 里有 ContentIdentifier", b"apple:ContentIdentifier" in data)
check("XMP 里有标识值", ident.encode() in data)
webp = os.path.join(tmp, "b.webp")
io.open(webp, "wb").write(b"RIFF\x00\x00\x00\x00WEBP")
check("非 JPEG 不写 XMP", live_photo.add_xmp_to_jpeg(webp, ident) is False)


# ④ 图集静图优先 JPEG；实况短片能被识别
item = {"images": [
    {"url_list": ["https://x/a.webp", "https://x/a.jpeg"]},
    {"url_list": ["https://x/b.webp"],
     "video": {"duration": 2700, "bit_rate": [
         {"bit_rate": 1000, "width": 720, "height": 1280, "play_addr": {"url_list": ["https://x/v2"]}},
         {"bit_rate": 900, "width": 720, "height": 1280, "play_addr": {"url_list": ["https://x/v2b"]}}]}},
]}
kind, urls = douk_dl.pick_media(item)
check("图集静图优先选 JPEG", urls[0][0] == "https://x/a.jpeg", urls[0][0])
check("没有 JPEG 时退回原地址", urls[1][0] == "https://x/b.webp", urls[1][0])
clips = douk_dl.live_clips(item)
check("识别出 1 张实况", len(clips) == 1, clips)
check("实况序号与静图 _N 对应", clips[0][0] == 2, clips)
check("实况选最高码率地址", clips[0][1] == "https://x/v2", clips)

# ⑤ 无实况的普通图集
plain = {"images": [{"url_list": ["https://x/c.webp"]}]}
check("普通图集没有实况", douk_dl.live_clips(plain) == [])

print("RESULT_%s" % ("OK" if checks[0] == checks[1] else "FAIL"), checks)