"""回归：监控线程异常退出要记日志并自动重启（最多 3 次）。"""
import os
import sys
import threading
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douyin_client as client  # noqa: E402

RESULT = {"checks": [], "ok": True}


def check(name, cond, extra=""):
    RESULT["checks"].append({"name": name, "ok": bool(cond), "extra": str(extra)[:300]})
    if not cond:
        RESULT["ok"] = False


def dead():
    th = threading.Thread(target=lambda: None)
    th.start()
    th.join()
    return th


def main():
    root = tk.Tk()
    real_toggle = client.App.toggle_monitor
    client.App.toggle_monitor = lambda *_a, **_k: None
    app = client.App(root)
    root.update_idletasks()

    app.monitor_loop = lambda: None
    app.stop_event.clear()
    app.job_running = True
    app._monitor_restarts = 0
    app.monitor_thread = dead()

    app.watch_monitor()
    check("第一次意外退出会重启", app._monitor_restarts == 1, app._monitor_restarts)
    check("重启后仍然是运行状态", app.job_running is True)

    for _i in range(3):
        app.monitor_thread = dead()
        app.watch_monitor()
    check("最多重启 3 次后停下", app._monitor_restarts == 4 and app.job_running is False,
          (app._monitor_restarts, app.job_running))
    check("停下时状态写明异常", app.status_var.get().startswith("已停止（异常）"),
          app.status_var.get())

    app.job_running = True
    app.stop_event.set()
    app.monitor_thread = dead()
    before = app._monitor_restarts
    app.watch_monitor()
    check("手动停止后不重启", app._monitor_restarts == before, app._monitor_restarts)
    app.stop_event.clear()

    app.job_running = True
    app._monitor_loop_body = lambda: (_ for _ in ()).throw(RuntimeError("模拟崩溃"))
    client.App.monitor_loop(app)
    check("线程崩溃后交给看门狗重启（job_running 仍为 True）", app.job_running is True)
    check("_idle 是方法，没有被实例属性覆盖", callable(app._idle))
    app.stop_event.set()
    _t0 = time.time()
    app._idle(time.time() + 0.5)
    check("_idle 能正常返回", time.time() - _t0 < 2)
    app.stop_event.clear()
    log_path = os.path.join(HERE, "monitor.log")
    tail = ""
    if os.path.isfile(log_path):
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            tail = fh.read()[-4000:]
    check("崩溃堆栈写进了日志", "监控线程异常退出" in tail, tail[-300:])

    # 刚自动开始监控时的误触停止保护
    app.stop_event.clear()
    app.cycle_count = 0
    app._monitor_started_at = time.time()
    app.monitor_thread = threading.Thread(target=lambda: time.sleep(3))
    app.monitor_thread.start()
    real_toggle(app, "测试-按钮")
    check("刚开 15 秒内的停止请求被忽略", app.stop_event.is_set() is False)
    check("忽略时提示再点一次", "再点一次" in app.status_override[0], app.status_override)

    app._monitor_started_at = time.time() - 60
    real_toggle(app, "测试-按钮")
    check("超过 15 秒后可以正常停止", app.stop_event.is_set() is True)

    app.stop_event.clear()
    app.cycle_count = 2
    app._monitor_started_at = time.time()
    app.monitor_thread = threading.Thread(target=lambda: time.sleep(3))
    app.monitor_thread.start()
    real_toggle(app, "测试-按钮")
    check("已经跑过轮次后可以正常停止", app.stop_event.is_set() is True)
    app.stop_event.clear()

    root.destroy()
    bad = [c for c in RESULT["checks"] if not c["ok"]]
    print("RESULT_OK" if RESULT["ok"] else "RESULT_FAIL")
    for c in bad:
        print("  FAIL:", c["name"], c.get("extra"))
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
