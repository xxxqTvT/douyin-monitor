# -*- coding: utf-8 -*-
"""回归：抖音作品列表接口软风控（HTTP 200 + 空 body）自动熔断降频（v1.5.15）。

背景：/aweme/v1/web/aweme/post/ 被风控时不返回 403，而是 200 + Content-Length: 0。
旧代码 json.loads(b'') 抛 "Expecting value: line 1 column 1 (char 0)"，
每个账号每轮刷 ERROR 且继续猛打，风控永不解除。
覆盖：网络层识别/退避/自动恢复、监控层冷却期零请求、日志友好化、全量核账降频。
全部离线：urlopen 与客户端都用假实现，不发任何网络请求。
"""
import io
import json
import logging
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douyin_monitor as core  # noqa: E402
import douyin_client as client  # noqa: E402

CHECKS = []


def check(name, cond, extra=""):
    CHECKS.append({"name": name, "ok": bool(cond), "extra": str(extra)[:400]})


def reset_rate():
    core._rate_clear()
    core._RATE_UNTIL = 0.0
    core._LAST_POST_REQ = 0.0


class _Resp:
    def __init__(self, body=b""):
        self._body = body
        self.headers = {"Content-Type": "application/json",
                        "x-tt-trace-tag": "id=26;cdn-cache=miss;type=dyn"}

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def bare_dy(body=b""):
    """不跑 DouYin.__init__（那要 douk_min），只造够 get_json 用的最小对象"""
    dy = object.__new__(core.DouYin)
    dy.timeout = 5
    dy.cookie_str = "sessionid=x"
    dy.signed_query = lambda path, params, host="https://www.douyin.com": "a_bogus=fake"
    return dy


# ---------------- ① 空 body 被识别为 RateLimited ----------------
_orig_urlopen = urllib.request.urlopen
calls = {"n": 0}


def fake_urlopen_empty(req, timeout=None):
    calls["n"] += 1
    return _Resp(b"")


urllib.request.urlopen = fake_urlopen_empty
reset_rate()
try:
    dy = bare_dy()
    raised = None
    try:
        dy.get_json(core.POST_PATH, {"sec_user_id": "SEC"}, referer="https://www.douyin.com/user/SEC")
    except Exception as exc:  # noqa: BLE001
        raised = exc
    check("空 body 抛 RateLimited", isinstance(raised, core.RateLimited), type(raised).__name__)
    check("空 body 触发冷却", core.rate_limit_remaining() > 0, core.rate_limit_remaining())
    check("触发冷却只发了一次请求", calls["n"] == 1, calls["n"])

    # 冷却期内再调用：应直接抛，且一个网络包都不再发
    n_before = calls["n"]
    raised2 = None
    try:
        dy.get_json(core.POST_PATH, {"sec_user_id": "SEC"}, referer="https://www.douyin.com/user/SEC")
    except Exception as exc:  # noqa: BLE001
        raised2 = exc
    check("冷却期内直接拒绝", isinstance(raised2, core.RateLimited), type(raised2).__name__)
    check("冷却期内零网络请求", calls["n"] == n_before, (n_before, calls["n"]))
    check("冷却文案含『冷却中』", "冷却" in str(raised2), str(raised2))
finally:
    urllib.request.urlopen = _orig_urlopen

# ---------------- ② 正常 JSON 自动解除冷却 ----------------
def fake_urlopen_ok(req, timeout=None):
    return _Resp(b'{"status_code":0,"aweme_list":[],"has_more":0,"max_cursor":"0"}')


urllib.request.urlopen = fake_urlopen_ok
try:
    # 手动把冷却拨到未来，再模拟「到点后成功」
    core._RATE_UNTIL = 0.0
    d = bare_dy().get_json(core.POST_PATH, {"sec_user_id": "SEC"})
    check("成功页返回可解析 JSON", d.get("status_code") == 0, d)
    check("成功后冷却清零", core.rate_limit_remaining() == 0, core.rate_limit_remaining())
finally:
    urllib.request.urlopen = _orig_urlopen

# ---------------- ③ 详情接口不受冷却影响 ----------------
reset_rate()
core._RATE_UNTIL = time.time() + 600     # 假装作品列表还在冷却
urllib.request.urlopen = fake_urlopen_ok
try:
    d = bare_dy().get_json(core.DETAIL_PATH, {"aweme_id": "123"})
    check("冷却期详情接口照常可用", d.get("status_code") == 0, d)
finally:
    urllib.request.urlopen = _orig_urlopen
    reset_rate()

# ---------------- ④ classify_error 的限流文案 ----------------
msg = core.classify_error(core.RateLimited("作品列表接口返回空响应（风控限流）"), "douyin")
check("限流文案说『自动降频』", "自动降频" in msg, msg)
check("限流文案不含『重新扫码登录』", "重新扫码" not in msg, msg)
check("限流文案说『无需操作』", "无需操作" in msg, msg)

