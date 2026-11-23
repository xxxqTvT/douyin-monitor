# -*- coding: utf-8 -*-
"""回归：直播检测间隔与账号视频（作品）检测间隔可以分别设置（v1.5.16）。

覆盖：
  ① 两项独立配置 + UI 控件 + 校验限幅；
  ② 独立轮询开启时，作品轮不查直播、直播轮单独按自己的间隔跑；
  ③ 直播轮只走 profile/other（PROFILE_PATH），绝不碰作品列表 POST_PATH；
  ④ 作品列表接口被风控冷却时，直播轮照常触发（开播检测不中断）；
  ⑤ 关掉「独立轮询」时回到旧行为（作品轮里顺带查直播）；
  ⑥ 直播状态没变化时不刷状态文件；开播/下播变化才落盘；
  ⑦ 不重复投递 live_start（同一场直播沿用地址）。
全部离线：客户端、接口、队列、写盘都换成本地假实现，不发任何网络请求。
"""
import io
import json
import os
import sys
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douyin_monitor as core  # noqa: E402
import douyin_client as client  # noqa: E402

CHECKS = []


def check(name, cond, extra=""):
    CHECKS.append({"name": name, "ok": bool(cond), "extra": str(extra)[:400]})


CFG = os.path.join(HERE, "client_config.json")
cfg = json.load(io.open(CFG, encoding="utf-8"))
cfg.update({"platform_chosen": True, "auto_monitor": False, "silent_mode": False,
            "audit_on_start": False, "audit_every_round": False, "tray_enabled": False,
            "live_check": True, "poll_interval": 300,
            "live_poll_interval": 60, "live_separate": True})
io.open(CFG, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))

root = tk.Tk()
client.App.toggle_monitor = lambda _self: None
client.App.cookie_ready = lambda _self, _plat="douyin": True
app = client.App(root)


# ---------------- ① 配置与 UI ----------------
check("默认配置有 live_poll_interval",
      "live_poll_interval" in client.DEFAULT_CONFIG, client.DEFAULT_CONFIG.get("live_poll_interval"))
check("默认配置有 live_separate",
      "live_separate" in client.DEFAULT_CONFIG, client.DEFAULT_CONFIG.get("live_separate"))
check("直播间隔可短于作品间隔下限",
      client.MIN_LIVE_INTERVAL < client.MIN_INTERVAL,
      (client.MIN_LIVE_INTERVAL, client.MIN_INTERVAL))
check("UI 有直播间隔输入框", hasattr(app, "live_interval_spin"))
check("UI 有独立轮询复选框", hasattr(app, "live_separate_var"))
check("UI 两个间隔初始值分别来自配置",
      app.interval_var.get() == 300 and app.live_interval_var.get() == 60,
      (app.interval_var.get(), app.live_interval_var.get()))

# 校验限幅：作品 300 不动，直播 20 合法（<30 但 >=10）
app.interval_var.set(300)
app.live_interval_var.set(20)
app.live_separate_var.set(True)
v = app.validate_interval(show_dialog=False)
check("作品间隔校验返回 300", v == 300, v)
check("直播间隔 20 秒被保留（不被 30 秒作品下限砍掉）",
      app.cfg["live_poll_interval"] == 20, app.cfg["live_poll_interval"])
app.live_interval_var.set(5)
app.validate_interval(show_dialog=False)
check("直播间隔低于 10 秒被抬到 10",
      app.cfg["live_poll_interval"] == client.MIN_LIVE_INTERVAL,
      app.cfg["live_poll_interval"])
app.live_interval_var.set(999999)
app.validate_interval(show_dialog=False)
check("直播间隔超过 24 小时被压到 86400",
      app.cfg["live_poll_interval"] == client.MAX_INTERVAL,
      app.cfg["live_poll_interval"])
app.live_interval_var.set(60)
app.save_config()
saved = core.read_json(CFG, None)
check("两个间隔都写进配置文件",
      saved.get("poll_interval") == 300 and saved.get("live_poll_interval") == 60,
      (saved.get("poll_interval"), saved.get("live_poll_interval")))
check("live_separate 写进配置文件", bool(saved.get("live_separate")), saved.get("live_separate"))


# ---------------- ② 作品轮与直播轮各自独立 ----------------
app.accounts = [{"iid": "r%d" % i, "target": "T%d" % i, "nickname": "账号%d" % i,
                 "platform": "douyin", "enabled": True, "sec_user_id": "SEC%d" % i}
                for i in range(3)]
app.run_cfg = {"interval": 300, "live_interval": 60, "live_separate": True, "live": True}

work_calls = []      # 作品轮 check_account 的 do_live 参数
app.check_account = lambda _dy, _acc, do_live=True: (
    work_calls.append(do_live) or {"items": 0, "files": 0})
