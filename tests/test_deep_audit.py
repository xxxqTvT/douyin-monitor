# -*- coding: utf-8 -*-
"""回归：每轮检查 / 手动刷新都要做「全量核账」（隐藏作品后来公开不漏抓）。

跑法（在 _t_sandbox 目录里）：python test_deep_audit.py
全部离线：假客户端 + 假 DouK + 假下载记录。
"""
import io
import json
import logging
import os
import sys
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douyin_client as client  # noqa: E402
import douyin_monitor as core  # noqa: E402

CHECKS = []
CFG = os.path.join(HERE, "client_config.json")
DATA = os.path.join(HERE, "data")


def check(name, cond, extra=""):
    CHECKS.append({"name": name, "ok": bool(cond), "extra": str(extra)[:400]})


def pump(root, seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def set_cfg(**kw):
    cfg = json.load(io.open(CFG, encoding="utf-8"))
    cfg.update(kw)
    io.open(CFG, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))


def item(aid, ts):
    return {"aweme_id": str(aid), "create_time": ts, "desc": "作品%s" % aid, "media_type": 0}


class FakeDY:
    platform = "douyin"

    def __init__(self, pages, declared=0):
        self.pages = pages
        self.calls = 0
        self.declared = declared

    def reload_cookie(self):
        pass

    def resolve_sec_uid(self, target):
        return "SEC_FAKE"

    def fetch_profile(self, sec):
        return {"aweme_count": self.declared, "uid": "UID_FAKE", "nickname": "测试A"}

    def fetch_posts_page(self, sec, count=18, cursor="0"):
        if self.calls >= len(self.pages):
            return [], False, "0"
        items = self.pages[self.calls]
        self.calls += 1
        return items, self.calls < len(self.pages), str(self.calls)


def write_records(pairs):
    os.makedirs(DATA, exist_ok=True)
    rec = {("douyin:%s" % a): {"time": "2026-01-01 00:00:00", "file": "x.mp4"} for a in pairs}
    io.open(os.path.join(DATA, "下载记录.json"), "w", encoding="utf-8").write(
        json.dumps(rec, ensure_ascii=False, indent=2))


