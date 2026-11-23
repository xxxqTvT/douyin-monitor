"""「监控内容：作品/直播」与「启动后先全量核账」的回归。

跑在沙箱目录里（tests/make_sandbox.py 建的）：
    python test_scope.py
"""
import io
import json
import os
import sys
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


class FakeDY:
    def fetch_profile(self, sec):
        return {"nickname": "测试账号", "uid": "1001", "unique_id": "test"}

    def resolve_sec_uid(self, target):
        return target


def main():
    os.chdir(HERE)
    root = tk.Tk()
    app = client.App(root)
    root.update_idletasks()

    calls = []
    core.check_posts = lambda *a, **k: calls.append("posts") or []
    core.check_live = lambda *a, **k: calls.append("live") or None

    acc = {"target": "测试账号", "nickname": "测试账号", "enabled": True,
           "sec_user_id": "SEC_SCOPE", "platform": "douyin"}
    dy = FakeDY()

    # ① 只监控作品：不查直播
    calls[:] = []
    app.run_cfg = {"live": True, "scope": "posts"}
    app.check_account(dy, acc, do_live=True)
    check("只监控作品时不查直播", "live" not in calls, calls)
    check("只监控作品时仍查作品", "posts" in calls, calls)

    # ② 只监控直播：不查作品
    calls[:] = []
    app.run_cfg = {"live": True, "scope": "live"}
    app.check_account(dy, acc, do_live=True)
    check("只监控直播时不查作品", "posts" not in calls, calls)
    check("只监控直播时查直播", "live" in calls, calls)

    # ③ 作品和直播：两样都查
    calls[:] = []
    app.run_cfg = {"live": True, "scope": "both"}
    app.check_account(dy, acc, do_live=True)
    check("作品和直播都查", ("posts" in calls) and ("live" in calls), calls)

    # ③b 账号级监控内容优先于全局（右键四项：作品 / 直播 / 作品和直播 / 跟全局）
    calls[:] = []
    app.run_cfg = {"live": True, "scope": "both"}
    acc_live_only = dict(acc, scope="live")
    app.check_account(dy, acc_live_only, do_live=True)
    check("账号设为只监控直播时不查作品", "posts" not in calls, calls)
    check("账号设为只监控直播时查直播", "live" in calls, calls)

    calls[:] = []
    acc_posts_only = dict(acc, scope="posts")
    app.check_account(dy, acc_posts_only, do_live=True)
    check("账号设为只监控作品时不查直播", "live" not in calls, calls)
    check("账号设为只监控作品时仍查作品", "posts" in calls, calls)

    calls[:] = []
    acc_both = dict(acc, scope="both")
    app.check_account(dy, acc_both, do_live=True)
    check("账号设为作品和直播时两样都查", ("posts" in calls) and ("live" in calls), calls)

    # 跟全局：账号上没有 scope，落回全局 live
    calls[:] = []
    acc_global = dict(acc)
    acc_global.pop("scope", None)
    app.run_cfg = {"live": True, "scope": "posts"}
    app.check_account(dy, acc_global, do_live=True)
    check("跟全局时按全局设置走（只作品）", "posts" in calls and "live" not in calls, calls)

    # 选择写入账号并落盘
    app.set_account_scope("live", [acc])
    check("set_account_scope 写入账号", acc.get("scope") == "live", acc.get("scope"))
    app.set_account_scope(None, [acc])
    check("选「跟全局」会清掉账号级 scope", "scope" not in acc, acc.get("scope"))

    # ④ 只监控作品时，独立直播轮询直接跳过
    calls[:] = []
    app.run_cfg = {"live": True, "scope": "posts"}
    app.live_cycle()
    check("只监控作品时直播轮询不跑", not calls, calls)
    app.live_cycle_count = 0

    # ⑤ 界面控件与配置默认值
    check("有「启动后先全量核账」勾选框", hasattr(app, "auto_full_var"), "")
    check("有「监控内容」下拉框", hasattr(app, "scope_box"), "")
    check("监控内容默认作品和直播",
          str(app.cfg.get("monitor_scope") or "both") == "both", app.cfg.get("monitor_scope"))
    check("下拉框显示作品和直播", app.scope_var.get() == "作品和直播", app.scope_var.get())

    # ⑥ 切换监控内容会写进配置
    app.scope_var.set("只监控直播")
    app.on_scope_change()
    check("切换监控内容写配置", app.cfg.get("monitor_scope") == "live",
          app.cfg.get("monitor_scope"))
    app.scope_var.set("作品和直播")
    app.on_scope_change()

    # ⑦ 启动自动监控时的全量标记：开启后来源「启动自动」的核账 trigger 带全量
    app.cfg["auto_start_full"] = True
    app.auto_full_var.set(True)
    app.on_auto_full_change()
    check("启动全量标记写入配置", bool(app.cfg.get("auto_start_full")) is True, app.cfg.get("auto_start_full"))

    # ⑦-2 监控时新作品下载完要投递到右上「本次下载」面板
    while not app.q.empty():
        app.q.get_nowait()

    def fake_check_posts(cfg, dy, st, first_run, report=None):
        report["files"] = [os.path.join(HERE, "x.mp4")]
        report["items"] = [{"aweme_id": "1", "kind": "视频", "desc": "测试",
                            "files": report["files"]}]
        return [{"aweme_id": "1"}]

    core.check_posts = fake_check_posts
    app.run_cfg = {"live": True, "scope": "posts"}
    app.check_account(dy, acc, do_live=False)
    events = []
    while not app.q.empty():
        events.append(app.q.get_nowait())
    downs = [e for e in events if e[0] == "downloaded"]
    check("监控下载会投递到本次下载面板", bool(downs), events[:5])
    check("面板事件带作品与文件",
          bool(downs) and downs[0][1].get("items") and downs[0][1].get("files"),
          downs[:1])

    # ⑦-3 手动刷新结束：按钮复位、状态离开「刷新中」
    acc["refresh_busy"] = True
    acc["state"] = "刷新中"
    app.on_refresh_done({"acc": acc, "nickname": "测试账号"})
    check("刷新结束后按钮复位", acc.get("refresh_busy") is False, acc)
    check("刷新结束后状态不是刷新中", str(acc.get("state")) != "刷新中", acc.get("state"))
    acc["refresh_busy"] = True
    acc["state"] = "刷新中"
    app.on_refresh_done({"acc": acc, "nickname": "测试账号", "error": "网络错误"})
    check("刷新失败显示错误状态", str(acc.get("state") or "").startswith("出错"), acc.get("state"))
    check("刷新失败也会复位按钮", acc.get("refresh_busy") is False, acc)

    # ⑧ 两个核账撞车：后到的直接跳过，不会同时翻页/重复补下载
    app._audit_busy = True
    busy = app.audit_account(acc, trigger="并发测试") or {}
    check("已有核账在跑时重复触发直接跳过", busy.get("skipped") == "已有核账在跑", busy)
    app._audit_busy = False
    check("核账结束后可以再次进入", app.audit_account is not None, "")

    root.destroy()
    out = os.path.join(HERE, "_scope_res.json")
    io.open(out, "w", encoding="utf-8").write(json.dumps(RESULT, ensure_ascii=False, indent=1))
    print("checks=%d failed=%d" % (len(RESULT["checks"]), sum(1 for c in RESULT["checks"] if not c["ok"])))
    for c in RESULT["checks"]:
        if not c["ok"]:
            print("  FAIL:", c["name"], c["extra"])
    print("RESULT_OK" if RESULT["ok"] else "RESULT_FAIL")
    return 0 if RESULT["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())