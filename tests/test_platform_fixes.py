"""平台化 / 直播三态修复的回归（2026-11-12，v1.5.18）。

跑在 tests\\make_sandbox.py 建的沙箱里。覆盖：
- cookie_ready(platform) 分平台读 Cookie（抖音 cookie / TikTok cookie_tiktok）；
- verify_cookie_async / on_login_done / change_cookie 的平台透传；
- add_account("tiktok") 的 @用户名 / 链接解析；
- sync_live 的开播、下播、换场、「账号不录」、全局关但账号录；
- 「每轮检查都核账」开关写进配置。
"""
import io
import json
import os
import shutil
import sys
import tempfile
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
        time.sleep(0.03)


def drain(app):
    """把队列里已经排好的事件全部取出来（不执行处理器）"""
    out = []
    while True:
        try:
            out.append(app.q.get_nowait())
        except Exception:  # noqa: BLE001
            break
    return out


def kinds(events):
    return [k for k, _p in events if k in ("live_start", "live_stop")]


def live_payloads(events):
    return [p for k, p in events if k in ("live_start", "live_stop")]


def safe_destroy(root):
    try:
        for aid in root.tk.splitlist(root.tk.call("after", "info")):
            try:
                root.after_cancel(aid)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    try:
        root.destroy()
    except Exception:  # noqa: BLE001
        pass


class FakeRec(object):
    def __init__(self, running=True):
        self.running = running
        self.name = "rec"


class FakeMgr(object):
    def __init__(self, rec=None):
        self.rec = rec
        self.stopped = []

    def get(self, _key):
        return self.rec

    def stop(self, key, reason="", wait=10):
        self.stopped.append((key, reason))
        if self.rec is not None:
            self.rec.running = False
        return {"state": "已结束"}


