"""回归：单独下载链接也进「本次下载」面板；左下角同时显示作品/直播倒计时。"""
import os
import sys
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douyin_client as client  # noqa: E402
import douyin_monitor as core  # noqa: E402

RESULT = {"checks": [], "ok": True}


def check(name, cond, extra=""):
    RESULT["checks"].append({"name": name, "ok": bool(cond), "extra": str(extra)[:300]})
    if not cond:
        RESULT["ok"] = False


def pump(root, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        root.update()
        time.sleep(0.02)


def main():
    tmp = os.path.join(HERE, "data", "_link_panel_tmp")
    os.makedirs(tmp, exist_ok=True)
    names = ["2026-01-01 00.00.00-图集-测试A-标题_1.webp",
             "2026-01-01 00.00.00-图集-测试A-标题_2.webp",
             "2026-01-02 00.00.00-视频-测试A-另一个标题.mp4"]
    files = []
    for name in names:
        p = os.path.join(tmp, name)
        with open(p, "wb") as fh:
            fh.write(b"x" * 16)
        files.append(p)

    old_dl = core.download_links
    root = None
    try:
        core.download_links = lambda cfg, links, timeout=None: (
            {"下载图集作品": 1, "下载视频作品": 1}, list(files), "fake")
        root = tk.Tk()
        client.App.toggle_monitor = lambda *_a, **_k: None
        client.App.cookie_ready = lambda *_a, **_k: True
        app = client.App(root)
        pump(root, 0.2)

        # ① 单独下载链接 -> 「本次下载」面板
        app._link_worker(["https://www.douyin.com/video/1"],
                         dict(app.cfg, save_dir=""))
        pump(root, 0.9)
        kids = app.dl_tree.get_children() if app.dl_tree is not None else []
        check("单独下载在面板里出现 2 条作品", len(kids) == 2, len(kids))
        check("图集两个文件合成一行（总文件 3）", app.dl_total_files == 3, app.dl_total_files)
        check("统计文字改成「本次已下载」", "本次已下载" in str(app.dl_sum_var.get()),
              app.dl_sum_var.get())
        check("有下载时面板自动显示", app.dl_visible is True)
        kinds = [app.dl_tree.item(k, "values")[2] for k in kids]
        check("面板里能看到图集/视频类型", "图集" in kinds and "视频" in kinds, kinds)

        # ①b 之前下过、这次跳过的作品不能出现在面板里
        before = len(app.dl_tree.get_children())
        app.on_downloaded({"nickname": "账号X", "files": [],
                           "items": [{"kind": "视频", "desc": "旧作品", "files": []}]})
        check("跳过的作品不进入本次下载面板", len(app.dl_tree.get_children()) == before,
              len(app.dl_tree.get_children()))
        app.on_downloaded({"nickname": "账号X", "files": [files[2]],
                           "items": [{"kind": "视频", "desc": "新作品", "files": [files[2]]}]})
        check("真下载的作品仍然显示", len(app.dl_tree.get_children()) == before + 1,
              len(app.dl_tree.get_children()))

        # ② 左下角倒计时
        app.run_cfg = {"live": True, "live_separate": True}
        app.next_check_ts = time.time() + 65
        app.next_live_ts = time.time() + 30
        txt = app.countdown_text()
        check("倒计时含「下次作品检查」", "下次作品检查" in txt, txt)
        check("倒计时含「下次直播检查」", "下次直播检查" in txt, txt)
        check("倒计时是 mm:ss 格式", "01:0" in txt and "00:3" in txt, txt)
        app.run_cfg = {"live": True, "live_separate": False}
        app.next_check_ts = 0
        app.next_live_ts = 0
        txt2 = app.countdown_text()
        check("未排期时显示进行中/随作品轮", "作品检查进行中" in txt2 and "随作品轮" in txt2, txt2)
    finally:
        core.download_links = old_dl
        if root is not None:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass

    bad = [c for c in RESULT["checks"] if not c["ok"]]
    print("RESULT_OK" if RESULT["ok"] else "RESULT_FAIL")
    for c in bad:
        print("  FAIL:", c["name"], c.get("extra"))
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
