"""界面结构自检：「本次下载」卡片 + 设置菜单的两层分类。

输出 `_ui.txt`（人看的文字）和 `_menu_tree.txt`（菜单树，可直接贴文档）。
"""
import io
import os
import sys
import tkinter as tk
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douyin_client as client  # noqa: E402

out = []
tree = []

CATEGORIES = ["账号与登录", "监控与通知", "下载", "直播", "界面与窗口", "数据与维护"]


def walk(widget):
    for child in widget.winfo_children():
        yield child
        for sub in walk(child):
            yield sub


def menu_items(menu):
    """(类型, 文字, 附加) 列表；分隔线类型为 separator"""
    items = []
    end = menu.index("end")
    if end is None:
        return items
    for i in range(end + 1):
        kind = menu.type(i)
        if kind == "separator":
            items.append((kind, "", ""))
            continue
        label = menu.entrycget(i, "label") if kind != "tearoff" else "tearoff"
        extra = ""
        if kind == "radiobutton":
            extra = "value=%s" % menu.entrycget(i, "value")
        elif kind == "checkbutton":
            extra = "toggle"
        items.append((kind, label, extra))
    return items


def main():
    root = tk.Tk()
    app = client.App(root)

    base = os.path.join(HERE, "data", "Volume", "Download")
    os.makedirs(base, exist_ok=True)
    vf = os.path.join(base, "2026-11-06 18.02.28-视频-飞鼠大帝^-我来也.mp4")
    if not os.path.exists(vf):
        with open(vf, "wb") as fh:
            fh.write(b"\x00" * 2000)
    app.on_downloaded({
        "nickname": "飞鼠大帝^",
        "items": [{"aweme_id": "111", "kind": "视频", "desc": "我来也#飞鼠大帝 #保安日记",
                   "files": [vf]}],
        "files": [vf],
    })
    root.update_idletasks()

    # ---------- 设置菜单：必须是「两层」（设置 → 分类 → 项） ----------
    settings = app.settings_menu
    top = menu_items(settings)
    cascades = [t[1] for t in top if t[0] == "cascade"]
    bare = [t[1] for t in top if t[0] not in ("cascade", "separator")]
    out.append("二级分类 %d 个：%s" % (len(cascades), cascades))
    out.append("分类名与预期一致：%s" % (cascades == CATEGORIES))
    out.append("二级直接挂具体项（应为空）：%s" % (bare,))

    deep, close_items, counts = [], [], []
    for i in range(settings.index("end") + 1):
        if settings.type(i) != "cascade":
            continue
        label = settings.entrycget(i, "label")
        sub = settings.nametowidget(settings.entrycget(i, "menu"))
        items = menu_items(sub)
        counts.append((label, len([x for x in items if x[0] != "separator"]),
                       len([x for x in items if x[0] == "separator"])))
        tree.append("├─ %s" % label)
        for kind, text, extra in items:
            if kind == "separator":
                tree.append("│  ├─ ────")
            elif kind == "cascade":
                deep.append("%s › %s" % (label, text))
                tree.append("│  ├─ [cascade] %s" % text)
            elif kind == "command":
                tree.append("│  ├─ %s" % text)
            else:
                tree.append("│  ├─ %-40s %s" % (text, extra))
                if "关闭窗口" in text:
                    close_items.append((label, text, extra))

    out.append("各分类项数（项, 分隔线）：%s" % (counts,))
    out.append("分类里若还有第三层子菜单（应为空）：%s" % (deep,))
    out.append("关闭窗口的两个选项：%s" % (close_items,))

    all_labels = []
    for i in range(settings.index("end") + 1):
        if settings.type(i) != "cascade":
            continue
        sub = settings.nametowidget(settings.entrycget(i, "menu"))
        all_labels.extend([x[1] for x in menu_items(sub)])
    out.append("首次运行下载开关已移除：%s" % (not any("首次运行自动下载" in x for x in all_labels)))

    # 关键项确实在（改名/漏挂都能抓到）
    must = {
        "账号与登录": ["添加账号…", "抖音扫码登录…", "更改抖音 Cookie…", "默认平台：抖音"],
        "监控与通知": ["启动软件后自动开始监控", "静默模式",
                   "桌面通知", "后台监听剪贴板",
                   "开始监控时核账历史作品",
                   "每轮检查都核账历史作品"],
        "下载": ["单独下载链接…", "补下载最近 3 天漏掉的作品…",
               "核账全部历史作品…", "默认保存位置…",
               "打开作品下载目录", "清除下载记录…"],
        "直播": ["直播自动录制", "直播独立轮询",
               "录制画质：原画", "打开直播回放目录"],
        "界面与窗口": ["深色", "关闭窗口：隐藏到托盘", "托盘图标",
                  "显示「直播录制」面板", "显示「本次下载」面板"],
        "数据与维护": ["打开数据目录", "打开日志文件",
                     "诊断作品计数…", "自动清理…"],
    }
    missing = []
    for i in range(settings.index("end") + 1):
        if settings.type(i) != "cascade":
            continue
        label = settings.entrycget(i, "label")
        sub = settings.nametowidget(settings.entrycget(i, "menu"))
        labels = [x[1] for x in menu_items(sub) if x[0] != "separator"]
        for want in must.get(label, []):
            # 允许菜单项带后缀说明（如「录制画质：原画（…720p…）」）
            if not any(x == want or x.startswith(want) for x in labels):
                missing.append("%s › %s" % (label, want))
    out.append("缺项（应为空）：%s" % (missing,))
    # 录制画质每个档位都要标出相对分辨率
    q_labels = [x for x in all_labels if x.startswith("录制画质：")]
    q_ok = len(q_labels) == 5 and all(("p" in x or "原始流" in x) for x in q_labels)
    out.append("录制画质档位（%d 项，含分辨率说明=%s）：%s" % (len(q_labels), q_ok, q_labels))
    bad = []
    if not q_ok:
        bad.append("录制画质菜单没有给每个档位标注分辨率：%s" % (q_labels,))
    if missing:
        bad.append("菜单缺项：%s" % missing)
    if cascades != CATEGORIES:
        bad.append("二级分类不对：%s" % cascades)
    if deep:
        bad.append("出现第三层子菜单：%s" % deep)
    all_labels = []
    for i in range(settings.index("end") + 1):
        if settings.type(i) != "cascade":
            continue
        sub = settings.nametowidget(settings.entrycget(i, "menu"))
        all_labels += [x[1] for x in menu_items(sub)]
    if any("拉流地址目录" in t for t in all_labels):
        bad.append("菜单里还有「打开拉流地址目录（txt）」：%s" % all_labels)
    if any("隐藏到任务栏" in t for t in all_labels):
        bad.append("菜单里还有「隐藏到任务栏」（已按要求删除）：%s" % all_labels)

    # ---------- 本次下载卡片 ----------
    buttons = []
    for w in walk(root):
        if isinstance(w, ttk.Button):
            try:
                if w.cget("text") in ("打开作品目录", "清空列表"):
                    buttons.append((w.cget("text"), w.winfo_x(), w.winfo_width(),
                                    int(bool(w.winfo_ismapped()))))
            except tk.TclError:
                continue
    out.append("本次下载卡片按钮：%s" % (buttons,))
    out.append("面板可见=%s 行数=%d 光标=%s" % (
        app.dl_visible, len(app.dl_tree.get_children()), app.dl_tree.cget("cursor")))
    out.append("表格绑定=%s" % (app.dl_tree.bind(),))
    texts = [b[0] for b in buttons]
    if "打开作品目录" not in texts or "清空列表" not in texts:
        bad.append("「本次下载」卡片按钮缺失：%s" % buttons)
    elif not all(b[3] for b in buttons):
        bad.append("「本次下载」卡片按钮没显示出来：%s" % buttons)
    if str(app.dl_tree.cget("cursor")) != "hand2":
        bad.append("本次下载表格光标不是 hand2")
    if not app.dl_tree.bind():
        bad.append("本次下载表格没有绑定点击/回车")
    if not app.dl_visible:
        bad.append("下载后「本次下载」面板没有出现")
    if getattr(app, "dl_close_btn", None) is None or getattr(app, "live_close_btn", None) is None:
        bad.append("直播录制/本次下载面板缺少关闭按钮")
    else:
        app.close_download_panel()
        if app.dl_visible or not app.dl_hidden:
            bad.append("「本次下载」关闭按钮没生效")
        app.show_download_panel()
        if not app.dl_visible:
            bad.append("「本次下载」无法从菜单重新显示")
        app.close_live_panel()
        if app.live_visible or not app.live_hidden:
            bad.append("「直播录制」关闭按钮没生效")
        app.show_live_panel()
        if not app.live_visible:
            bad.append("「直播录制」无法从菜单重新显示")

    if len(getattr(app, "cookie_badges", {}) or {}) != 2:
        bad.append("顶部 Cookie 指示灯不是两个")
    if getattr(app, "live_tree", None) is None:
        bad.append("直播录制面板没建出来")
    cols = list(app.tree["columns"])
    if "platform" not in cols or "live" not in cols:
        bad.append("账号表缺平台/直播列：%s" % cols)
    if "refresh" not in cols:
        bad.append("账号表缺「刷新」列：%s" % cols)
    if cols.index("refresh") != cols.index("live") + 1:
        bad.append("「刷新」列不在「直播」状态后面：%s" % (cols,))
    if "home" in cols:
        bad.append("账号表还留着重复的「主页链接」列：%s" % (cols,))
    if "target" in cols:
        bad.append("账号表还显示抖音号列（应改成悬停显示）：%s" % (cols,))
    if cols[1] != "nickname":
        bad.append("昵称不是第二列：%s" % (cols,))
    anchors = {c: str(app.tree.heading(c, "anchor") or "") for c in cols}
    if any(v != "w" for v in anchors.values()):
        bad.append("账号列表表头与内容没有左对齐：%s" % (anchors,))
    if not getattr(app, "full_audit_spin", None):
        bad.append("缺少「全量核账间隔」设置")
    else:
        out.append("全量核账间隔：%s 分钟" % app.full_audit_var.get())
    if not getattr(app, "full_audit_enabled_var", None):
        bad.append("「设置→监控与通知」缺少「自动全量核账」开关")
    else:
        app.cfg["full_audit_enabled"] = True
        app.full_audit_enabled_var.set(False)
        app.on_full_audit_enabled_change()
        off_ok = app.cfg.get("full_audit_enabled") is False
        app.full_audit_enabled_var.set(True)
        app.on_full_audit_enabled_change()
        out.append("自动全量核账开关：可关闭=%s" % off_ok)
        if not off_ok:
            bad.append("关闭自动全量核账开关没写配置")
    if not getattr(app, "auto_full_var", None) or not getattr(app, "scope_box", None):
        bad.append("缺少「启动后先全量核账」或「监控内容」控件")
    else:
        out.append("监控内容：%s" % app.scope_var.get())
    if not getattr(app, "acc_tip", None):
        bad.append("昵称悬停提示没挂上")
    else:
        kids0 = app.tree.get_children()
        if kids0:
            bbox = app.tree.bbox(kids0[0], "nickname")
            if bbox:
                class _Ev:
                    pass
                ev = _Ev()
                ev.x, ev.y = bbox[0] + 5, bbox[1] + 5
                tip_text = app.acc_tip._acc_text(ev)
                out.append("昵称悬停文字：%s" % tip_text)
                if not tip_text.startswith("抖音号：") or app.accounts[0].get("target") not in tip_text:
                    bad.append("悬停提示不是抖音号：%s" % tip_text)
    if not str(app.tree.cget("xscrollcommand") or "").strip():
        bad.append("账号表下方没有横向滚动条")
    kids = app.tree.get_children()
    if kids:
        vals = app.tree.item(kids[0], "values")
        acc0 = app.accounts[0]
        out.append("首行各列：%s" % (vals,))
        if len(vals) != len(cols):
            bad.append("行数据列数(%d)与表头列数(%d)不一致" % (len(vals), len(cols)))
        elif (vals[cols.index("refresh")] != app.refresh_cell(acc0)
              or vals[cols.index("save")] != app.save_dir_label(acc0)):
            bad.append("行数据与列顺序错位：%s" % (vals,))
    if "<Button-3>" not in app.tree.bind():
        bad.append("账号表没绑右键菜单（<Button-3>）")
    btn = getattr(app, "history_btn", None)
    if btn is None:
        bad.append("找不到「下载历史视频」按钮")
    else:
        root.update_idletasks()
        out.append("历史按钮高度：%s/%s" % (btn.winfo_height(), btn.winfo_reqheight()))
        if not btn.winfo_ismapped():
            bad.append("「下载历史视频」按钮没有显示")
        elif btn.winfo_height() < btn.winfo_reqheight() - 2:
            bad.append("「下载历史视频」按钮被压扁（高度 %s < %s）"
                       % (btn.winfo_height(), btn.winfo_reqheight()))
    if app.refresh_cell({}) != "↻ 刷新":
        bad.append("刷新列文字不对：%r" % app.refresh_cell({}))
    # 「保存位置」列点击应打开该账号的文件夹（把 open_path 换成记录器，避免真的弹资源管理器）
    kids = app.tree.get_children()
    if kids:
        opened = []
        orig_path = app.open_path
        app.open_path = lambda path, create=False: opened.append((path, create))
        try:
            app.open_account_save_dir(app.accounts[0])
        finally:
            app.open_path = orig_path
        if not opened:
            bad.append("点「保存位置」没有打开对应文件夹")
        else:
            out.append("保存位置点击 → 打开：%s（create=%s）" % opened[-1])
        if "<Button-1>" not in app.tree.bind():
            bad.append("账号表没绑单击（保存位置打不开）")
    live_cols = list(app.live_tree["columns"])
    if "act" not in live_cols:
        bad.append("直播录制表缺「操作」列：%s" % live_cols)
    if "<Button-1>" not in app.live_tree.bind():
        bad.append("直播录制表没绑点击（开始/暂停）")
    if any(w.cget("text") == "打开直播地址目录"
           for w in walk(root) if isinstance(w, ttk.Button)):
        bad.append("还有「打开直播地址目录」按钮（已不再保存直播地址）")

    # 直播面板：没开录制也要显示「直播中（未录制）」并给出「开始」
    app.live_info = {"k1": {"key": "k1", "nickname": "测试主播", "live": True,
                            "flv": "http://127.0.0.1:1/x.flv", "quality": "FULL_HD1",
                            "quality_label": "原画", "platform": "douyin"}}
    started = []
    if app.live_mgr:
        app.live_mgr.start = lambda key, **kw: started.append((key, kw.get("url")))
    app.refresh_live_panel()
    check_row = []
    for iid in app.live_tree.get_children():
        check_row.append(app.live_tree.item(iid, "values"))
    out.append("直播面板伪行：%s" % (check_row,))
    if not app.live_visible or not check_row:
        bad.append("直播中但未录制时，直播面板没有显示")
    elif "开始" not in check_row[0] or "直播中" not in str(check_row[0]):
        bad.append("直播面板「未录制」行文案不对：%s" % (check_row[0],))
    app.toggle_live_recording("k1")
    if not started:
        bad.append("点「开始」没有调用录制器")
    app.live_info = {}
    app.refresh_live_panel()
    # 监控列 = 直播圆点 + 账号级监控内容（跟全局/作品/直播/作品+直播）
    cell_live = app.live_cell({"live": True})
    cell_idle = app.live_cell({})
    if not (cell_live.startswith("● ") and "跟全局" in cell_live):
        bad.append("直播中时监控列文字不对：%s" % cell_live)
    if "跟全局" not in cell_idle or "●" in cell_idle:
        bad.append("未直播时监控列文字不对：%s" % cell_idle)
    scope_acc = {"nickname": "A"}
    scope_seen = []
    for val, text in app.SCOPE_CHOICES:
        scope_acc.pop("scope", None)
        if val:
            scope_acc["scope"] = val
        scope_seen.append(app.scope_label(scope_acc))
    if scope_seen != ["作品+直播", "作品", "直播", "跟全局"]:
        bad.append("账号级监控内容四选不对：%s" % (scope_seen,))
    out.append("监控列：直播中=%s 空闲=%s 四选=%s" % (cell_live, cell_idle, scope_seen))
    for iid in app.dl_tree.get_children():
        out.append("  行 %s → 文件 %s" % (iid, app.dl_files.get(iid)))

    # ---------- 界面去重：同功能按钮不再出现两次 ----------
    seen = {}
    dup = []
    for w in walk(root):
        if isinstance(w, (ttk.Button, tk.Button)):
            try:
                txt = w.cget("text")
            except tk.TclError:
                continue
            if not txt or not w.winfo_ismapped():
                continue
            seen.setdefault(txt, []).append(w)
            if len(seen[txt]) > 1 and txt not in dup:
                dup.append(txt)
    out.append("可见按钮（按文字去重后）：%s" % sorted(seen))
    out.append("重复的可见按钮（应为空）：%s" % dup)
    if dup:
        bad.append("同功能按钮重复出现：%s" % dup)
    if "设置 ▾" in seen:
        bad.append("标题栏还有第二个「设置 ▾」按钮")
    tops = [app.root.nametowidget(n).entrycget(i, "label")
            for n in [app.root.cget("menu")]
            for i in range(app.root.nametowidget(n).index("end") + 1)
            if app.root.nametowidget(n).type(i) == "cascade"]
    out.append("菜单栏入口：%s" % tops)
    if tops.count("设置") != 1:
        bad.append("设置入口不是唯一：%s" % tops)
    details = [w for w in walk(root) if isinstance(w, ttk.Checkbutton)]
    detail_txt = [w.cget("text") for w in details]
    out.append("日志面板开关：%s" % detail_txt)
    if "详细日志" not in detail_txt:
        bad.append("日志面板缺「详细日志」开关：%s" % detail_txt)

    # ---------- 新开关写配置 ----------
    app.notify_var.set(False)
    app.on_notify_change()
    app.dlvideo_var.set(True)
    app.dlimg_var.set(False)
    app.on_download_kind_change()
    out.append("开关写盘：notify_enabled=%s download_video=%s download_images=%s" % (
        app.cfg.get("notify_enabled"), app.cfg.get("download_video"),
        app.cfg.get("download_images")))
    app.notify_var.set(True)
    app.on_notify_change()
    app.dlimg_var.set(True)
    app.on_download_kind_change()

    # ---------- 账号行颜色提示 ----------
    cases = [({"enabled": True, "state": "监控中"}, "ok"),
             ({"enabled": True, "live": True, "state": "监控中"}, "live"),
             ({"enabled": True, "state": "刷新中"}, "busy"),
             ({"enabled": True, "state": "接口限流"}, "warn"),
             ({"enabled": True, "state": "出错: 网络"}, "warn"),
             ({"enabled": False, "state": "监控中"}, "off"),
             ({"enabled": True, "state": "待监控"}, "wait")]
    got_tags = [app.row_tags(dict(c))[0] for c, _ in cases]
    out.append("账号行颜色标签：%s" % got_tags)
    if got_tags != [e for _, e in cases]:
        bad.append("账号行颜色不对：%s" % got_tags)
    for name in ("busy", "live", "warn"):
        bg = str(app.tree.tag_configure(name, "background") or "")
        if not bg:
            bad.append("账号行标签 %s 没有背景色" % name)
    out.append("账号行背景色：live=%s ok底=%r" % (
        app.tree.tag_configure("live", "background"),
        app.tree.tag_configure("ok", "background")))

    # ---------- 中间分隔栏：两侧不能被拖没，列宽要跟着伸缩 ----------
    import hi_dpi
    old_sash = app.pane.sashpos(0)
    # 夹取下限按 DPI 缩放（和 on_sash_changed 一致）
    lo_px = hi_dpi.scale_px(app.SASH_LEFT_MIN)
    right_px = hi_dpi.scale_px(app.SASH_RIGHT_MIN)
    app.pane.sashpos(0, 50)
    app.on_sash_changed()
    root.update()
    left_min_ok = app.pane.sashpos(0) >= lo_px
    app.pane.sashpos(0, app.pane.winfo_width())
    app.on_sash_changed()
    root.update()
    right_min_ok = app.pane.winfo_width() - app.pane.sashpos(0) >= right_px

    # 关键不变量：拖到最窄时列宽总和不能超过表格宽度，
    # 否则出现横向滚动条、内容被切，看起来像"分隔栏拖了但列表不缩放"。
    app.pane.sashpos(0, lo_px)
    app.on_sash_changed()
    root.update()
    tree_w = app.tree.winfo_width()
    sum_col = sum(app.tree.column(c, "width") for c in app.tree["columns"])
    xview = app.tree.xview()
    no_hscroll = xview[1] >= 0.999        # xview=(first,last)；last=1.0 表示全部可见、无横向滚动；<1 表示有滚动
    fits = sum_col <= tree_w + 2
    stretch_cols = tuple(app.tree.column(c, "stretch") for c in app.tree["columns"])
    out.append("分隔栏夹取：左=%s 右=%s" % (left_min_ok, right_min_ok))
    out.append("最窄列宽：sumCol=%s treeW=%s 无横向滚动=%s 弹性列数=%s" % (
        sum_col, tree_w, no_hscroll, sum(stretch_cols)))
    app.pane.sashpos(0, old_sash)
    app.on_sash_changed()
    root.update()
    if not left_min_ok:
        bad.append("分隔栏能把左侧拖到看不见")
    if not right_min_ok:
        bad.append("分隔栏能把右侧拖到看不见")
    if not fits or not no_hscroll:
        bad.append("拖到最窄时列宽超出表格：sumCol=%s treeW=%s xview=%s" % (
            sum_col, tree_w, xview))
    if sum(stretch_cols) < 2:
        bad.append("弹性伸缩列不足 2 个，拖宽时会单列膨胀：%s" % (stretch_cols,))

    # ---------- 账号列表行高可调：在「设置→界面与窗口」菜单里 ----------
    import hi_dpi
    from tkinter import simpledialog as _sd
    style = ttk.Style(root)
    rh_before = style.lookup("Treeview", "rowheight")
    # 不写盘：mock save_config，避免行高测试污染 client_config.json 影响后续运行
    _orig_save = app.save_config
    app.save_config = lambda: None
    _orig_askint = _sd.askinteger
    try:
        _sd.askinteger = lambda *a, **k: 48
        app.set_row_height()
        root.update()
        rh_after = style.lookup("Treeview", "rowheight")
        expect_48 = hi_dpi.scale_px(48)
        row_h_ok = str(rh_after) == str(expect_48) and app.cfg.get("row_height") == 48
        # 越界夹取：对话框给 999
        _sd.askinteger = lambda *a, **k: 999
        app.set_row_height()
        clamp_ok = app.cfg.get("row_height") == 80
        # 取消（返回 None）不改配置
        app.cfg["row_height"] = 33
        _sd.askinteger = lambda *a, **k: None
        app.set_row_height()
        cancel_ok = app.cfg.get("row_height") == 33
    finally:
        _sd.askinteger = _orig_askint
        app.save_config = _orig_save
    # 菜单项存在（menubar 通过 root.config(menu=...) 设置，没有存成 app.menubar）
    look_menu = None
    menubar = None
    try:
        menubar = app.root.nametowidget(app.root["menu"])
    except Exception:
        pass
    if menubar is not None:
        try:
            for i in range(menubar.index("end") or 0 + 1):
                if menubar.type(i) != "separator" and menubar.entrycget(i, "label") == "设置":
                    settings_menu = menubar.nametowidget(menubar.entrycget(i, "menu"))
                    for j in range(settings_menu.index("end") or 0 + 1):
                        if settings_menu.type(j) != "separator" and "界面与窗口" in settings_menu.entrycget(j, "label"):
                            look_menu = settings_menu.nametowidget(settings_menu.entrycget(j, "menu"))
                            break
                    break
        except Exception:
            pass
    menu_has = False
    if look_menu is not None:
        try:
            menu_has = any("行高" in look_menu.entrycget(j, "label")
                           for j in range(look_menu.index("end") or 0 + 1)
                           if look_menu.type(j) != "separator")
        except Exception:
            pass
    app.cfg["row_height"] = 30
    out.append("行高(菜单)：默认=%s 设48=%s(期望%s) 夹取80=%s 取消=%s 菜单项=%s" % (
        rh_before, rh_after, expect_48, clamp_ok, cancel_ok, menu_has))
    if not row_h_ok:
        bad.append("行高设置没生效：%s != %s" % (rh_after, expect_48))
    if not clamp_ok:
        bad.append("行高越界没有夹取到 80：%s" % app.cfg.get("row_height"))
    if not cancel_ok:
        bad.append("取消行高对话框却改了配置")
    if not menu_has:
        bad.append("「设置→界面与窗口」菜单里没有「账号列表行高」项")

    root.destroy()
    io.open(os.path.join(HERE, "_ui.txt"), "w", encoding="utf-8").write("\n".join(out))
    io.open(os.path.join(HERE, "_menu_tree.txt"), "w", encoding="utf-8").write(
        "设置\n" + "\n".join(tree))
    if bad:
        print("RESULT_FAIL")
        for b in bad:
            print("  FAIL:", b)
        sys.exit(1)
    print("UI_OK")


if __name__ == "__main__":
    main()