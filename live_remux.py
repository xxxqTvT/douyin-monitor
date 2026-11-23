# -*- coding: utf-8 -*-
"""把录下来的 FLV 直播流重新封装成 MP4（纯 Python，不依赖 ffmpeg）。

抖音/TikTok 的 FLV 拉流是标准 FLV（H.264 + AAC）。录完直接留 .flv 的话，
Windows 自带播放器打不开；这里把 FLV 的 tag 拆出来，按 MP4 的
ftyp/moov/mdat 结构重新封装成 .mp4，H.264/AAC 不用转码，几乎瞬间完成。

只支持 AVC(H.264) + AAC；遇到 HEVC 或其它编码原样保留 .flv，不破坏录像。
"""

import struct

VIDEO_CODECS = {7: "H.264"}
AUDIO_CODECS = {10: "AAC"}

FREQ_TABLE = [96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050,
              16000, 12000, 11025, 8000, 7350]

_CHUNK = 1 << 20


# ------------------------------------------------------------------ FLV 解析
class _Video(object):
    __slots__ = ("dts", "ts", "cts", "key", "data")


class _Audio(object):
    __slots__ = ("dts", "data")


def _amf_dims(body):
    """从 FLV onMetaData 里取 width/height（AMF0 number）"""
    dims = {}
    for name in ("width", "height"):
        key = b"\x00" + struct.pack(">H", len(name)) + name.encode()
        pos = body.find(key)
        if pos >= 0 and pos + len(key) + 9 <= len(body) and body[pos + len(key)] == 0x00:
            dims[name] = int(struct.unpack(">d", body[pos + len(key) + 1:
                                                  pos + len(key) + 9])[0])
    return dims.get("width") or 0, dims.get("height") or 0


def _read_flv(path):
    """返回 (avcC 配置, aac 配置, 视频样本列表, 音频样本列表)"""
    avcc = b""
    asc = b""
    width = height = 0
    videos = []
    audios = []
    with open(path, "rb") as fh:
        head = fh.read(13)
        if len(head) < 13 or head[0:3] != b"FLV":
            raise ValueError("不是 FLV 文件")
        buf = bytearray()
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            buf += chunk
            while len(buf) >= 11:
                tag_type = buf[0] & 0x1F
                size = (buf[1] << 16) | (buf[2] << 8) | buf[3]
                ts = ((buf[4] << 16) | (buf[5] << 8) | buf[6]) | (buf[7] << 24)
                total = 11 + size + 4
                if len(buf) < total:
                    break
                body = bytes(buf[11:11 + size])
                del buf[:total]
                if tag_type == 9 and size >= 5:
                    if body[0] not in (0x17, 0x27):      # 只认 AVC
                        continue
                    if body[1] == 0:                    # AVC sequence header
                        var = _Video()
                        var.dts = ts
                        var.cts = 0
                        var.key = True
                        var.data = body[5:]
                        if len(var.data) > 7 and not avcc:
                            avcc = var.data
                        continue
                    var = _Video()
                    var.dts = ts
                    cts = (body[2] << 16) | (body[3] << 8) | body[4]
                    if cts & 0x800000:
                        cts -= 0x1000000
                    var.cts = cts
                    var.key = (body[0] & 0x0F) == 1
                    var.data = body[5:] if len(body) > 5 else b""
                    if var.data:
                        videos.append(var)
                elif tag_type == 8 and size >= 2:
                    if (body[0] >> 4) != 10:            # 只认 AAC
                        continue
                    if body[1] == 0:
                        if not asc:
                            asc = body[2:]
                        continue
                    var = _Audio()
                    var.dts = ts
                    var.data = body[2:]     # [0]=编码/采样率, [1]=AACPacketType, 之后是裸 AAC 帧
                    if var.data:
                        audios.append(var)
                elif tag_type == 18:
                    w, h = _amf_dims(body)
                    width = width or w
                    height = height or h
    return avcc, asc, width, height, videos, audios


# ------------------------------------------------------------------ MP4 写入
def _box(kind, payload=b""):
    return struct.pack(">I", len(payload) + 8) + kind + payload


def _full(kind, version, flags, payload=b""):
    return _box(kind, struct.pack(">B", version) + struct.pack(">I", flags)[1:] + payload)


def _matrix():
    return struct.pack(">9i", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000)


def _stts(deltas):
    entries = []
    for d in deltas:
        d = max(0, int(d))
        if entries and entries[-1][1] == d:
            entries[-1][0] += 1
        else:
            entries.append([1, d])
    body = struct.pack(">I", len(entries))
    for n, d in entries:
        body += struct.pack(">II", n, d)
    return _full(b"stts", 0, 0, body)


