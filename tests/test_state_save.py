# -*- coding: utf-8 -*-
"""状态写盘的并发/容错回归（Permission denied: 'client_state.json.tmp' 那个 bug）

跑法（在 _t_sandbox 目录里）：
    python test_state_save.py
"""
import io
import json
import logging
import os
import sys
import threading
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douyin_client as client  # noqa: E402
import douyin_monitor as core  # noqa: E402

CHECKS = []


def check(name, ok, extra=""):
    CHECKS.append({"name": name, "ok": bool(ok), "extra": str(extra)[:600]})


def dump():
    path = os.path.join(HERE, "_state_res.json")
    io.open(path, "w", encoding="utf-8").write(json.dumps(
        {"checks": CHECKS, "failed": sum(1 for c in CHECKS if not c["ok"])},
        ensure_ascii=False, indent=1))
    print("checks=%d failed=%d" % (len(CHECKS), sum(1 for c in CHECKS if not c["ok"])))
    print("RESULT_OK" if all(c["ok"] for c in CHECKS) else "RESULT_FAIL")
    for c in CHECKS:
        if not c["ok"]:
            print("  FAIL:", c["name"], c["extra"][:200].encode("utf-8", "replace").decode("utf-8"))


# ---------------- ① 并发写同一个 JSON：不许撞、不许留 .tmp ----------------
target = os.path.join(HERE, "client_state.json")
errors = []


def writer(idx):
    for i in range(25):
        try:
            core.write_json(target, {"accounts": {"a%d" % idx: {"seen": {"n%d" % i: {}}}}})
        except Exception as exc:  # noqa: BLE001
            errors.append("%s: %s" % (idx, exc))


ts = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
for t in ts:
    t.start()
for t in ts:
    t.join()
check("8 线程 ×25 次并发写 JSON 不报错", not errors, errors[:3])
try:
    data = json.load(io.open(target, encoding="utf-8"))
    check("并发写完之后文件是合法 JSON", isinstance(data, dict) and "accounts" in data, list(data)[:3])
except Exception as exc:  # noqa: BLE001
    check("并发写完之后文件是合法 JSON", False, exc)
left = [f for f in os.listdir(HERE) if f.endswith(".tmp")]
check("不残留临时文件", not left, left[:5])

# ---------------- ② 真实机制：还有一个句柄开着时 os.replace 会 PermissionError ----------------
old_style = target + ".tmp"
holder = open(old_style, "w", encoding="utf-8")
holder.write("hold")
holder.flush()
raised_swap = None
try:
    os.replace(old_style, target)       # 旧写法：另一个线程刚写完还没关句柄就 replace
except PermissionError as exc:
    raised_swap = exc
holder.close()
try:
    os.remove(old_style)
except OSError:
    pass
check("还有句柄开着时 os.replace 报的就是 PermissionError（11:45 那条错误的来源）",
      raised_swap is not None, raised_swap)

# ---------------- ③ 配置里的路径：内存绝对、磁盘相对 ----------------
client.App.toggle_monitor = lambda _self: None
client.App.cookie_ready = lambda _self, _plat="douyin": True
cfg_path = os.path.join(HERE, "client_config.json")
cfg = json.load(io.open(cfg_path, encoding="utf-8"))
cfg["accounts"] = cfg.get("accounts") or [{"iid": "row1", "target": "测试A", "nickname": "测试A",
                                           "platform": "douyin", "enabled": True,
                                           "sec_user_id": "SEC", "uid": "1"}]
io.open(cfg_path, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=1))

root = tk.Tk()
app = client.App(root)
for _ in range(5):
    root.update()
check("load_config 给的是绝对路径", os.path.isabs(app.cfg["state_file"]), app.cfg["state_file"])
app.save_config()
check("save_config 之后内存里仍是绝对路径", os.path.isabs(app.cfg["state_file"]), app.cfg["state_file"])
saved = json.load(io.open(cfg_path, encoding="utf-8"))
check("磁盘上存的是相对名（换目录也能用）",
      not os.path.isabs(saved.get("state_file") or ""), saved.get("state_file"))

# ---------------- ④ 相对路径兜底：存状态前补成绝对 ----------------
app.cfg["state_file"] = "client_state.json"
app.state["accounts"] = {"SEC": {"seen": {"1001": {"type": "视频"}}, "first_run_done": True}}
app.save_state()
check("相对路径会被补成 BASE_DIR 下的绝对路径", os.path.isabs(app.cfg["state_file"]), app.cfg["state_file"])
check("兜底写盘真的落到了程序目录",
      os.path.isfile(os.path.join(client.BASE_DIR, "client_state.json")),
      app.cfg["state_file"])

# ---------------- ⑤ 写盘失败：不抛、只警告、恢复后复位 ----------------
logs = []


class Cap(logging.Handler):
    def emit(self, rec):
        logs.append(rec.getMessage())


cap = Cap()
core.log.addHandler(cap)
old_level = core.log.level
core.log.setLevel(logging.DEBUG)

real_write = core.write_json


def boom(*_a, **_k):
    raise PermissionError(13, "Permission denied", "client_state.json.tmp")


core.write_json = boom
raised = None
try:
    app.save_state()
    app.save_state()
except Exception as exc:  # noqa: BLE001
    raised = exc
check("存状态失败不往上抛（否则整轮检查会挂）", raised is None, raised)
check("失败次数在累计", app._save_fails == 2, app._save_fails)
check("失败只警告，不报错", any("状态没存下来" in m for m in logs), logs[-1][:120])

core.write_json = real_write
app.save_state()
check("恢复成功后计数归零", app._save_fails == 0, app._save_fails)
check("恢复时写一条「状态恢复保存」", any("状态恢复保存" in m for m in logs), logs[-1][:120])

# ---------------- ⑥ 一轮检查不会因为存状态失败而中断 ----------------
app.save_state = boom
stats = None
raised2 = None
try:
    # 直接调一轮（假 client，不会联网）
    class FakeDY:
        platform = "douyin"

        def reload_cookie(self):
            return 0

    app.get_client = lambda *_a, **_k: FakeDY()
    app.check_account = lambda *_a, **_k: {"items": 0, "files": 0}
    stats = app.monitor_cycle()
except Exception as exc:  # noqa: BLE001
    raised2 = exc
check("存状态失败不影响一轮检查返回", raised2 is None and isinstance(stats, dict), raised2 or stats)
check("这一轮仍然统计到了账号数", int((stats or {}).get("checked") or 0) >= 1, stats)

core.log.removeHandler(cap)
core.log.setLevel(old_level)

# 收尾
try:
    for name in ("client_state.json", "client_state.json.tmp"):
        p = os.path.join(HERE, name)
        if os.path.isfile(p):
            os.remove(p)
    root.destroy()
except Exception:  # noqa: BLE001
    pass

dump()