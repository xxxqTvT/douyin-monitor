"""功能回归：① 点「本次下载」的行能定位到文件；② 关闭窗口时按设置隐藏到托盘 / 退出。

只跑在 _t_feat 临时目录，不碰真实配置；打开文件与弹窗都被替换成记录器。
"""
import json
import os
import sys
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douyin_client as client  # noqa: E402
import douyin_monitor as core  # noqa: E402
import douk_dl  # noqa: E402

RESULT = {"checks": [], "ok": True}


def check(name, cond, extra=""):
    RESULT["checks"].append({"name": name, "ok": bool(cond), "extra": str(extra)[:300]})
    if not cond:
        RESULT["ok"] = False


def make_file(path, size=2048):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\x00" * size)
    return path


def main():
    calls = {"file": [], "dir": [], "info": []}
    client.messagebox.showinfo = lambda *a, **k: calls["info"].append(a[1] if len(a) > 1 else a)
    client.messagebox.showwarning = lambda *a, **k: calls["info"].append(a[1] if len(a) > 1 else a)
    client.messagebox.showerror = lambda *a, **k: calls["info"].append(a[1] if len(a) > 1 else a)

    # 这个回归只测面板/关闭行为：把「启动后自动监控」的开关和 toggle 都堵掉，
    # 免得它在断言跑到一半时真的起监控线程去打网络
    client.App.toggle_monitor = lambda _self: None
    root = tk.Tk()
    app = client.App(root)
    root.update_idletasks()

    # 打开动作换成记录器
    opened = {"file": [], "dir": []}
    app.shell_open_file = lambda p: (opened["file"].append(p), (True, ""))[1]
    app.shell_open_dir = lambda p: (opened["dir"].append(p), (True, ""))[1]

    base = os.path.join(HERE, "data", "Volume", "Download")
    vfile = make_file(os.path.join(base, "2026-11-06 18.02.28-视频-测试A-我来也.mp4"))
    i1 = make_file(os.path.join(base, "2026-11-06 18.02.28-图集-测试A-相册_1.jpeg"))
    i2 = make_file(os.path.join(base, "2026-11-06 18.02.28-图集-测试A-相册_2.jpeg"))

    # ---- 1) 面板行 → 文件映射 ----
    app.on_downloaded({
        "nickname": "测试A",
        "items": [
            {"aweme_id": "111", "kind": "视频", "desc": "我来也", "files": [vfile]},
            {"aweme_id": "222", "kind": "图集", "desc": "相册", "files": [i1, i2]},
            {"aweme_id": "333", "kind": "视频", "desc": "被跳过的", "files": []},
        ],
        "files": [vfile, i1, i2],
    })
    root.update_idletasks()
    kids = app.dl_tree.get_children()
    by_id = {app.dl_meta[k]["aweme_id"]: k for k in kids}
    check("面板只插入真下载的 2 行", len(kids) == 2, kids)
    check("每行都有映射", all(k in app.dl_files for k in kids), list(app.dl_files))
    check("视频行=1 个文件", len(app.dl_files[by_id["111"]]) == 1, app.dl_files[by_id["111"]])
    check("图集行=2 个文件", len(app.dl_files[by_id["222"]]) == 2, app.dl_files[by_id["222"]])
    check("之前下过、这次跳过的作品不显示", "333" not in by_id, list(by_id))
    check("面板可见", bool(app.dl_visible), app.dl_visible)

    # ---- 2) 点行打开 ----
    app.open_download_file(by_id["111"])
    check("点视频行打开了 mp4", opened["file"] == [vfile], opened["file"])
    app.open_download_file(by_id["111"])     # 连点：1.5 秒内不重复开
    check("连点不重复打开", len(opened["file"]) == 1, opened["file"])
    app.open_download_file(by_id["222"])
    check("图集打开所在目录", opened["dir"] == [os.path.dirname(i1)], opened["dir"])


    # 选中行 + 「打开所选」
    app.dl_tree.selection_set(by_id["111"])
    opened["file"].clear()
    app._dl_open_stamp = ("", 0.0)
    app.open_download_selected()
    check("打开所选可用", opened["file"] == [vfile], opened["file"])

    # 文件被挪走 → 按同名找回来
    moved_dir = os.path.join(HERE, "data", "Volume", "UID999_测试A_发布作品")
    os.makedirs(moved_dir, exist_ok=True)
    moved = os.path.join(moved_dir, os.path.basename(vfile))
    os.replace(vfile, moved)
    app._dl_open_stamp = ("", 0.0)
    opened["file"].clear()
    app.open_download_file(by_id["111"])
    check("文件搬走后能找回", opened["file"] == [moved], opened["file"])
    os.replace(moved, vfile)

    # ---- 3) _fill_report 的文件认领 ----
    items = [{
        "aweme_id": "900",
        "create_time": 1794999999,
        "desc": "我来也",
        "author": {"nickname": "测试A"},
        "video": {"play_addr": {}},
    }, {
        "aweme_id": "901",
        "create_time": 1794999999,
        "desc": "相册",
        "author": {"nickname": "测试A"},
        "images": [{"url_list": ["x"]}],
    }]
    stamp = douk_dl.item_time(items[0])
    files = [os.path.join(base, "%s-视频-测试A-我来也.mp4" % stamp),
             os.path.join(base, "%s-图集-测试A-相册_1.jpeg" % stamp),
             os.path.join(base, "%s-图集-测试A-相册_2.jpeg" % stamp)]
    for f in files:
        make_file(f)
    rep = {}
    core._fill_report(rep, files, items, {"download_video": True, "download_images": True})
    check("report 有 items", len(rep.get("items") or []) == 2, rep.get("items"))
    check("视频认领 1 个文件", len(rep["items"][0]["files"]) == 1, rep["items"][0]["files"])
    check("图集认领 2 个文件", len(rep["items"][1]["files"]) == 2, rep["items"][1]["files"])
    check("两张作品不串", all("相册" not in f for f in rep["items"][0]["files"]),
          rep["items"][0]["files"])

    # ---- 4) 关闭窗口时：隐藏到托盘（默认） ----
    app.cfg.pop("close_action", None)
    check("默认关闭行为=隐藏到托盘", app.close_action() == "minimize", app.close_action())
    app.close_var.set("minimize")
    app.on_close_action_change()
    root.update()
    check("设置写入配置（隐藏到托盘）", app.cfg.get("close_action") == "minimize", app.cfg.get("close_action"))
    on_disk = json.load(open(os.path.join(HERE, "client_config.json"), encoding="utf-8"))
    check("配置落盘", on_disk.get("close_action") == "minimize", on_disk.get("close_action"))

    app.on_close()          # 等同点 ×
    root.update()
    state = str(root.state())
    check("隐藏到托盘：窗口没被销毁", bool(root.winfo_exists()), state)
    check("隐藏到托盘：状态是 withdrawn/iconic", state in ("withdrawn", "iconic"), state)
    check("隐藏到托盘：监控线程没被停", not app.stop_event.is_set(), app.stop_event.is_set())
    check("隐藏到托盘：录制管理器还在", app.live_mgr is not None, type(app.live_mgr).__name__)

    app.show_from_tray()    # 托盘"显示主界面"
    root.update()
    check("能重新显示", str(root.state()) == "normal", str(root.state()))

    # ---- 4b) 旧的 taskbar 配置按最小化处理（选项已删除）----
    app.cfg["close_action"] = "taskbar"
    check("旧 taskbar 配置按隐藏到托盘处理", app.close_action() == "minimize", app.close_action())
    app.cfg["close_action"] = "exit"
    app.close_var.set("exit")
    app.on_close_action_change()

    # ---- 5) 关闭窗口时：退出 ----
    app.close_var.set("exit")
    app.on_close_action_change()
    root.update()
    check("改回退出", app.close_action() == "exit", app.close_action())
    app.on_close()
    try:
        alive = bool(root.winfo_exists())
    except tk.TclError:
        alive = False
    check("退出：窗口已销毁", not alive, "destroyed" if not alive else "still alive")
    check("退出：stop_event 已置位", app.stop_event.is_set(), app.stop_event.is_set())

    with open(os.path.join(HERE, "_res.json"), "w", encoding="utf-8") as fh:
        json.dump(RESULT, fh, ensure_ascii=False, indent=2)
    print("RESULT_OK" if RESULT["ok"] else "RESULT_FAIL")
    print("checks=%d failed=%d" % (len(RESULT["checks"]),
                                   sum(1 for c in RESULT["checks"] if not c["ok"])))


if __name__ == "__main__":
    main()