def _ctts(offsets):
    entries = []
    for o in offsets:
        o = int(o)
        if entries and entries[-1][1] == o:
            entries[-1][0] += 1
        else:
            entries.append([1, o])
    body = struct.pack(">I", len(entries))
    for n, o in entries:
        body += struct.pack(">Ii", n, o)
    return _full(b"ctts", 0, 0, body)


def _stsz(sizes):
    body = struct.pack(">II", 0, len(sizes))
    for s in sizes:
        body += struct.pack(">I", int(s))
    return _full(b"stsz", 0, 0, body)


def _stco(offset):
    return _full(b"stco", 0, 0, struct.pack(">II", 1, int(offset)))


def _stsc(count):
    return _full(b"stsc", 0, 0, struct.pack(">IIIII", 1, 1, int(count), 1, 1))


def _stss(samples):
    body = struct.pack(">I", len(samples))
    for n in samples:
        body += struct.pack(">I", int(n))
    return _full(b"stss", 0, 0, body)


def _avc1(avcc, width, height):
    name = b"\x00" * 32
    payload = (b"\x00" * 6 + struct.pack(">H", 1) +
               struct.pack(">HH", 0, 0) + b"\x00" * 12 +
               struct.pack(">HH", max(1, width), max(1, height)) +
               struct.pack(">II", 0x00480000, 0x00480000) + b"\x00" * 4 +
               struct.pack(">H", 1) + name + struct.pack(">H", 0x0018) +
               struct.pack(">H", 0xFFFF) +
               _box(b"avcC", avcc))
    return _box(b"avc1", payload)


def _esds(asc):
    def desc(tag, payload):
        return struct.pack(">BB", tag, len(payload)) + payload

    dec = (struct.pack(">B", 0x40) + struct.pack(">B", 0x15) +
           b"\x00\x00\x00" + struct.pack(">II", 128000, 128000) +
           desc(0x05, asc))
    es = (struct.pack(">H", 1) + b"\x00" + desc(0x04, dec) + desc(0x06, b"\x02"))
    return _full(b"esds", 0, 0, desc(0x03, es))


def _mp4a(asc, rate, channels):
    payload = (b"\x00" * 6 + struct.pack(">H", 1) +
               struct.pack(">HH", 0, 0) + b"\x00" * 4 +
               struct.pack(">HH", channels, 16) + b"\x00" * 4 +
               struct.pack(">I", rate << 16) + _esds(asc))
    return _box(b"mp4a", payload)


def _trak(kind, track_id, timescale, duration, stbl, width=0, height=0, volume=0):
    tkhd_payload = (struct.pack(">IIIII", 0, 0, track_id, 0, duration) +
                    b"\x00" * 8 + struct.pack(">hhhH", 0, 0, volume, 0) +
                    _matrix() +
                    struct.pack(">II", width << 16, height << 16))
    tkhd = _full(b"tkhd", 0, 3, tkhd_payload)
    mdhd = _full(b"mdhd", 0, 0,
                 struct.pack(">IIII", 0, 0, timescale, duration) +
                 struct.pack(">HH", 0x55C4, 0))
    handler = b"vide" if kind == "video" else b"soun"
    hdlr = _full(b"hdlr", 0, 0, b"\x00" * 4 + handler + b"\x00" * 12 +
                 (b"VideoHandler\x00" if kind == "video" else b"SoundHandler\x00"))
    if kind == "video":
        minf_head = _full(b"vmhd", 0, 1, b"\x00" * 8)
    else:
        minf_head = _full(b"smhd", 0, 0, b"\x00" * 4)
    dinf = _box(b"dinf", _full(b"dref", 0, 0,
                               struct.pack(">I", 1) + _full(b"url ", 0, 1)))
    minf = _box(b"minf", minf_head + dinf + stbl)
    mdia = _box(b"mdia", mdhd + hdlr + minf)
    return _box(b"trak", tkhd + mdia)


def audio_stts_durations(audios, rate):
    """按 FLV 里音频 tag 的真实时间戳算每个 AAC 帧占多久（单位：采样数）。

    直播里网络卡一下会丢若干音频包，如果一律按 1024 采样（约 23.2ms）累加，
    音频轨道会比视频短，越到后面音画差得越多（42 分钟的录像能差 5 秒）。
    这里用相邻 tag 的时间差还原真实时长，中间的空档由播放器按时间戳补静音。
    """
    out = []
    for i, a in enumerate(audios):
        if i + 1 < len(audios):
            delta = audios[i + 1].dts - a.dts
        else:
            delta = 1024.0 * 1000.0 / rate
        samples = int(round(delta * rate / 1000.0))
        out.append(max(1, samples))
    return out


