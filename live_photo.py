# -*- coding: utf-8 -*-
"""把抖音图集里的「实况」片段保存成 Apple 实况照片（Live Photo）配对文件。

抖音的实况图集里，每张实况照片在 `images[i]['video']` 带一段 1.5~3 秒的短片。
本模块做两件事（纯 Python，不依赖 ffmpeg）：
- 把下载到的短片（MP4，H.264/AAC）改写成 QuickTime `.mov`，并写入
  `com.apple.quicktime.content.identifier`（Apple 用它把静图和短片配成一张实况照片）；
- 给同名的 JPEG 静图写入同一标识的 XMP（`apple:ContentIdentifier` /
  `apple:MotionPhoto`），这样 iPhone / macOS「照片」导入后能当作实况照片播放。

配对规则：静图 `..._7.jpg` + 短片 `..._7.mov`（同名不同扩展名），标识一致。
"""

import os
import struct
import uuid

XMP_NS_APPLE = "http://ns.apple.com/1.0/"
XMP_SIG = b"http://ns.adobe.com/xap/1.0/\x00"
KEY_ID = b"com.apple.quicktime.content.identifier"


# ------------------------------------------------------------------ 标识
def content_id(seed=""):
    """给一张实况照片生成稳定的 UUID（同一个作品同一张图每次都一样）。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "douyin-live-photo:%s" % (seed or uuid.uuid4()))).upper()


# ------------------------------------------------------------------ MOV
def _box(kind, payload):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _fullbox(kind, payload, version=0, flags=0):
    return _box(kind, struct.pack(">B3s", version, flags.to_bytes(3, "big")) + payload)


def _iter_boxes(data, start=0, end=None):
    """遍历 ISO BMFF 顶层/子级 box，返回 (type, start, end)。"""
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size = struct.unpack_from(">I", data, pos)[0]
        kind = data[pos + 4:pos + 8]
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            break
        yield kind, pos, pos + size
        pos += size


def _meta_box(identifier):
    """Apple 实况照片标识：moov/udta/meta（keys + ilst）。"""
    hdlr = _fullbox(b"hdlr", struct.pack(">I", 0) + b"mdta" + b"\x00" * 12 + b"\x00")
    keys = _fullbox(b"keys", struct.pack(">I", 1) +
                    struct.pack(">I", 8 + len(KEY_ID)) + b"mdta" + KEY_ID)
    value = identifier.encode("utf-8")
    data_box = _box(b"data", struct.pack(">II", 1, 0) + value)   # flags=1 → UTF-8
    ilst = _box(b"ilst", _box(b"\x00\x00\x00\x01", data_box))
    return _fullbox(b"meta", hdlr + keys + ilst)


def _patch_chunk_offsets(moov, delta):
    """moov 体积变大后，mdat 位置整体后移 delta，stco/co64 里的绝对偏移要跟着加。"""
    if not delta:
        return moov
    buf = bytearray(moov)

    def walk(start, end):
        for kind, s, e in _iter_boxes(bytes(buf), start, end):
            if kind == b"stco":
                _patch_one_offset_box(buf, s, e, delta, 4)
            elif kind == b"co64":
                _patch_one_offset_box(buf, s, e, delta, 8)
            else:
                walk(s + 8, e)

    walk(0, len(buf))
    return bytes(buf)


def _patch_one_offset_box(buf, start, end, delta, width):
    """stco：4 字节偏移；co64：8 字节偏移。"""
    pos = start + 12             # box 头 8 + version/flags 4
    if pos + 4 > end:
        return
    count = struct.unpack_from(">I", buf, pos)[0]
    pos += 4
    fmt = ">I" if width == 4 else ">Q"
    for i in range(count):
        p = pos + i * width
        if p + width > end:
            break
        struct.pack_into(fmt, buf, p, struct.unpack_from(fmt, buf, p)[0] + delta)


def mp4_to_mov(data, identifier):
    """把 MP4 字节改写成 QuickTime 容器并写入实况照片标识，返回新字节。

    Douyin 的短片常自带 udta/meta（cprt、©too 等）：保留原内容，只在 udta 里
    再追加一个带实况标识的 meta，然后按 moov 实际增量修正 stco/co64 偏移。
    """
    boxes = list(_iter_boxes(data))
    if not boxes:
        raise ValueError("不是有效的 MP4 数据")
    moov_box = next((b for b in boxes if b[0] == b"moov"), None)
    if moov_box is None:
        raise ValueError("MP4 里没有 moov（可能是不完整的下载）")
    mdat_box = next((b for b in boxes if b[0] == b"mdat"), None)
    moov_start, moov_end = moov_box[1], moov_box[2]
    meta = _meta_box(identifier)

    body = bytearray()
    has_udta = False
    for kind, s, e in _iter_boxes(data, moov_start + 8, moov_end):
        if kind == b"udta":
            has_udta = True
            inner = bytearray()
            for k2, s2, e2 in _iter_boxes(data, s + 8, e):
                if k2 == b"meta":        # 换掉旧的 meta，避免两个标识互相矛盾
                    continue
                inner += data[s2:e2]
            body += _box(b"udta", bytes(inner) + meta)
        else:
            body += data[s:e]
    if not has_udta:
        body += _box(b"udta", meta)

    delta = (len(body) + 8) - (moov_end - moov_start)
    if delta and (mdat_box is None or mdat_box[1] > moov_start):
        body = bytearray(_patch_chunk_offsets(bytes(body), delta))
    new_moov = _box(b"moov", bytes(body))

    out = bytearray()
    for kind, start, end in boxes:
        if kind == b"ftyp":
            chunk = bytearray(data[start:end])
            if chunk[8:12] != b"qt  ":
                chunk[8:12] = b"qt  "      # 改主品牌，播放器按 QuickTime 处理
            out += bytes(chunk)
        elif kind == b"moov":
            out += new_moov
        else:
            out += data[start:end]
    return bytes(out)


def write_mov(mp4_path, mov_path, identifier):
    data = open(mp4_path, "rb").read()
    open(mov_path, "wb").write(mp4_to_mov(data, identifier))
    return os.path.getsize(mov_path)


# ------------------------------------------------------------------ JPEG
def _xmp_packet(identifier):
    return ("""<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:apple="%s" apple:ContentIdentifier="%s"
   apple:MotionPhoto="1" apple:MotionPhotoVersion="1" apple:MotionPhotoCompatible="1"/>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>""" % (XMP_NS_APPLE, identifier)).encode("utf-8")


def add_xmp_to_jpeg(path, identifier):
    """给 JPEG 插入 XMP（APP1）。已经是 JPEG 才处理；其它格式返回 False。"""
    try:
        data = open(path, "rb").read()
    except OSError:
        return False
    if data[:2] != b"\xff\xd8":
        return False
    packet = XMP_SIG + _xmp_packet(identifier)
    app1 = b"\xff\xe1" + struct.pack(">H", len(packet) + 2) + packet
    if len(packet) + 2 > 0xFFFF:
        return False
    # 插在 SOI 之后、其它段之前
    out = data[:2] + app1 + data[2:]
    tmp = path + ".xmp.tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(out)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def has_xmp_identifier(path):
    """测试/自检用：JPEG 里是否已有 Apple 实况标识。"""
    try:
        return KEY_ID in open(path, "rb").read() or b"apple:ContentIdentifier" in open(path, "rb").read()
    except OSError:
        return False