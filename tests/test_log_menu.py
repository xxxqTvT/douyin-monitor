"""回归：日志文案/显示（中文级别、折叠、过滤）+ 账号列表右键菜单。

全部离线：只建界面、造两条假账号，不联网。
"""
import io
import json
import logging
import os
import sys
import time
import tkinter as tk
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douyin_client as client  # noqa: E402
import douyin_monitor as core  # noqa: E402

RESULT = {"checks": [], "ok": True}
CFG = os.path.join(HERE, "client_config.json")


def check(name, cond, extra=""):
    RESULT["checks"].append({"name": name, "ok": bool(cond), "extra": str(extra)[:300]})
    if not cond:
        RESULT["ok"] = False


def pump(root, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        root.update()
        time.sleep(0.02)


def set_cfg(**kw):
    cfg = json.load(io.open(CFG, encoding="utf-8"))
    cfg.update(kw)
    io.open(CFG, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))


def log_lines(app):
    return [ln for ln in app.log_text.get("1.0", "end-1c").split("\n") if ln]


class FakeMenu:
    """替掉 tk.Menu：只记录 popup 的菜单项，不真的弹窗"""
    captured = []

    def __init__(self, master=None, **kw):
        self.items = []
        self.labels = []

    def add_command(self, label="", state="normal", command=None, **kw):
        self.items.append(("command", label, state))

    def add_separator(self, **kw):
        self.items.append(("sep", "", "normal"))

    def add_checkbutton(self, label="", **kw):
        self.items.append(("check", label, "normal"))

    def tk_popup(self, x, y):
        FakeMenu.captured.append(self)

    def grab_release(self):
        pass