def remux_flv_to_mp4(src, dst):
    """FLV → MP4。成功返回 True（dst 生成），失败抛 ValueError/OSError。"""
    avcc, asc, width, height, videos, audios = _read_flv(src)
    if not avcc or not videos:
        raise ValueError("没有 H.264 视频数据，无法转 MP4")
    if not asc and audios:
        raise ValueError("音频不是 AAC，无法转 MP4")
    base = min(videos[0].dts, audios[0].dts if audios else videos[0].dts)
    # 负 composition offset 用整体平移 DTS 的方式消掉
    vcts = [v.cts for v in videos]
    shift = -min(0, min(vcts)) if vcts else 0
    deltas = []
    prev = None
    for v in videos:
        dts = v.dts - base + shift
        v.ts = dts
        if prev is not None:
            deltas.append(max(0, dts - prev))
        prev = dts
    if deltas:
        deltas.append(deltas[-1] or 40)
    else:
        deltas.append(40)
    vdur = (videos[-1].ts - videos[0].ts) + (deltas[-1] if deltas else 40) if videos else 0
    vsizes = [len(v.data) for v in videos]
    vkey = [i + 1 for i, v in enumerate(videos) if v.key]

    audio_rate = 44100
    channels = 2
    if asc:
        try:
            obj = (asc[0] >> 3) & 0x1F
            idx = ((asc[0] & 0x07) << 1) | ((asc[1] >> 7) & 0x01)
            if 0 <= idx < len(FREQ_TABLE):
                audio_rate = FREQ_TABLE[idx]
            channels = (asc[1] >> 3) & 0x0F or 2
            if obj not in (1, 2, 3, 4):
                raise ValueError("不支持的 AAC profile")
        except IndexError:
            raise ValueError("AAC 配置损坏")
    adurs = audio_stts_durations(audios, audio_rate) if audios else []
    adur = max(1, sum(adurs)) if adurs else 1
    asizes = [len(a.data) for a in audios]

    vstbl = _box(b"stbl",
                 _full(b"stsd", 0, 0, struct.pack(">I", 1) + _avc1(avcc, width, height)) +
                 _stts(deltas) + _ctts(vcts) + _stsc(len(videos)) +
                 _stsz(vsizes) + _stco(0) +
                 (_stss(vkey) if vkey else b""))
    astbl = b""
    if audios:
        astbl = _box(b"stbl",
                     _full(b"stsd", 0, 0, struct.pack(">I", 1) +
                           _mp4a(asc, audio_rate, channels)) +
                     _stts(adurs) + _stsc(len(audios)) +
                     _stsz(asizes) + _stco(0))

    def build_moov(video_off, audio_off):
        mvhd = _full(b"mvhd", 0, 0,
                     struct.pack(">IIII", 0, 0, 1000, max(vdur, 1)) +
                     struct.pack(">I", 0x00010000) + struct.pack(">H", 0x0100) +
                     b"\x00" * 10 + _matrix() + b"\x00" * 24 +
                     struct.pack(">I", 3))
        trak_v = _trak("video", 1, 1000, max(vdur, 1),
                       vstbl.replace(_stco(0), _stco(video_off)),
                       width=width, height=height)
        traks = trak_v
        if audios:
            traks += _trak("audio", 2, audio_rate, adur,
                           astbl.replace(_stco(0), _stco(audio_off)),
                           volume=0x0100)
        return _box(b"moov", mvhd + traks)

    ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 0x200) +
                b"isom" + b"iso2" + b"avc1" + b"mp41")
    probe = build_moov(0, 0)
    mdat_start = len(ftyp) + len(probe) + 8
    video_off = mdat_start
    audio_off = mdat_start + sum(vsizes)
    moov = build_moov(video_off, audio_off)
    if len(moov) != len(probe):
        raise ValueError("MP4 头长度不稳定")

    mdat_payload = b"".join(v.data for v in videos) + b"".join(a.data for a in audios)
    with open(dst, "wb") as fh:
        fh.write(ftyp)
        fh.write(moov)
        fh.write(struct.pack(">I", len(mdat_payload) + 8) + b"mdat")
        fh.write(mdat_payload)
    return True