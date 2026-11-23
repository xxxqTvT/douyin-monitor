"""「启动软件后自动开始监控」的回归：调度、开关、各种跳过条件。

跑在沙箱目录里（`tests/make_sandbox.py` 建的），只读假配置；
`toggle_monitor` 被替换成计数器，不会真的去发网络请求。
"""
import io
import json
import os
import sys
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douyin_client as client  # noqa: E402

CFG = os.path.join(HERE, "client_config.json")
RESULT = {"checks": [], "ok": True}
CALLS = {"toggle": 0}


def check(name, cond, extra=""):
    RESULT["checks"].append({"name": name, "ok": bool(cond), "extra": str(extra)[:300]})
    if not cond:
        RESULT["ok"] = False


def read_cfg():
    return json.load(io.open(CFG, encoding="utf-8"))


def write_cfg(cfg):
    io.open(CFG, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))


def pump(root, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        root.update()
        time.sleep(0.03)


def set_cfg(**kw):
    cfg = read_cfg()
    cfg.update(kw)
    write_cfg(cfg)


def safe_destroy(root):
    """先把挂着的 after 撤掉再 destroy，免得测试输出里蹦 Tcl 报错"""
    try:
        for aid in root.tk.splitlist(root.tk.call("after", "info")):
            try:
                root.after_cancel(aid)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    try:
        safe_destroy(root)
    except Exception:  # noqa: BLE001
        pass


def main():
    # toggle_monitor 换成计数器（必须在建 App 之前，__init__ 里靠 after 调度它）
    def fake_toggle(_self, *_args, **_kwargs):
        CALLS["toggle"] += 1
    client.App.toggle_monitor = fake_toggle
    client.App.cookie_ready = lambda _self, _plat="douyin": True

    check("默认值是开", client.DEFAULT_CONFIG.get("auto_monitor") is True,
          client.DEFAULT_CONFIG.get("auto_monitor"))

    # ---- 1) 开着的时候，启动 1.8 秒后自动开跑 ----
    set_cfg(auto_monitor=True, platform_chosen=True)
    CALLS["toggle"] = 0
    root = tk.Tk()
    app = client.App(root)
    check("启动瞬间还没开跑", CALLS["toggle"] == 0, CALLS["toggle"])
    pump(root, 2.4)
    check("1.8 秒后自动开跑一次", CALLS["toggle"] == 1, CALLS["toggle"])
    pump(root, 1.0)
    check("只跑一次，不重复触发", CALLS["toggle"] == 1, CALLS["toggle"])

    # ---- 2) 菜单开关写配置 ----
    app.automon_var.set(False)
    app.on_automon_change()
    check("关掉后 cfg=False", app.cfg.get("auto_monitor") is False, app.cfg.get("auto_monitor"))
    check("关掉后落盘", read_cfg().get("auto_monitor") is False, read_cfg().get("auto_monitor"))
    app.automon_var.set(True)
    app.on_automon_change()
    check("再打开 cfg=True", app.cfg.get("auto_monitor") is True, app.cfg.get("auto_monitor"))

    # ---- 3) 各种跳过条件 ----
    CALLS["toggle"] = 0
    app.cfg["auto_monitor"] = False
    app.auto_start_monitor()
    check("设置关掉时不动", CALLS["toggle"] == 0, CALLS["toggle"])

    app.cfg["auto_monitor"] = True
    app.cfg["platform_chosen"] = False
    app.auto_start_monitor()
    check("没选平台时不动", CALLS["toggle"] == 0, CALLS["toggle"])

    app.cfg["platform_chosen"] = True
    saved_accounts = app.accounts
    app.accounts = [{"target": "x", "enabled": False}]
    app.auto_start_monitor()
    check("没有启用账号时不动", CALLS["toggle"] == 0, CALLS["toggle"])

    app.accounts = saved_accounts
    client.App.cookie_ready = lambda _self, _plat="douyin": False
    app.auto_start_monitor()
    check("没登录 Cookie 时不动", CALLS["toggle"] == 0, CALLS["toggle"])

    client.App.cookie_ready = lambda _self, _plat="douyin": True
    app.job_running = True          # 已经在跑
    app.auto_start_monitor()
    check("已经在监控时不动", CALLS["toggle"] == 0, CALLS["toggle"])
    app.job_running = False

    app.auto_start_monitor()
    check("条件都满足才开跑", CALLS["toggle"] == 1, CALLS["toggle"])
    safe_destroy(root)

    # ---- 4) 关掉设置后，新启动的实例不该自己跑 ----
    set_cfg(auto_monitor=False, platform_chosen=True)
    CALLS["toggle"] = 0
    root2 = tk.Tk()
    app2 = client.App(root2)
    pump(root2, 2.4)
    check("关掉后启动不自动跑", CALLS["toggle"] == 0, CALLS["toggle"])
    safe_destroy(root2)

    # 复原成默认（开），别把沙箱配置弄脏
    set_cfg(auto_monitor=True)

    io.open(os.path.join(HERE, "_auto_res.json"), "w", encoding="utf-8").write(
        json.dumps(RESULT, ensure_ascii=False, indent=2))
    print("RESULT_OK" if RESULT["ok"] else "RESULT_FAIL")
    print("checks=%d failed=%d" % (len(RESULT["checks"]),
                                   sum(1 for c in RESULT["checks"] if not c["ok"])))


if __name__ == "__main__":
    main()