def main():
    old_douk = core.douk_download
    old_recent = core.recent_downloads
    old_finish = core._finish_download

    set_cfg(platform_chosen=True, auto_monitor=False, silent_mode=False,
            audit_on_start=False, audit_every_round=True)
    write_records(["1001", "1002"])

    root = tk.Tk()
    client.App.toggle_monitor = lambda _self: None
    client.App.cookie_ready = lambda _self, _plat="douyin": True
    app = client.App(root)
    pump(root, 0.2)

    # ---------------- ① 每轮检查：每个账号都调用一次全量核账 ----------------
    calls = []

    def fake_audit(acc, trigger="每轮检查", dry_run=False, quiet=False):
        calls.append((acc.get("nickname") or acc.get("target"), trigger, quiet))
        return {"posts": 2, "missing": 0, "items": 0, "files": 0}

    app.audit_account = fake_audit
    app.check_account = lambda *_a, **_k: {"items": 0, "files": 0}
    app.get_client = lambda *_a, **_k: FakeDY([[item(1001, 1000), item(1002, 2000)]], declared=2)

    stats = app.monitor_cycle()
    check("每轮检查里每个启用账号都核了一次账",
          len(calls) == len([a for a in app.accounts if a.get("enabled", True)]), calls)
    check("每轮核账用的是「每轮检查」触发且安静模式", all(c[1] == "每轮检查" and c[2] for c in calls),
          calls[:2])
    check("本轮统计里带上核账账号数", int(stats.get("deep") or 0) == len(calls), stats)
    check("核账都在本地时缺口统计为 0", int(stats.get("deep_missing") or 0) == 0, stats)

    # ---------------- ② 汇总行说清楚核账结论 ----------------
    logs = []

    class Cap(logging.Handler):
        def emit(self, rec):
            logs.append(rec.getMessage())

    cap = Cap()
    core.log.addHandler(cap)
    old_level = core.log.level
    core.log.setLevel(logging.DEBUG)

    # 全量核账（deep_full 覆盖全部账号）→ 历史作品都在本地
    app.log_round_summary(dict(stats, cost=12, deep_full=int(stats.get("deep") or 0)), 12, 240)
    line = logs[-1] if logs else ""
    check("全量核账汇总行写「全量核账无缺失」", "全量核账无缺失" in line, line)
    check("汇总行仍然只有一行", line.count("第 ") == 1, line)

    # 增量核账（没有全量）→ 不写「没有缺的」，写「未检测到发布新作品」
    logs[:] = []
    app.log_round_summary(dict(stats, cost=12, deep_full=0), 12, 240)
    line2 = logs[-1] if logs else ""
    check("增量核账汇总行写「未检测到发布新作品」", "未检测到发布新作品" in line2, line2)
    check("增量核账汇总行不写「无缺失」结论", "全量核账无缺失" not in line2, line2)

    logs[:] = []
    app.log_round_summary({"checked": 2, "new": 0, "files": 0, "failed": 0,
                           "deep": 2, "deep_missing": 3, "deep_posts": 40}, 20, 240)
    check("有缺口时汇总行写「历史核账补了 3 个」", "历史核账补了 3 个" in (logs[-1] if logs else ""),
          logs[-1] if logs else "")

    # ---------------- ③ 关掉开关就不核账 ----------------
    app.cfg["audit_every_round"] = False
    calls.clear()
    app.monitor_cycle()
    check("audit_every_round=False 时不再核账", not calls, calls)
    app.cfg["audit_every_round"] = True

    # ---------------- ④ 手动刷新也核账（trigger=手动刷新） ----------------
    calls.clear()
    acc = app.accounts[0]
    acc.pop("refresh_busy", None)
    app._refresh_worker(acc)
    check("手动刷新走了一次核账", len(calls) == 1, calls)
    check("手动刷新的核账 trigger 是「手动刷新」", calls and calls[0][1] == "手动刷新", calls)
    check("手动刷新结束清掉 refresh_busy", not acc.get("refresh_busy"), acc.get("refresh_busy"))

    # ---------------- ⑤ audit_account 自己：安静模式没缺口不写日志 ----------------
    del app.audit_account            # 还原真实现
    (app.state.get("accounts", {}).get("SEC_A") or {}).pop("audit_top_ts", None)  # 用全新数据，别继承上一段的水位
    logs[:] = []
    app.get_client = lambda *_a, **_k: FakeDY([[item(1001, 1000), item(1002, 2000)]], declared=2)
    res = app.audit_account(acc, trigger="每轮检查", quiet=True)
    _off = [m for m in logs if "【测试A】" in m and "增量核账进度" not in m and "到设定间隔" not in m]
    check("没缺口时安静模式一条日志都不写", not _off, _off)
    check("返回里有主页条数", int(res.get("posts") or 0) == 2, res)
    check("返回里缺口为 0", int(res.get("missing") or 0) == 0, res)

    # ---------------- ⑥ 有缺口：写一行 + 真补下载 ----------------
    write_records(["1001"])          # 1002 没下过 → 缺口 1 个
    downloaded = []

    def fake_douk(cfg, items, platform="douyin", dy=None, dest="", alt_dirs=()):
        return {"下载视频作品 1 个": 1}, [os.path.join(HERE, "fake_1002.mp4")], "ok"

    def fake_recent(cfg, since, uid=""):
        return [os.path.join(HERE, "fake_1002.mp4")]

    def fake_finish(cfg, files, stats, text, items, report):
        report["files"] = list(files)
        report["items"] = [{"kind": "视频", "desc": "作品1002"}]
        return files

    core.douk_download = fake_douk
    core.download_known_items = fake_douk
    core.recent_downloads = fake_recent
    core._finish_download = fake_finish
    app.record_session = lambda *_a, **_k: None
    logs[:] = []
    res2 = app.audit_account(acc, trigger="每轮检查", quiet=True)
    joined = "\n".join(logs)
    check("有缺口时即使安静模式也写一行", "缺 1 个" in joined, joined[-200:])
    check("缺口行说清楚「开始补下载」", "开始补下载" in joined, joined[-200:])
    check("返回值里缺口数为 1", int(res2.get("missing") or 0) == 1, res2)
    check("返回值里文件数为 1", int(res2.get("files") or 0) == 1, res2)
    check("补到的作品会推给「本次下载」面板",
          any(k == "downloaded" for k, _v in list(app.q.queue) or []), list(app.q.queue)[:2])

    core.log.removeHandler(cap)
    core.log.setLevel(old_level)
    core.douk_download = old_douk
    core.recent_downloads = old_recent
    core._finish_download = old_finish
    try:
        root.destroy()
    except Exception:  # noqa: BLE001
        pass


main()
path = os.path.join(HERE, "_deep_res.json")
io.open(path, "w", encoding="utf-8").write(json.dumps(
    {"checks": CHECKS, "failed": sum(1 for c in CHECKS if not c["ok"])}, ensure_ascii=False, indent=1))
print("checks=%d failed=%d" % (len(CHECKS), sum(1 for c in CHECKS if not c["ok"])))
print("RESULT_OK" if all(c["ok"] for c in CHECKS) else "RESULT_FAIL")
for c in CHECKS:
    if not c["ok"]:
        print("  FAIL:", c["name"], c["extra"][:200].encode("utf-8", "replace").decode("utf-8"))