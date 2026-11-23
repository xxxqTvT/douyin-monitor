"""回归：内置下载器必须返回真实文件列表，且能认出别的目录里已经下过的文件。

之前的 bug：核账把文件下到账号历史文件夹，但用「最近 15 分钟扫 Download」判断结果，
永远得到 0，于是日志出现「下载 23 个；新增文件 0 个」「下载器没给出新文件」。
"""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douk_dl  # noqa: E402
import douyin_monitor as core  # noqa: E402

RESULT = {"checks": [], "ok": True}


def check(name, cond, extra=""):
    RESULT["checks"].append({"name": name, "ok": bool(cond), "extra": str(extra)[:200]})
    if not cond:
        RESULT["ok"] = False


def video_item(aid, path):
    return {"aweme_id": aid, "desc": "测试作品", "create_time": 1700000000,
            "author": {"nickname": "测试A"},
            "video": {"play_addr": {"url_list": [("file:///" + path.replace("\\", "/"))]}}}


def main():
    root = tempfile.mkdtemp(prefix="dl_test_")
    dest = os.path.join(root, "账号文件夹")
    alt = os.path.join(root, "Download")
    os.makedirs(dest, exist_ok=True)
    os.makedirs(alt, exist_ok=True)
    src = os.path.join(root, "src.mp4")
    with open(src, "wb") as f:
        f.write(b"x" * 4096)

    cfg = {"disk_free_min_mb": 0}
    records = {}

    # ① 正常下载：要返回真实文件路径，stats 与文件数一致
    item = video_item("111", src)
    stats, files = douk_dl.download_items(cfg, [item], "douyin", dest, records=records,
                                          data_root=root)
    check("下载成功返回 1 个文件", len(files) == 1 and os.path.isfile(files[0]), files)
    check("统计与文件数一致", stats.get("下载视频作品") == 1, stats)
    check("写入了下载记录", records.get("douyin:111") is not None, records)

    # ② 目标目录已有同名文件：跳过，不重复下载
    records.clear()
    stats2, files2 = douk_dl.download_items(cfg, [video_item("222", src)], "douyin", dest,
                                            records=records, data_root=root)
    check("目标目录已有就跳过", stats2.get("跳过视频作品") == 1 and not files2, stats2)

    # ③ 文件在别的目录（历史文件夹/Download）：也能认出来，补记录不重下
    name = douk_dl.build_name(video_item("333", src), "video") + ".mp4"
    alt_file = os.path.join(alt, name)
    with open(alt_file, "wb") as f:
        f.write(b"y" * 100)
    records.clear()
    stats3, files3 = douk_dl.download_items(cfg, [video_item("333", src)], "douyin", dest,
                                            records=records, data_root=root, alt_dirs=[alt])
    check("别的目录有文件时跳过下载", stats3.get("跳过视频作品") == 1 and not files3, stats3)
    check("跳过时也补下载记录", records.get("douyin:333") is not None, records)

    # ④ 下载失败不能虚报「下载了 N 个」
    bad = {"aweme_id": "444", "desc": "坏地址", "create_time": 1700000000,
           "author": {"nickname": "测试A"},
           "video": {"play_addr": {"url_list": ["http://127.0.0.1:1/none.mp4"]}}}
    stats4, files4 = douk_dl.download_items(cfg, [bad], "douyin", dest, records={},
                                            data_root=root)
    check("失败时文件为空", files4 == [], files4)
    check("失败时不计入下载数", stats4.get("下载视频作品", 0) == 0, stats4)

    # ⑤ 图集：多个文件都返回、并合并成一个作品记录
    img1 = os.path.join(root, "a.webp")
    img2 = os.path.join(root, "b.webp")
    for p in (img1, img2):
        with open(p, "wb") as f:
            f.write(b"z" * 512)
    slide = {"aweme_id": "555", "desc": "图集作品", "create_time": 1700000000,
             "author": {"nickname": "测试A"},
             "images": [{"url_list": ["file:///" + img1.replace("\\", "/")]},
                        {"url_list": ["file:///" + img2.replace("\\", "/")]}]}
    rec5 = {}
    stats5, files5 = douk_dl.download_items(cfg, [slide], "douyin", dest, records=rec5,
                                            data_root=root)
    check("图集返回 2 个文件", len(files5) == 2, files5)
    check("图集只算 1 个作品", stats5.get("下载图集作品") == 1, stats5)
    check("图集写 1 条记录", rec5.get("douyin:555") is not None, rec5)

    # ⑥ 核账预匹配：只凭作品元数据（不请求详情接口）就能认出本地文件
    v = video_item("666", src)
    dst = os.path.join(root, "历史")
    os.makedirs(dst, exist_ok=True)
    want = os.path.join(dst, douk_dl.build_name(v, "video") + ".mp4")
    with open(want, "wb") as f:
        f.write(b"v" * 256)
    hit = douk_dl.match_name_files(v, "video", [dst])
    check("按文件名认出视频已在本地", hit == [want], hit)

    slide2 = {"aweme_id": "777", "desc": "图集作品", "create_time": 1700000000,
              "author": {"nickname": "测试A"},
              "images": [{"url_list": ["u1"]}, {"url_list": ["u2"]}]}
    stem = douk_dl.build_name(slide2, "image")
    for i in (1, 2):
        with open(os.path.join(dst, "%s_%d.webp" % (stem, i)), "wb") as f:
            f.write(b"i")
    hit2 = douk_dl.match_name_files(slide2, "image", [dst])
    check("按文件名认出图集已在本地", len(hit2) == 2, hit2)

    # ⑦ 新作品其实早就在本地：find_local_files 要能认出来（用于「跳过也下面板」）
    v2 = video_item("888", src)
    hist = core.history_account_dir({"data_dir": root}, {"nickname": "测试A", "unique_id": "1"})
    os.makedirs(hist, exist_ok=True)
    want2 = os.path.join(hist, douk_dl.build_name(v2, "video") + ".mp4")
    with open(want2, "wb") as f:
        f.write(b"z" * 128)
    found = core.find_local_files([(v2, "视频")], {"data_dir": root, "nickname": "测试A", "unique_id": "1"}, "")
    check("新作品文件已在本地时能认出来", found == [want2], found)

    # ⑧ 图集只下了一部分：existing_files 必须判为「不完整」，否则核账会永久跳过
    slide3 = {"aweme_id": "999", "desc": "半套图集", "create_time": 1700000000,
              "author": {"nickname": "测试A"},
              "images": [{"url_list": ["u1"]}, {"url_list": ["u2"]}, {"url_list": ["u3"]}]}
    stem3 = douk_dl.build_name(slide3, "image")
    part = os.path.join(dst, "%s_1.webp" % stem3)
    with open(part, "wb") as f:
        f.write(b"p")
    urls3 = douk_dl.pick_media(slide3)[1]
    hit3 = douk_dl.existing_files(slide3, "image", urls3, [dst])
    check("图集只下了一部分时不算完整", hit3 == [], hit3)
    for i in (2, 3):
        with open(os.path.join(dst, "%s_%d.webp" % (stem3, i)), "wb") as f:
            f.write(b"q")
    hit4 = douk_dl.existing_files(slide3, "image", urls3, [dst])
    check("图集补齐后才算完整", len(hit4) == 3, hit4)

    # 清晰度优先于码率：1440p 档的码率可能比 1080p 低，但分辨率更高
    hi = {"video": {"bit_rate": [
        {"gear_name": "normal_1080_0", "bit_rate": 2261116, "play_addr": {"url_list": ["u1080"]}},
        {"gear_name": "adapt_lowest_1440_1", "bit_rate": 2142446, "play_addr": {"url_list": ["u1440"]}},
    ]}}
    kind, urls = douk_dl.pick_media(hi)
    check("1440p 档优先于 1080p", bool(urls) and urls[0][0] == "u1440", urls)
    four = {"video": {"bit_rate": [
        {"gear_name": "normal_1080_0", "bit_rate": 2559427, "play_addr": {"url_list": ["u1080"]}},
        {"gear_name": "adapt_lowest_4_1", "bit_rate": 3703569, "play_addr": {"url_list": ["u4k"]}},
    ]}}
    kind, urls = douk_dl.pick_media(four)
    check("4K 档优先于 1080p", bool(urls) and urls[0][0] == "u4k", urls)

    bad = [c for c in RESULT["checks"] if not c["ok"]]
    print("RESULT_OK" if RESULT["ok"] else "RESULT_FAIL")
    for c in bad:
        print("  FAIL:", c["name"], c.get("extra"))
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