def main():
    set_cfg(platform_chosen=True, auto_monitor=False, tray_enabled=False, notify_enabled=False)
    root = tk.Tk()
    orig_toggle = client.App.toggle_monitor
    client.App.toggle_monitor = lambda _self: None
    app = client.App(root)
    root.update_idletasks()
    # 本节测的是「详细日志」的排版/折叠/过滤，先关掉默认概要过滤
    app.log_summary_only = False
    app.clear_log_view()

    # ---------------- ① 日志：中文级别 + 折叠 + 过滤 ----------------
    app.append_log("23:39:12 [信息] 【测试A】检查完成：没有新作品")
    lines = log_lines(app)
    check("日志能写入面板", len(lines) == 1, lines)
    check("信息行不再挂「[信息]」标签（级别靠颜色区分）",
          "[信息]" not in lines[0], lines[0])

    tags = app.log_text.tag_names("1.0")
    check("信息行不上色", "err" not in tags and "warn" not in tags, tags)

    app.append_log("23:39:12 [注意] 【测试A】还没有登录 抖音 的 Cookie")
    check("注意行用 warn 颜色", "warn" in app.log_text.tag_names("2.0"),
          app.log_text.tag_names("2.0"))
    app.append_log("23:39:13 [错误] 账号 测试A 检查失败：网络错误")
    check("错误行用 err 颜色", "err" in app.log_text.tag_names("3.0"),
          app.log_text.tag_names("3.0"))
    app.append_log("23:39:14 [信息] 下载结束：新增 2 个文件")
    check("成功语汇用 ok 颜色", "ok" in app.log_text.tag_names("4.0"),
          app.log_text.tag_names("4.0"))

    # 连续重复折叠
    before = len(log_lines(app))
    for _ in range(4):
        app.append_log("23:39:20 [信息] 直播状态：未开播")
    after = log_lines(app)[-1]
    check("连续重复折叠成一行", len(log_lines(app)) == before + 1, len(log_lines(app)))
    check("折叠行标出重复次数", "重复 ×4" in after, after)

    # 只看提醒/错误
    app.log_warn_only_var.set(True)
    app.log_warn_only = True
    n0 = len(log_lines(app))
    app.append_log("23:40:00 [信息] 这条应该被过滤掉")
    check("只看提醒时过滤信息行", len(log_lines(app)) == n0, len(log_lines(app)))
    app.append_log("23:40:01 [注意] 这条要留下")
    check("只看提醒时保留注意行", len(log_lines(app)) == n0 + 1, log_lines(app)[-1])
    app.log_warn_only_var.set(False)
    app.log_warn_only = False

    app.clear_log_view()
    check("清空日志会清掉折叠状态", app._log_n == 0 and log_lines(app) == [])

    # 默认概要模式：只留检测/下载/直播事件，技术细节不刷屏
    app.log_summary_only = True
    app.append_log("23:41:00 [信息] 作品列表接口冷却 180 秒")
    check("概要模式过滤技术细节", log_lines(app) == [], log_lines(app))
    app.append_log("23:41:01 [信息] 第 7 轮检查完成：没有新作品（用时 3 秒，下一轮 5 分钟后）")
    check("概要模式保留检测结果", len(log_lines(app)) == 1, log_lines(app))
    app.append_log("23:41:02 [信息] 下载完成：新增 2 个文件")
    check("概要模式保留下载结果", len(log_lines(app)) == 2, log_lines(app))
    app.append_log("23:41:03 [信息] 开始录制：测试主播（原画）")
    check("概要模式保留直播录制", len(log_lines(app)) == 3, log_lines(app))
    app.log_summary_only = False

    # ---------------- ② 一轮汇总：不再每账号刷「暂无更新」 ----------------
    records = []

    class Cap(logging.Handler):
        def emit(self, rec):
            records.append(rec.getMessage())

    cap = Cap()
    core.log.addHandler(cap)
    core.log.setLevel(logging.INFO)

    app.accounts = [
        {"iid": "i1", "target": "账号一", "nickname": "测试A", "enabled": True,
         "platform": "douyin", "sec_user_id": "SEC_A"},
        {"iid": "i2", "target": "账号二", "nickname": "测试B", "enabled": True,
         "platform": "douyin", "sec_user_id": "SEC_B"},
    ]
    app.refresh_tree()
    root.update_idletasks()

    app.check_account = lambda dy, acc, **_k: {"items": 0, "files": 0, "live": False}
    app.get_client = lambda platform=None: SimpleNamespace(reload_cookie=lambda: None)
    app.cycle_count = 7
    stats = app.monitor_cycle()
    check("一轮返回统计", stats.get("checked") == 2 and stats.get("new") == 0, stats)

    app.log_round_summary(stats, cost=3.2, wait=300)
    blob = "\n".join(records)
    check("有轮次汇总行", "第 7 轮检查完成" in blob, blob[-200:])
    check("汇总行说明没有新作品", "没有新作品" in blob, blob[-200:])
    check("汇总行给出下一轮时间", "下一轮" in blob, blob[-200:])
    check("不再逐账号写「暂无更新」", "暂无更新" not in blob, blob[-200:])
    core.log.removeHandler(cap)

    # ---------------- ③ 账号列表右键菜单 ----------------
    FakeMenu.captured.clear()
    orig_menu = client.tk.Menu
    client.tk.Menu = FakeMenu
    try:
        bbox = app.tree.bbox("i1")
        check("表格有行几何", bool(bbox), bbox)
        ev = SimpleNamespace(x=bbox[0] + 40, y=bbox[1] + bbox[3] // 2,
                             x_root=600, y_root=400)
        app.tree.selection_remove(*app.tree.selection())
        app.on_tree_context(ev)
        check("右键会弹出菜单", len(FakeMenu.captured) == 1, len(FakeMenu.captured))
        menu = FakeMenu.captured[0]
        labels = [l for _t, l, _s in menu.items]
        for want in ("打开主页", "复制主页链接", "复制昵称与账号", "刷新（直播 + 新作品）",
                     "核账历史作品（只报告缺什么，不下载）", "下载历史作品…",
                     "补下载最近 3 天的漏项…", "设置保存位置…", "打开作品目录",
                     "监控作品", "监控直播", "作品和直播", "跟全局",
                     "暂停监控", "删除账号…"):
            check("右键菜单有「%s」" % want, any(want == x or want in x for x in labels), labels)
        check("右键会选中该行", app.tree.selection() == ("i1",), app.tree.selection())
        check("单账号时打开主页可用",
              [s for _t, l, s in menu.items if l == "打开主页"] == ["normal"])

        # 账号级监控内容：四个选项可打勾，当前值带 ✓
        app.accounts[0]["scope"] = "live"
        FakeMenu.captured.clear()
        app.on_tree_context(ev)
        labels2 = [l for _t, l, _s in FakeMenu.captured[0].items]
        check("监控内容四项都在", all(any(l.strip().endswith(t2) for l in labels2)
                                     for t2 in ("作品和直播", "监控作品", "监控直播", "跟全局")),
              labels2)
        check("当前监控内容打勾", any(l.startswith("✓ 监控直播") for l in labels2), labels2)
        app.accounts[0].pop("scope", None)
        FakeMenu.captured.clear()
        app.on_tree_context(ev)
        labels3 = [l for _t, l, _s in FakeMenu.captured[0].items]
        check("跟全局时勾在跟全局", any(l.startswith("✓ 跟全局") for l in labels3), labels3)

        # 多选：右键选区内保留多选，出现「已选 N 个账号」
        app.tree.selection_set(("i1", "i2"))
        FakeMenu.captured.clear()
        app.on_tree_context(SimpleNamespace(x=bbox[0] + 40, y=bbox[1] + bbox[3] // 2,
                                           x_root=600, y_root=400))
        menu3 = FakeMenu.captured[0]
        labels3 = [l for _t, l, _s in menu3.items]
        check("多选时提示已选几个", any("已选 2 个账号" in l for l in labels3), labels3)
        check("多选时打开主页禁用",
              [s for _t, l, s in menu3.items if l == "打开主页"] == ["disabled"])
        check("多选时刷新改成多个账号",
              any("刷新这 2 个账号" in l for l in labels3), labels3)
        check("多选保留选区", set(app.tree.selection()) == {"i1", "i2"}, app.tree.selection())

        # 复制主页链接
        app.copy_profile_url(app.accounts[0])
        check("复制主页链接进剪贴板", "douyin.com/user/SEC_A" in app.root.clipboard_get(),
              app.root.clipboard_get())
        app.copy_account_text(app.accounts[0])
        check("复制昵称与账号", "测试A" in app.root.clipboard_get(), app.root.clipboard_get())
    finally:
        client.tk.Menu = orig_menu

    # 副标题提示右键
    check("账号列表副标题提示右键", "右键" in app.count_var.get(), app.count_var.get())

    client.App.toggle_monitor = orig_toggle
    set_cfg(auto_monitor=True)
    root.destroy()

    io.open(os.path.join(HERE, "_log_menu_res.json"), "w", encoding="utf-8").write(
        json.dumps(RESULT, ensure_ascii=False, indent=2))
    print("RESULT_OK" if RESULT["ok"] else "RESULT_FAIL")
    print("checks=%d failed=%d" % (len(RESULT["checks"]),
                                   sum(1 for c in RESULT["checks"] if not c["ok"])))
    for c in RESULT["checks"]:
        if not c["ok"]:
            print("  FAIL:", c["name"], c["extra"])


if __name__ == "__main__":
    main()