# -*- coding: utf-8 -*-
"""批量下载：下载进度回调；记录还在但文件没了要重新下载。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import douk_dl  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS", name)
    else:
        print("  FAIL", name, detail)
        FAILED.append(name)


def main():
    tmp = tempfile.mkdtemp(prefix="dl_progress_")
    root = tmp
    dest = os.path.join(tmp, "Volume", "Download")
    os.makedirs(dest, exist_ok=True)
    # 2 号作品的记录指向真实存在的文件 → 应跳过；1 号记录指向不存在的文件 → 应重新下载
    existing = os.path.join(dest, "old.mp4")
    with open(existing, "wb") as fh:
        fh.write(b"x" * 10)
    records = {
        "douyin:1": {"file": os.path.join("Volume", "Download", "gone.mp4"), "title": "gone"},
        "douyin:2": {"file": os.path.join("Volume", "Download", "old.mp4"), "title": "old"},
    }

    picked = {"n": 0}

    def fake_pick(item):
        return ("video", ["http://example/fake.mp4"])

    def fake_download(item, kind, dest_dir, headers, timeout=60, save_live=True):
        picked["n"] += 1
        p = os.path.join(dest_dir, "new%s.mp4" % item.get("aweme_id"))
        with open(p, "wb") as fh:
            fh.write(b"y" * 20)
        return [p], ""

    orig_pick, orig_dl = douk_dl.pick_media, douk_dl.download_item
    douk_dl.pick_media = fake_pick
    douk_dl.download_item = fake_download
    try:
        calls = []
        cfg = {"data_dir": root, "disk_free_min_mb": 0}
        items = [{"aweme_id": "1"}, {"aweme_id": "2"}]
        stats, files = douk_dl.download_items(
            cfg, items, "douyin", dest, records=records, data_root=root,
            progress=lambda done, total: calls.append((done, total)))
    finally:
        douk_dl.pick_media, douk_dl.download_item = orig_pick, orig_dl

    check("记录文件丢失 → 重新下载", stats.get("下载视频作品") == 1, stats)
    check("记录文件存在 → 跳过", stats.get("跳过视频作品") == 1, stats)
    check("实际只下载了 1 个", picked["n"] == 1, picked)
    check("进度回调覆盖全部作品", calls == [(1, 2), (2, 2)], calls)
    check("记录已更新为新文件", os.path.exists(os.path.join(root, records["douyin:1"]["file"])),
          records["douyin:1"])

    if FAILED:
        print("RESULT_FAIL", len(FAILED), FAILED)
        sys.exit(1)
    print("RESULT_OK")


if __name__ == "__main__":
    main()