app.audit_account = lambda *_a, **_k: {"posts": 0, "missing": 0}
app.get_client = lambda *_a, **_k: type("C", (), {"reload_cookie": lambda self: None})()
app.cycle_count = 1
work_calls[:] = []
app.monitor_cycle()
check("独立轮询时作品轮跑了 3 个账号", len(work_calls) == 3, work_calls)
check("独立轮询时作品轮不查直播", work_calls and all(x is False for x in work_calls), work_calls)

# 关掉独立轮询 → 作品轮恢复顺带查直播（旧行为）
app.run_cfg["live_separate"] = False
work_calls[:] = []
app.monitor_cycle()
check("关掉独立轮询 → 作品轮恢复查直播",
      len(work_calls) == 3 and all(x is True for x in work_calls), work_calls)
app.run_cfg["live_separate"] = True


# ---------------- ③ 直播轮只打 profile/other，不碰作品列表 ----------------
paths = []


class FakeDY:
    platform = "douyin"

    def reload_cookie(self):
        pass

    def fetch_profile(self, spec):
        paths.append(("fetch_profile", spec))
        return {"nickname": "账号0", "live_status": 0, "room_id": "0",
                "uid": "123", "aweme_count": 5}

    def resolve_sec_uid(self, spec):
        paths.append(("resolve_sec_uid", spec))
        return "SEC_NEW"

    def fetch_live(self, web_rid):
        paths.append(("fetch_live", web_rid))
        return None


fake_dy = FakeDY()
app.get_client = lambda *_a, **_k: fake_dy
app.state = {"accounts": {}}
app.sync_live = lambda *_a, **_k: None
app.q = type("Q", (), {"put": lambda self, _x: None})()
app.session = None
app.save_state = lambda: paths.append(("save_state", ""))

paths[:] = []
app.live_cycle()
profiles = [p for p in paths if p[0] == "fetch_profile"]
check("直播轮对每个账号取一次资料", len(profiles) == 3, profiles)
check("直播轮完全不发作品列表请求",
      not any("POST" in str(p) or "post" == str(p[0]).lower() for p in paths), paths)
check("直播轮不发拉流地址请求（当前没开播）",
      not any(p[0] == "fetch_live" for p in paths), paths)
check("没人开播时不写状态文件",
      not any(p[0] == "save_state" for p in paths), paths)
check("直播轮计数递增", app.live_cycle_count == 1, app.live_cycle_count)


# ---------------- ④ 冷却期照样触发直播轮 ----------------
live_rounds = []
app.live_cycle = lambda: live_rounds.append(1)
app.next_live_ts = 0.0
app.run_cfg["live_interval"] = client.MIN_LIVE_INTERVAL  # 10 秒，测试里不用真睡
app.stop_event.clear()
# 模拟作品接口冷却：monitor_loop 会走 rate_wait 分支，然后仍要调 _idle
core._RATE_UNTIL = 1e12
orig_summary = app.log_round_summary
summaries = []
app.log_round_summary = lambda stats, cost, wait: summaries.append(stats)
app.monitor_cycle = lambda: (_ for _ in ()).throw(
    AssertionError("冷却期不该跑作品检测"))
# 不真的跑整个 loop（会睡很久），单独验证 _idle 在冷却等待窗口里能插直播轮
import time as _time  # noqa: E402
app._idle = client.App._idle.__get__(app)
idle_thread = None


def short_idle():
    # 用很短的窗口（0.35 秒）验证 live_cycle 至少被触发一次
    app.next_live_ts = 0.0
    app._idle(_time.time() + 0.35)


import threading  # noqa: E402
idle_thread = threading.Thread(target=short_idle, daemon=True)
idle_thread.start()
idle_thread.join(timeout=3)
check("作品接口冷却/等待窗口内仍触发直播轮", len(live_rounds) >= 1, live_rounds)
check("_idle 到点能自己结束", not idle_thread.is_alive(), idle_thread.is_alive())
core._RATE_UNTIL = 0.0
core._RATE_STRIKES = 0
app.log_round_summary = orig_summary


# ---------------- ④b 真跑 monitor_loop：冷却期整轮跳过作品，但仍进 _idle ----------------
idle_calls = []
app._idle = lambda end: (idle_calls.append(end), app.stop_event.set())
summaries[:] = []
app.log_round_summary = lambda stats, cost, wait: summaries.append(stats)
core._RATE_UNTIL = 1e12
app.session = None
app.monitor_loop()
check("冷却期 monitor_loop 没跑作品检测",
      len(summaries) == 1 and "rate_wait" in summaries[0], summaries)
check("冷却期 monitor_loop 仍进入 _idle（直播轮能插进来）",
      len(idle_calls) == 1, idle_calls)