def make_settings(douk_dir, data):
    path = os.path.join(douk_dir, "Volume", "settings.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


def main():
    tmp = tempfile.mkdtemp(prefix="tm_plat_")
    cfg_path = os.path.join(HERE, "client_config.json")
    orig_cfg = io.open(cfg_path, "rb").read()   # 测试会 save_config，结束时原样还回去
    make_settings(tmp, {
        "cookie": {"sessionid": "douyin-sid"},
        "cookie_tiktok": "sessionid=tiktok-sid; tt_chain=abc",
    })

    root = tk.Tk()
    app = client.App(root)
    app.cfg["data_dir"] = tmp
    app.cfg["douk_dir"] = ""

    # ---- 0) Cookie 文件位置：必须落在数据目录，不能跟着当前工作目录跑 ----
    check("douk_settings_path 落在数据目录",
          os.path.abspath(core.douk_settings_path(app.cfg)) ==
          os.path.abspath(os.path.join(tmp, "Volume", "settings.json")),
          core.douk_settings_path(app.cfg))

    # ---- 1) cookie_ready 分平台 ----
    check("抖音 Cookie 可用", app.cookie_ready("douyin") is True)
    check("TikTok Cookie 可用（字符串格式）", app.cookie_ready("tiktok") is True)
    make_settings(tmp, {"cookie": {"sessionid": "douyin-sid"}})
    check("TikTok 没 Cookie 时返回 False", app.cookie_ready("tiktok") is False)
    check("抖音仍有 Cookie", app.cookie_ready("douyin") is True)
    make_settings(tmp, {"cookie": {"sessionid": "douyin-sid"},
                        "cookie_tiktok": "sessionid=tiktok-sid"})
    check("TikTok Cookie 补回后可用", app.cookie_ready("tiktok") is True)

    # ---- 2) verify_cookie_async 平台透传 + 结果落进 _cookie_verify ----
    seen = {}

    def fake_verify(cfg, sec="", timeout=20, platform="douyin"):
        seen["platform"] = platform
        return True, "ok-%s" % platform

    orig_verify = core.verify_cookie
    core.verify_cookie = fake_verify
    app.verify_cookie_async("tiktok")
    pump(root, 0.8)
    check("verify_cookie 收到 tiktok", seen.get("platform") == "tiktok", seen)
    check("验证结果记到 tiktok", app._cookie_verify.get("tiktok") == (True, "ok-tiktok"),
          app._cookie_verify)
    core.verify_cookie = orig_verify

    # ---- 3) on_login_done 把 Cookie 存到对应平台 ----
    saved = {}

    def fake_save(cfg, text, platform="douyin"):
        saved["platform"] = platform
        saved["text"] = text
        return 2

    orig_save = core.save_cookie_text
    orig_summary = core.cookie_summary
    core.save_cookie_text = fake_save
    core.cookie_summary = lambda cfg, platform="douyin": "已设置"
    app.verify_cookie_async = lambda platform=None: None
    app.on_login_done({"ok": True, "cookie": {"sessionid": "tik"}, "platform": "tiktok"})
    check("on_login_done 存到 tiktok", saved.get("platform") == "tiktok", saved)
    core.save_cookie_text = orig_save
    core.cookie_summary = orig_summary

    # ---- 4) change_cookie 平台透传 ----
    opened = []
    app.open_cookie_dialog = lambda platform="douyin": opened.append(platform)
    app.change_cookie("tiktok")
    check("change_cookie('tiktok') 打开 tiktok 对话框", opened == ["tiktok"], opened)

    # ---- 5) add_account("tiktok") 解析 @用户名 ----
    accs_before = len(app.accounts)
    orig_ask = client.simpledialog.askstring
    orig_resolve = app.resolve_account
    client.simpledialog.askstring = lambda *a, **k: "@someone"
    app.resolve_account = lambda acc: None
    app.add_account("tiktok")
    client.simpledialog.askstring = orig_ask
    app.resolve_account = orig_resolve
    check("TikTok 账号加进列表", len(app.accounts) == accs_before + 1, len(app.accounts))
    if len(app.accounts) > accs_before:
        acc = app.accounts[-1]
        check("平台记成 tiktok", acc.get("platform") == "tiktok", acc.get("platform"))
        check("unique_id 去掉 @", acc.get("unique_id") == "someone", acc.get("unique_id"))
        check("主页链接是 tiktok", acc.get("url") == "https://www.tiktok.com/@someone",
              acc.get("url"))
        check("列表显示 @someone", acc.get("target") == "@someone", acc.get("target"))
        app.accounts.remove(acc)

    # ---- 6) sync_live：开播 / 下播 / 不录 / 全局关但账号录 ----
    acc = {"target": "测试A", "platform": "douyin", "sec_user_id": "SEC_A"}
    live = {"nickname": "测试A", "title": "直播标题", "room": "r1", "quality": "FULL_HD1",
            "quality_label": "原画", "flv": "http://x/flv", "hls": "http://x/hls",
            "platform": "douyin"}

    app.cfg["live_record"] = True
    app.live_mgr = FakeMgr(None)
    app.sync_live(acc, "SEC_A", "测试A", live, False)
    ev = drain(app)
    check("开播发 live_start", kinds(ev) == ["live_start"], kinds(ev))
    if live_payloads(ev):
        p = live_payloads(ev)[0]
        check("live_start 带拉流地址", p.get("flv") == "http://x/flv", p.get("flv"))
        check("live_start 带平台", p.get("platform") == "douyin", p.get("platform"))

    app.live_mgr = FakeMgr(FakeRec(True))
    app.sync_live(acc, "SEC_A", "测试A", live, True)
    ev = drain(app)
    check("同一场在录时不重复起", kinds(ev) == [], kinds(ev))

    app.live_mgr = FakeMgr(FakeRec(True))
    app.sync_live(acc, "SEC_A", "测试A", None, True)
    ev = drain(app)
    check("下播发 live_stop", kinds(ev) == ["live_stop"], kinds(ev))
    if live_payloads(ev):
        check("下播原因写「直播已结束」", live_payloads(ev)[0].get("reason") == "直播已结束",
              live_payloads(ev)[0].get("reason"))

    app.cfg["live_record"] = True
    app.live_mgr = FakeMgr(FakeRec(True))
    acc["live_record"] = False
    app.sync_live(acc, "SEC_A", "测试A", live, True)
    ev = drain(app)
    check("账号设为不录时发 live_stop", kinds(ev) == ["live_stop"], kinds(ev))
    app.live_mgr = FakeMgr(None)
    app.sync_live(acc, "SEC_A", "测试A", live, False)
    ev = drain(app)
    check("账号设为不录时不起录", kinds(ev) == [], kinds(ev))

    acc["live_record"] = True
    app.cfg["live_record"] = False
    app.live_mgr = FakeMgr(None)
    app.sync_live(acc, "SEC_A", "测试A", live, False)
    ev = drain(app)
    check("全局关但账号设为录时仍然录", kinds(ev) == ["live_start"], kinds(ev))

    acc.pop("live_record", None)
    app.cfg["live_record"] = False
    app.live_mgr = FakeMgr(None)
    app.sync_live(acc, "SEC_A", "测试A", live, False)
    ev = drain(app)
    check("跟全局且全局关时不起录", kinds(ev) == [], kinds(ev))

    app.cfg["live_record"] = True
    app.live_mgr = FakeMgr(None)
    bad = dict(live)
    bad["flv"] = bad["hls"] = ""
    app.sync_live(acc, "SEC_A", "测试A", bad, False)
    ev = drain(app)
    check("没有拉流地址时不起录", kinds(ev) == [], kinds(ev))

    # ---- 7) 每轮核账开关 ----
    app.auditevery_var.set(False)
    app.on_auditevery_change()
    check("关掉后 cfg=False", app.cfg.get("audit_every_round") is False,
          app.cfg.get("audit_every_round"))
    app.auditevery_var.set(True)
    app.on_auditevery_change()
    check("再打开 cfg=True", app.cfg.get("audit_every_round") is True,
          app.cfg.get("audit_every_round"))

    safe_destroy(root)
    shutil.rmtree(tmp, ignore_errors=True)
    io.open(cfg_path, "wb").write(orig_cfg)

    io.open(os.path.join(HERE, "_platform_res.json"), "w", encoding="utf-8").write(
        json.dumps(RESULT, ensure_ascii=False, indent=2))
    print("RESULT_OK" if RESULT["ok"] else "RESULT_FAIL")
    print("checks=%d failed=%d" % (len(RESULT["checks"]),
                                   sum(1 for c in RESULT["checks"] if not c["ok"])))


if __name__ == "__main__":
    main()