# ---------------- ⑤ 指数退避 ----------------
reset_rate()
core._rate_trip("（测试）")
w1 = core.rate_limit_remaining()
core._rate_trip("（测试）")
w2 = core.rate_limit_remaining()
check("连续触发退避变长", w2 > w1, (round(w1), round(w2)))
check("退避上限 60 分钟", w2 <= 61 * 60, round(w2))
reset_rate()

# ---------------- ⑥ 客户端：RateLimited 只打一条 WARNING 并停轮 ----------------
import tkinter as tk  # noqa: E402

CFG = os.path.join(HERE, "client_config.json")
cfg = json.load(io.open(CFG, encoding="utf-8"))
cfg.update({"platform_chosen": True, "auto_monitor": False, "silent_mode": False,
            "audit_on_start": False, "audit_every_round": True,
            "audit_round_interval": 6})
io.open(CFG, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))

root = tk.Tk()
client.App.toggle_monitor = lambda _self: None
client.App.cookie_ready = lambda _self, _plat="douyin": True
app = client.App(root)

logs = []


class Cap(logging.Handler):
    def emit(self, rec):
        logs.append((rec.levelname, rec.getMessage()))


cap = Cap()
core.log.addHandler(cap)
old_level = core.log.level
core.log.setLevel(logging.DEBUG)

# 造 3 个账号，全部抛 RateLimited
app.accounts = [{"iid": "r%d" % i, "target": "T%d" % i, "nickname": "账号%d" % i,
                 "platform": "douyin", "enabled": True, "sec_user_id": "SEC%d" % i}
                for i in range(3)]
class _FakeClient:
    def reload_cookie(self):
        pass
app.get_client = lambda *_a, **_k: _FakeClient()


def boom(*_a, **_k):
    raise core.RateLimited("作品列表接口冷却中")


app.check_account = boom
app.audit_account = lambda *_a, **_k: {"posts": 0, "missing": 0}
app.cycle_count = 6
logs[:] = []
stats = app.monitor_cycle()
err_lines = [m for lv, m in logs if lv == "ERROR" and "检查失败" in m]
warn_lines = [m for lv, m in logs if lv == "WARNING" and "限流" in m]
check("限流时不刷每账号 ERROR", len(err_lines) == 0, err_lines)
check("限流时只打一条 WARNING", len(warn_lines) == 1, warn_lines)
check("限流时立即停止本轮", int(stats.get("checked") or 0) == 0, stats)
check("限流提示『无需操作』", "无需操作" in (warn_lines[0] if warn_lines else ""),
      warn_lines[:1])

# ---------------- ⑦ 冷却轮次汇总只一行友好提示 ----------------
logs[:] = []
app.log_round_summary({"rate_wait": 600}, 1, 600)
check("冷却汇总写『暂停』", any("暂停" in m for _lv, m in logs), logs[-1:])
check("冷却汇总写『单独下载不受影响』",
      any("单独下载" in m for _lv, m in logs), logs[-1:])
check("冷却汇总只有一行", len([m for _lv, m in logs if "第 " in m]) == 1, logs[-2:])
check("限流轮不算『账号出错』", int(stats.get("failed") or 0) == 0, stats)
check("限流轮标了 rate_hit", bool(stats.get("rate_hit")), stats)
logs[:] = []
app.log_round_summary({"rate_hit": True, "checked": 0, "failed": 0}, 1, 300)
check("rate_hit 汇总写『自动降频』", any("自动降频" in m for _lv, m in logs), logs[-1:])
check("rate_hit 汇总不写『个账号出错』",
      not any("个账号出错" in m for _lv, m in logs), logs[-1:])

# ---------------- ⑧ 全量核账降频 ----------------
audit_calls = []
app.audit_account = lambda acc, trigger="每轮检查", dry_run=False, quiet=False: (
    audit_calls.append((acc.get("nickname"), trigger)) or
    {"posts": 1, "missing": 0, "items": 0, "files": 0})
app.check_account = lambda *_a, **_k: {"items": 0, "files": 0}
for cycle in (1, 2, 3, 6, 7, 12):
    audit_calls.clear()
    app.cycle_count = cycle
    app.monitor_cycle()
    check("第 %d 轮核账账号数 = %s" % (cycle, 3 if cycle % 6 == 0 else 0),
          len(audit_calls) == (3 if cycle % 6 == 0 else 0), audit_calls)

core.log.removeHandler(cap)
core.log.setLevel(old_level)
try:
    root.destroy()
except Exception:
    pass

print("checks=%d failed=%d" % (len(CHECKS), sum(1 for c in CHECKS if not c["ok"])))
print("RESULT_OK" if all(c["ok"] for c in CHECKS) else "RESULT_FAIL")
for c in CHECKS:
    if not c["ok"]:
        print("  FAIL:", c["name"], c["extra"][:200])