check("冷却等待窗口按 rate_wait 计算",
      idle_calls and summaries and summaries[0]["rate_wait"] > 0, summaries)
core._RATE_UNTIL = 0.0
core._RATE_STRIKES = 0
app.stop_event.clear()
app.log_round_summary = orig_summary
app._idle = client.App._idle.__get__(app)


# ---------------- ⑤ 关掉「检测直播」时直播轮不跑 ----------------
app.run_cfg["live"] = False
live_rounds[:] = []
app.live_cycle = client.App.live_cycle.__get__(app)   # 还原真实现
paths[:] = []
app.live_cycle()
check("全局关掉直播检测 → 直播轮零请求", len(paths) == 0, paths)
app.run_cfg["live"] = True


# ---------------- ⑥ 开播状态变化才写盘 ----------------
class LiveDY(FakeDY):
    def __init__(self, live_status):
        self.live_status = live_status

    def fetch_profile(self, spec):
        paths.append(("fetch_profile", spec))
        return {"nickname": "账号0", "live_status": self.live_status,
                "room_id": "5566", "short_id": "7788", "unique_id": "u1",
                "uid": "123", "aweme_count": 5}

    def fetch_live(self, web_rid):
        paths.append(("fetch_live", web_rid))
        return {"room_id": "5566", "title": "开播啦",
                "flv": {"FULL_HD1": "https://cdn/x.flv"},
                "hls": {"FULL_HD1": "https://cdn/x.m3u8"}}


starts = []
app.sync_live = lambda acc, key, nick, info, was: starts.append((key, info, was))

app.get_client = lambda *_a, **_k: LiveDY(0)
app.state = {"accounts": {}}
paths[:] = []
app.live_cycle()
check("没开播不写盘", not any(p[0] == "save_state" for p in paths), paths)

app.get_client = lambda *_a, **_k: LiveDY(1)
paths[:] = []
app.live_cycle()
check("检测到开播会写状态文件", any(p[0] == "save_state" for p in paths), paths)
check("开播会取拉流地址", any(p[0] == "fetch_live" for p in paths), paths)
check("开播投递 live_start", any(x[1] for x in starts), starts)

# 同一场继续播：不重复写盘、不重复投递
starts[:] = []
paths[:] = []
app.live_cycle()
check("同一场直播不重复写盘",
      not any(p[0] == "save_state" for p in paths), paths)
check("同一场直播仍回填 live 报告（录制重启后能续上）",
      any(x[1] for x in starts), starts)

# 下播：状态从 True 变 False，写一次盘
app.get_client = lambda *_a, **_k: LiveDY(0)
paths[:] = []
app.live_cycle()
check("下播状态变化会写盘", any(p[0] == "save_state" for p in paths), paths)
acc0 = app.accounts[0]
check("下播后账号状态不再是「直播中」", str(acc0.get("state") or "") != "直播中", acc0.get("state"))
check("下播后左侧直播列不再显示直播中", not acc0.get("live"), acc0.get("live"))


# ---------------- ⑥-2 有 ORIGIN（源画质）时优先用最高档 ----------------
class OriginDY:
    platform = "douyin"

    def fetch_live(self, web_rid):
        return {"room_id": "r9", "title": "测试",
                "flv": {"ORIGIN": "https://cdn/o.flv", "FULL_HD1": "https://cdn/f.flv"},
                "hls": {"ORIGIN": "https://cdn/o.m3u8", "FULL_HD1": "https://cdn/f.m3u8"}}


rep = {}
core.check_live({"sec_user_id": "S", "live_quality": "FULL_HD1", "live_record": False,
                 "notify": False, "data_dir": HERE},
                OriginDY(), {"live_active": False},
                {"live_status": 1, "room_id": "r9", "nickname": "测试直播",
                 "short_id": "7788", "unique_id": "u"}, report=rep)
check("有 ORIGIN 时优先源画质", (rep.get("live") or {}).get("quality") == "ORIGIN", rep.get("live"))

# ---------------- ⑦ _check_live_account 不碰作品列表接口常量 ----------------
import inspect  # noqa: E402
src = inspect.getsource(client.App._check_live_account)
check("_check_live_account 不引用 check_posts",
      "check_posts" not in src, src[:120])
check("_check_live_account 不引用 POST_PATH",
      "POST_PATH" not in src and "aweme/post" not in src, src[:120])
check("_check_live_account 用 check_live",
      "core.check_live" in src, src[:120])

try:
    root.destroy()
except Exception:
    pass

print("checks=%d failed=%d" % (len(CHECKS), sum(1 for c in CHECKS if not c["ok"])))
for c in CHECKS:
    if not c["ok"]:
        print("  FAIL: %s  %s" % (c["name"], c["extra"]))
print("RESULT_OK" if all(c["ok"] for c in CHECKS) else "RESULT_FAIL")
