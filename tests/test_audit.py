"""回归：① 补下载不再卡死（ask_main 主线程死锁）；② 历史作品全量核账。

全部离线：网络客户端、DouK 下载、记录读取都换成本地假实现。
"""
import io
import json
import logging
import os
import sys
import threading
import time
import tempfile
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douyin_client as client  # noqa: E402
import douyin_monitor as core  # noqa: E402

RESULT = {"checks": [], "ok": True}
CFG = os.path.join(HERE, "client_config.json")
DATA = os.path.join(HERE, "data")


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


class FakeDY:
    """假客户端：按页吐出预设作品，fetch_new_items 会一直翻到 has_more=False"""
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


def item(aid, ts):
    return {"aweme_id": str(aid), "create_time": ts, "desc": "作品%s" % aid,
            "media_type": 4 if False else 0}


def write_records(pairs):
    os.makedirs(DATA, exist_ok=True)
    rec = {}
    for aid in pairs:
        rec["douyin:%s" % aid] = {"time": "2026-01-01 00:00:00", "file": "x.mp4"}
    io.open(os.path.join(DATA, "下载记录.json"), "w", encoding="utf-8").write(
        json.dumps(rec, ensure_ascii=False, indent=2))


def main():
    old_askyesno = client.messagebox.askyesno
    old_douk = core.douk_download
    old_recent = core.recent_downloads
    old_finish = core._finish_download

    set_cfg(platform_chosen=True, silent_mode=False, auto_monitor=False, audit_on_start=True)
    root = tk.Tk()
    orig_toggle = client.App.toggle_monitor   # ⑤ 要还原真实现
    client.App.toggle_monitor = lambda _self: None
    app = client.App(root)
    root.update_idletasks()

    # ---------------- ① ask_main 在主线程里不能再死锁 ----------------
    client.messagebox.askyesno = lambda *a, **k: True
    t0 = time.time()
    ans = app.ask_main("测试", "主线程里调用应该直接弹窗、不阻塞")
    cost = time.time() - t0
    check("主线程调 ask_main 立刻返回（旧实现会卡死）", cost < 1.0, "耗时 %.3fs" % cost)
    check("主线程调 ask_main 能拿到答案", ans is True, ans)

    # 静默模式下仍然是「自动按否」，不弹窗
    app.cfg["silent_mode"] = True
    client.messagebox.askyesno = lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该弹窗"))
    check("静默模式不弹窗、返回 False", app.ask_main("x", "y") is False)
    app.cfg["silent_mode"] = False

    # ---------------- ② 补下载入口不再走 ask_main ----------------
    called = {"ask_main": 0, "threads": 0}
    app.ask_main = lambda *a, **k: called.__setitem__("ask_main", called["ask_main"] + 1) or False
    client.messagebox.askyesno = lambda *a, **k: True

    def fake_worker(targets):
        called["threads"] += 1
    app._backfill_worker = fake_worker

    t0 = time.time()
    app.backfill_missing()
    cost = time.time() - t0
    check("点补下载不卡死（<1s 返回）", cost < 1.0, "耗时 %.3fs" % cost)
    check("补下载不再用 ask_main", called["ask_main"] == 0, called["ask_main"])
    check("补下载确实起了后台线程", called["threads"] == 1, called["threads"])
    check("补下载有忙标志", app.backfill_busy is True)
    app.on_backfill_done(3)
    check("补下载结束清忙标志", app.backfill_busy is False)
    check("补下载结果写进状态栏", "补下载完成" in str(app.status_override[0]),
          app.status_override[0])

    # ---------------- ③ find_missing_all：只看下载记录、不受 3 天限制 ----------------
    write_records(["1001"])          # 1001 已下载，其余缺
    pages = [[item(1004, 4000), item(1003, 3000)], [item(1002, 2000), item(1001, 1000)]]
    dy = FakeDY(pages)
    cfg = {"sec_user_id": "SEC", "platform": "douyin"}
    missing, total, _mt = core.find_missing_all(cfg, dy, limit=10, max_pages=10)  # 恢复适配：三元组
    ids = [x["aweme_id"] for x in missing]
    check("核账翻完了整个主页", total == 4, total)
    check("核账只挑没下载记录的", ids == ["1002", "1003", "1004"], ids)
    check("核账按时间从旧到新", [x["create_time"] for x in missing] == [2000, 3000, 4000])

    dy2 = FakeDY([[item(9, 9000), item(8, 8000)]])
    write_records([])
    missing2, _, _mt2 = core.find_missing_all(cfg, dy2, limit=1)  # 恢复适配：三元组
    check("核账 limit 生效", len(missing2) == 1, len(missing2))

    # ---------------- ④ _audit_worker：只报告 vs 真补下载 ----------------
    downloads = []

    dests = []

    def fake_download(cfg2, items, platform="douyin", dy=None, dest="", alt_dirs=()):
        downloads.append([str(x.get("aweme_id") if isinstance(x, dict) else x) for x in items])
        dests.append(dest)
        return {"下载视频作品": len(items)}, [], "fake"

    core.douk_download = fake_download
    core.download_known_items = fake_download
    core.recent_downloads = lambda cfg2, since, uid="": []
    core._finish_download = lambda cfg2, files, stats, text, items, report: (
        report.setdefault("files", []) and None or report.setdefault("items", []))

    write_records(["2001"])
    dy3 = FakeDY([[item(2003, 3000), item(2002, 2000)], [item(2001, 1000)]])
    app.get_client = lambda platform=None: dy3
    app.cookie_ready = lambda platform=None: True
    app.accounts = [{"target": "测试A", "nickname": "测试A", "enabled": True,
                     "sec_user_id": "SEC_A", "platform": "douyin"}]

    downloads.clear()
    app.start_audit(trigger="手动核账", dry_run=True)
    pump(root, 1.5)
    check("只报告模式会翻主页但不下载", downloads == [], downloads)
    check("只报告模式结束会复位 audit_running", app.audit_running is False)
    check("只报告模式有状态提示", "核账完成" in str(app.status_override[0]),
          app.status_override[0])

    # 真补下载
    dy4 = FakeDY([[item(2003, 3000), item(2002, 2000)], [item(2001, 1000)]])
    app.get_client = lambda platform=None: dy4
    downloads.clear()
    app.start_audit(trigger="手动核账", dry_run=False)
    pump(root, 1.5)
    got = [u for chunk in downloads for u in chunk]
    check("真补下载抓到缺的 2 个", len(got) == 2, got)
    check("下载的是缺的作品", sorted(got) == ["2002", "2003"], got[:2])
    check("真补下载后 audit_running 复位", app.audit_running is False)

    # ---------------- ⑤ 开始监控会触发核账 ----------------
    triggered = {"n": 0}
    orig_start_audit = app.start_audit          # ⑥ 还要用真实现
    app.start_audit = lambda targets=None, trigger="", dry_run=False: triggered.__setitem__(
        "n", triggered["n"] + 1) or True
    client.App.toggle_monitor = orig_toggle    # 用真实现验证「开始监控顺手核账」
    app.cfg["audit_on_start"] = True
    app.monitor_loop = lambda: None
    app.stop_event.clear()
    app.accounts = [{"target": "测试A", "nickname": "测试A", "enabled": True,
                     "sec_user_id": "SEC_A", "platform": "douyin"}]
    app.cookie_ready = lambda platform=None: True
    app.toggle_monitor()
    check("开始监控会顺手核账", triggered["n"] == 1, triggered["n"])
    app.stop_event.set()
    app.job_running = False
    app.monitor_thread = None

    triggered["n"] = 0
    app.cfg["audit_on_start"] = False
    app.toggle_monitor()
    check("关掉开关就不核账", triggered["n"] == 0, triggered["n"])
    app.stop_event.set()
    app.job_running = False
    app.monitor_thread = None

    # ---------------- ⑥ 全量翻页去重 + 计数口径 ----------------
    dup = FakeDY([[item(1, 1000), item(2, 2000)], [item(2, 2000), item(3, 3000)]])
    allp = core.fetch_all_posts(dup, "SEC", count=18, max_pages=10)
    ids_all = [x["aweme_id"] for x in allp]
    check("全量翻页按 id 去重（重复页不会算两次）", ids_all == ["1", "2", "3"], ids_all)

    txt = client.App.audit_count_text(402, 399)
    check("计数口径：主页 402 接口 399 会写清差额", "402" in txt and "399" in txt and "差 3" in txt, txt)
    check("计数口径：相等时不啰嗦",
          "399" in client.App.audit_count_text(399, 399)
          and "一致" in client.App.audit_count_text(399, 399),
          client.App.audit_count_text(399, 399))
    check("计数口径：拿不到 aweme_count 时用接口数",
          "42" in client.App.audit_count_text(0, 42)
          and "资料页没给计数" in client.App.audit_count_text(0, 42),
          client.App.audit_count_text(0, 42))
    check("计数口径：差额说明是「不对外公开」",
          "不对外公开" in client.App.audit_count_text(8, 7), client.App.audit_count_text(8, 7))

    # ---------------- ⑦ 作品计数诊断 ----------------
    diag_dy = FakeDY([[item(4001, 1000), item(4002, 2000)],
                      [item(4002, 2000), item(4003, 3000)]], declared=9)
    diag = core.diagnose_posts({"sec_user_id": "SEC", "platform": "douyin"}, diag_dy)
    check("诊断：资料页计数", diag["declared"] == 9, diag["declared"])
    check("诊断：去重条数", diag["fetched"] == 3, diag["fetched"])
    check("诊断：原始条数含重复", diag["raw"] == 4, diag["raw"])
    check("诊断：末页 has_more 为假", diag["has_more"] is False, diag["has_more"])
    check("诊断：分年/分类型有统计", diag["by_year"] and diag["by_kind"], diag)
    check("诊断：时间范围取到最旧最新",
          diag["oldest"][0] == 1000 and diag["newest"][0] == 3000, diag["oldest"])
    check("诊断：结论判据（原始≠去重）", diag["raw"] != diag["fetched"], diag)

    records2 = []

    class Cap2(logging.Handler):
        def emit(self, rec):
            records2.append(rec.getMessage())

    cap2 = Cap2()
    core.log.addHandler(cap2)
    core.log.setLevel(logging.INFO)
    app.log_diagnosis("测试A", diag)
    core.log.removeHandler(cap2)
    blob2 = "\n".join(records2)
    check("诊断日志有「作品计数诊断」", "作品计数诊断" in blob2, blob2[:120])
    check("诊断日志有翻页/去重信息", "去重" in blob2 and "翻" in blob2, blob2[:200])
    check("诊断日志给出分页异常结论", "异常" in blob2, blob2[-160:])

    app.start_audit = orig_start_audit          # 还原真实现
    # 核账日志里必须出现「差 N 个」，并且不再出现「未见过的新作品」
    records = []

    class Cap(logging.Handler):
        def emit(self, rec):
            records.append(rec.getMessage())

    cap = Cap()
    core.log.addHandler(cap)
    old_level = core.log.level
    core.log.setLevel(logging.INFO)
    write_records(["3001"])
    dy5 = FakeDY([[item(3001, 1000), item(3002, 2000)]], declared=5)
    app.get_client = lambda platform=None: dy5
    app.cookie_ready = lambda platform=None: True
    app.start_audit(trigger="手动核账", dry_run=True)
    pump(root, 1.5)
    core.log.removeHandler(cap)
    core.log.setLevel(old_level)
    blob = "\n".join(records)
    check("核账日志说明接口差额", "接口计数 5，3 条不公开" in blob, blob[-200:])
    check("核账日志不再自称「未见过的新作品」", "未见过的新作品" not in blob, blob[:200])
    check("翻页细节降为细节级（INFO 里不再出现「全量翻页」）",
          "全量翻页" not in blob, blob[:200])

    # 复原
    client.messagebox.askyesno = old_askyesno
    # ---------------- ⑤ 核账尊重「首次检查选了不下载」+ 补下载落到账号历史文件夹 ----------------
    downloads.clear()
    dests.clear()
    app.state["accounts"]["SEC_A"] = {"seen": {"2002": {"skip": True}}}
    write_records([])
    dy4 = FakeDY([[item(2002, 2000), item(2003, 3000)]])
    app.get_client = lambda platform=None: dy4
    app.cookies = getattr(app, "cookies", {})
    app.cookie_ready = lambda platform=None: True
    acc = {"target": "测试A", "nickname": "测试A", "enabled": True,
           "sec_user_id": "SEC_A", "platform": "douyin"}
    # 2003 的文件其实已经在账号文件夹里：核账应先按文件名认出来、只补记录，不再请求下载
    hist = core.history_account_dir(dict(app.cfg), acc)
    os.makedirs(hist, exist_ok=True)
    it2003 = item(2003, 3000)
    name2003 = core.douk_dl.build_name(it2003, "video") + ".mp4"
    with open(os.path.join(hist, name2003), "wb") as f:
        f.write(b"x" * 1024)
    res5 = app.audit_account(acc, trigger="测试", quiet=True)
    got_ids = list(downloads[0] if downloads else [])
    check("核账跳过 skip=True 的作品", got_ids == [], got_ids)
    check("文件已在本地时不再请求下载", int(res5.get("already") or 0) == 1, res5)
    rec5 = core.douk_dl.load_records(core.data_root(dict(app.cfg)))
    shown5 = (rec5.get("douyin:2003") or {}).get("file") or ""
    check("已在本地的作品只补记录、指向账号历史文件夹",
          "发布作品" in shown5, shown5)
    top5 = (app.state.get("accounts", {}).get("SEC_A") or {}).get("audit_top_ts")
    check("核账没缺口时记下已核到的时间（下轮可增量）", int(top5 or 0) == 3000, top5)
    full5 = (app.state.get("accounts", {}).get("SEC_A") or {}).get("full_audit_ts")
    check("首次/隔天核账会记下全量时间", int(full5 or 0) > 0, full5)

    # ⑩ 到点的全量核账必须真翻页，不能被「主页作品数没变」短路掉
    import logging as _logging
    recs10 = []
    cap10 = _logging.Handler()
    cap10.emit = lambda r: recs10.append(r.getMessage())
    core.log.addHandler(cap10)
    old_lv = core.log.level
    core.log.setLevel(_logging.INFO)
    st10 = (app.state.get("accounts", {}).get("SEC_A") or {})
    st10["declared"] = 2                       # 和主页报的 declared 一样
    st10["full_audit_ts"] = int(time.time()) - 3600   # 1 小时前全量过
    app.cfg["full_audit_interval"] = 30        # 间隔 30 分钟 → 现在到点
    dy10 = FakeDY([[item(2002, 2000), item(2003, 3000)]], declared=2)
    app.get_client = lambda platform=None: dy10
    res10 = app.audit_account(acc, trigger="每轮检查", quiet=True)
    core.log.removeHandler(cap10)
    core.log.setLevel(old_lv)
    blob10 = "\n".join(recs10)
    check("到点全量：真的重新翻了页（FakeDY 被调用）", dy10.calls >= 1, dy10.calls)
    check("到点全量：不再出现「作品数没变跳过全量」",
          "跳过全量" not in blob10, blob10[-200:])
    check("到点全量：日志写明已到设定间隔",
          "到设定间隔" in blob10, blob10[-200:])
    check("到点全量：返回里作品数正确", int(res10.get("posts") or 0) == 2, res10)

    # ⑪ 用户关闭「自动全量核账」后，定时到点也不做全量（手动触发仍可）
    recs11 = []
    cap11 = _logging.Handler()
    cap11.emit = lambda r: recs11.append(r.getMessage())
    core.log.addHandler(cap11)
    core.log.setLevel(_logging.INFO)
    st10["full_audit_ts"] = int(time.time()) - 3600   # 又到点了
    app.cfg["full_audit_enabled"] = False            # 用户关掉自动全量
    dy11 = FakeDY([[item(2002, 2000), item(2003, 3000)]], declared=2)
    app.get_client = lambda platform=None: dy11
    app.audit_account(acc, trigger="每轮检查", quiet=True)
    core.log.removeHandler(cap11)
    core.log.setLevel(old_lv)
    blob11 = "\n".join(recs11)
    check("关闭自动全量后到点不做全量（无「到设定间隔」日志）",
          "到设定间隔" not in blob11, blob11[-200:])
    app.cfg["full_audit_enabled"] = True
    app.cfg["full_audit_interval"] = 1440

    # ⑨ 增量核账：上次核过的旧作品不再重复扫描
    tmp9 = tempfile.mkdtemp(prefix="audit_inc_")
    cfg9 = {"data_dir": tmp9, "sec_user_id": "SEC_FAKE", "platform": "douyin"}
    dy9 = FakeDY([[item(9001, 5000)], [item(9000, 1000)]])
    m9, tot9, n9 = core.find_missing_all(cfg9, dy9, since_ts=3000)
    check("增量核账只核对上次之后的作品", [str(x["aweme_id"]) for x in m9] == ["9001"], m9)
    check("增量核账记下最新时间", int(cfg9.get("_audit_newest_ts") or 0) == 5000, cfg9.get("_audit_newest_ts"))
    fresh9 = core.fetch_new_items(FakeDY([[item(9002, 6000)]]), "SEC_FAKE", {}, known={"9002"})
    check("下载记录里已有的作品不再当新作品", fresh9 == [], fresh9)


    core.douk_download = old_douk
    core.recent_downloads = old_recent
    core._finish_download = old_finish
    set_cfg(audit_on_start=True)
    root.destroy()

    io.open(os.path.join(HERE, "_audit_res.json"), "w", encoding="utf-8").write(
        json.dumps(RESULT, ensure_ascii=False, indent=2))
    print("RESULT_OK" if RESULT["ok"] else "RESULT_FAIL")
    print("checks=%d failed=%d" % (len(RESULT["checks"]),
                                   sum(1 for c in RESULT["checks"] if not c["ok"])))
    for c in RESULT["checks"]:
        if not c["ok"]:
            print("  FAIL:", c["name"], c["extra"])


if __name__ == "__main__":
    main()