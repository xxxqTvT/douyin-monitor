# -*- coding: utf-8 -*-
"""回归：点账号行里的「刷新」不能弹「是否下载历史作品」，也不能被整号下载卡住。"""
import io
import json
import os
import sys
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douyin_client as client  # noqa: E402
import douyin_monitor as core  # noqa: E402

CFG = os.path.join(HERE, "client_config.json")
CHECKS = []


def check(name, cond, extra=""):
    CHECKS.append((name, bool(cond), str(extra)[:300]))


def pump(root, seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def set_cfg(**kw):
    cfg = json.load(io.open(CFG, encoding="utf-8"))
    cfg.update(kw)
    io.open(CFG, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))


class FakeDY(object):
    platform = "douyin"

    def reload_cookie(self):
        pass

    def resolve_sec_uid(self, target):
        return "SEC_FAKE"

    def fetch_profile(self, sec):
        return {"aweme_count": 10, "uid": "UID_FAKE", "nickname": "测试A", "live_status": 0}

    def fetch_posts_page(self, sec, count=18, cursor="0"):
        return [], False, "0"


def main():
    set_cfg(auto_monitor=False, platform_chosen=True, silent_mode=False,
            live_check=True, audit_on_start=False)
    root = tk.Tk()
    client.App.toggle_monitor = lambda *_a, **_k: None
    client.App.cookie_ready = lambda *_a, **_k: True
    app = client.App(root)
    pump(root, 0.2)

    acc = app.accounts[0]
    acc["sec_user_id"] = "SEC_FAKE"
    app.state["accounts"].pop("SEC_FAKE", None)

    captured = {}
    orig_posts = core.check_posts
    orig_live = core.check_live
    try:
        def fake_posts(cfg, dy, st, first_run, decide=None, report=None):
            captured.update(decide=decide, first_run=first_run)
            return []

        core.check_posts = fake_posts
        core.check_live = lambda *_a, **_k: None
        app.record_session = lambda *_a, **_k: None
        app.sync_live = lambda *_a, **_k: None

        # 1) 手动刷新：不传询问回调
        app.check_account(FakeDY(), acc, ask_history=False)
        check("手动刷新不弹「是否下载历史作品」", captured.get("decide") is None, captured.get("decide"))
        check("首次运行标记仍然生效", captured.get("first_run") is True, captured.get("first_run"))

        # 2) 监控/其它调用也不再询问（首次检查只登记，历史作品走「下载历史视频」）
        app.state["accounts"].pop("SEC_FAKE", None)
        app.check_account(FakeDY(), acc)
        check("监控首轮也不再询问", captured.get("decide") is None, captured.get("decide"))
        check("首次运行询问方法已移除", not hasattr(app, "ask_first_run"), "ask_first_run")
    finally:
        core.check_posts = orig_posts
        core.check_live = orig_live

    # 3) 刷新线程必须用 ask_history=False，且刷新能跑完
    called = {}

    def fake_check(dy, a, do_live=True, ask_history=True):
        called["ask"] = ask_history
        return {"items": 0}

    app.check_account = fake_check
    app.audit_account = lambda *_a, **_k: {"posts": 0, "missing": 0, "items": 0}
    app.get_client = lambda *_a, **_k: FakeDY()
    acc["refresh_busy"] = False
    app._refresh_worker(acc)
    pump(root, 0.1)
    check("刷新线程不弹历史下载询问", called.get("ask") is False, called)
    check("刷新结束后账号回到监控中", acc.get("state") == "监控中", acc.get("state"))
    check("刷新结束后不再标记刷新中", acc.get("refresh_busy") is False, acc.get("refresh_busy"))

    root.destroy()
    failed = [c for c in CHECKS if not c[1]]
    for name, ok, extra in CHECKS:
        print(("  PASS " if ok else "  FAIL ") + name + ((" " + extra) if not ok else ""))
    print("RESULT_OK" if not failed else "RESULT_FAIL %d %s" % (len(failed), [c[0] for c in failed]))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()