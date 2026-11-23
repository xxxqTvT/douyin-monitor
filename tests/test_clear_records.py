# -*- coding: utf-8 -*-
"""回归：清除下载记录（JSON 记录）与备份旧文件（含历史下载文件夹）。"""
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import douyin_monitor as core  # noqa: E402
import douk_dl  # noqa: E402

CHECKS = []


def check(name, cond, extra=""):
    CHECKS.append((name, bool(cond), str(extra)[:300]))


class FakeDY(object):
    def __init__(self, ids):
        self.ids = ids

    def fetch_all_post_ids(self, sec):
        return list(self.ids)


def main():
    root = tempfile.mkdtemp(prefix="clear_recs_")
    cfg = {"data_dir": root}

    # ---------- ① 清除下载记录：只删这个账号的作品 ----------
    files = os.path.join(root, "Volume", "历史下载", "UID1_测试A_发布作品")
    os.makedirs(files, exist_ok=True)
    with open(os.path.join(files, "a.mp4"), "wb") as fh:
        fh.write(b"x")
    douk_dl.save_records(root, {
        "douyin:1": {"file": os.path.join("Volume", "历史下载", "UID1_测试A_发布作品", "a.mp4")},
        "douyin:2": {"file": os.path.join("Volume", "历史下载", "UID1_测试A_发布作品", "b.mp4")},
        "douyin:9": {"file": "other.mp4"},
    })
    total, deleted = core.clear_download_records(cfg, "SEC", dy=FakeDY(["1", "2"]))
    recs = douk_dl.load_records(root)
    check("清除记录统计", (total, deleted) == (2, 2), (total, deleted))
    check("本账号记录已删", "douyin:1" not in recs and "douyin:2" not in recs, recs)
    check("别的账号记录保留", "douyin:9" in recs, recs)

    # ---------- ② 备份旧文件：历史下载目录 + Download 里按昵称挑 ----------
    dl = os.path.join(root, "Volume", "Download")
    os.makedirs(dl, exist_ok=True)
    with open(os.path.join(dl, "2026-01-01 00.00.00-视频-测试A-标题.mp4"), "wb") as fh:
        fh.write(b"y")
    with open(os.path.join(dl, "2026-01-01 00.00.00-视频-测试B-别人的.mp4"), "wb") as fh:
        fh.write(b"z")
    bdir, nd, nf = core.backup_account_downloads(cfg, "测试A", uid="1")
    check("备份目录存在", bool(bdir) and os.path.isdir(bdir), bdir)
    check("历史下载文件夹被移走", nd == 1, nd)
    check("Download 里本账号文件被移走", nf == 1, nf)
    check("历史下载文件夹已不在", not os.path.isdir(files), files)
    check("别人的文件没动", os.path.isfile(os.path.join(dl, "2026-01-01 00.00.00-视频-测试B-别人的.mp4")))
    check("备份里能找到文件", any(n.endswith("a.mp4") for _d, _s, fs in os.walk(bdir) for n in fs),
          bdir)

    failed = [c for c in CHECKS if not c[1]]
    for name, ok, extra in CHECKS:
        print(("  PASS " if ok else "  FAIL ") + name + ((" " + extra) if not ok else ""))
    print("RESULT_OK" if not failed else "RESULT_FAIL %d %s" % (len(failed), [c[0] for c in failed]))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()