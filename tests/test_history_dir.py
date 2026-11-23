# -*- coding: utf-8 -*-
"""历史下载目录：默认/自定义位置、按账号分文件夹（DouK 命名）、迁移与记录同步。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import douyin_monitor as core  # noqa: E402
import douk_dl  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS", name)
    else:
        print("  FAIL", name, detail)
        FAILED.append(name)


def main():
    tmp = tempfile.mkdtemp(prefix="history_dir_")
    cfg = {"data_dir": tmp}
    accounts = [
        {"nickname": "测试A", "unique_id": "123456"},
        {"nickname": "测试B", "unique_id": "654321"},
    ]

    # 1) 默认目录：数据目录\Volume\历史下载
    d = core.history_dir(cfg)
    check("默认历史下载目录", d == os.path.join(tmp, "Volume", "历史下载"), d)

    # 2) 自定义目录
    custom = os.path.join(tmp, "MyHistory")
    check("自定义历史下载目录", core.history_dir(dict(cfg, history_save_dir=custom)) == os.path.abspath(custom))

    # 3) 单账号目录名：DouK 规则 UID<抖音号>_<昵称>_发布作品
    adir = core.history_account_dir(cfg, accounts[0])
    check("账号目录名", os.path.basename(adir) == "UID123456_测试A_发布作品", adir)
    check("账号目录在历史下载下", os.path.dirname(adir) == d)

    # 4) 旧 Download 迁移：按文件名里的昵称归到各自账号目录
    dl = os.path.join(tmp, "Volume", "Download")
    os.makedirs(dl, exist_ok=True)
    name_a = "2026-11-02 00.35.12-视频-测试A-标题A.mp4"
    name_b = "2026-11-02 00.35.13-图集-测试B-标题B_1.webp"
    name_c = "2026-11-02 00.35.14-视频-测试A.mp4"          # 标题为空：昵称后直接跟扩展名
    wrong_dir = os.path.join(d, "测试B_1.webp_发布作品")     # 旧版误按「测试B_1.webp」建的目录
    os.makedirs(wrong_dir, exist_ok=True)
    name_d = "2026-11-02 00.35.15-图集-测试B_1.webp"
    for path, data in ((os.path.join(dl, name_a), b"a" * 100),
                       (os.path.join(dl, name_b), b"b" * 50),
                       (os.path.join(dl, name_c), b"c" * 30),
                       (os.path.join(wrong_dir, name_d), b"d" * 20)):
        with open(path, "wb") as fh:
            fh.write(data)
    records = {
        "douyin:111": {"time": "2026-11-12 10:00:00",
                       "file": os.path.join("Volume", "Download", name_a), "title": "A"},
        "douyin:222": {"time": "2026-11-12 10:00:00",
                       "file": os.path.join("Volume", "Download", name_b), "title": "B"},
        "douyin:333": {"time": "2026-11-12 10:00:00",
                       "file": os.path.join("Volume", "Download", "missing.mp4"), "title": "C"},
    }
    douk_dl.save_records(tmp, records)
    res = core.migrate_history_downloads(cfg, accounts)
    check("迁移文件数", res["moved"] == 4, res)
    check("A 进自己的账号目录", os.path.isfile(os.path.join(adir, name_a)))
    check("标题为空的 A 也进自己的账号目录", os.path.isfile(os.path.join(adir, name_c)))
    check("B 进自己的账号目录",
          os.path.isfile(os.path.join(d, "UID654321_测试B_发布作品", name_b)))
    check("误命名目录里的 B 也被归位",
          os.path.isfile(os.path.join(d, "UID654321_测试B_发布作品", name_d)))
    check("错误命名目录已删除", not os.path.exists(wrong_dir))
    check("Download 已空", not os.listdir(dl))
    recs = douk_dl.load_records(tmp)
    check("记录 A 指向账号目录",
          recs["douyin:111"]["file"] == os.path.join("Volume", "历史下载", "UID123456_测试A_发布作品", name_a),
          recs["douyin:111"]["file"])
    check("记录 B 指向账号目录",
          recs["douyin:222"]["file"] == os.path.join("Volume", "历史下载", "UID654321_测试B_发布作品", name_b),
          recs["douyin:222"]["file"])
    check("缺失文件的记录不变（随后按失效清理）",
          recs["douyin:333"]["file"] == os.path.join("Volume", "Download", "missing.mp4"))

    # 5) 历史下载根目录里的散文件也会被归档
    stray = "2026-11-03 10.00.00-视频-测试A-散落.mp4"
    with open(os.path.join(d, stray), "wb") as fh:
        fh.write(b"c" * 10)
    res2 = core.migrate_history_downloads(cfg, accounts)
    check("散文件被归档", os.path.isfile(os.path.join(adir, stray)), res2)

    # 6) 重复整理无副作用
    res3 = core.migrate_history_downloads(cfg, accounts)
    check("重复整理无副作用", res3["moved"] == 0, res3)

    # 7) 坏记录能被清理识别（相对路径按数据目录解析）
    stale = core.cleanup_data(cfg, days=0, dry_run=True)
    check("坏记录能被识别为失效", stale["records"] == 1, stale)

    # 8) DouK 旧目录（Volume\UID<号>_<昵称>_发布作品）合并进历史下载，同名同大小去重
    tmp2 = tempfile.mkdtemp(prefix="history_legacy_")
    cfg2 = {"data_dir": tmp2}
    a2 = {"nickname": "测试A", "unique_id": "123456"}
    target = core.history_account_dir(cfg2, a2)
    legacy = os.path.join(tmp2, "Volume", "UID123456_测试A_发布作品")
    os.makedirs(target, exist_ok=True)
    os.makedirs(legacy, exist_ok=True)
    dup = "2026-11-04 10.00.00-视频-测试A-重复.mp4"
    uniq = "2026-11-05 10.00.00-视频-测试A-仅旧目录有.mp4"
    for folder, name, data in ((target, dup, b"d" * 64), (legacy, dup, b"d" * 64), (legacy, uniq, b"u" * 32)):
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(data)
    res4 = core.migrate_history_downloads(cfg2, [a2])
    check("旧 Volume 目录里的独有文件被并入", os.path.isfile(os.path.join(target, uniq)), res4)
    check("同名同大小的重复文件被去重", res4.get("dupes") == 1, res4)
    check("合并后旧目录已删除", not os.path.isdir(legacy), legacy)

    if FAILED:
        print("RESULT_FAIL", len(FAILED), FAILED)
        sys.exit(1)
    print("RESULT_OK")


if __name__ == "__main__":
    main()