# 回归脚本（临时沙箱，不碰真实数据）

只为了「改完代码快速自查」用，不是正式测试框架。所有脚本都在**复制出来的沙箱**里跑，
`client_config.json` / `data\` 都是假的，真实目录 `抖音监控\` 不会被读到或写入。

## 用法

```bash
cd /d D:\deepseek\dshzijian\源代码
python tests\make_sandbox.py            # 默认建在 ..\_t_sandbox
cd ..\_t_sandbox
python test_features.py                 # → RESULT_OK，27 项断言（面板行→文件、关闭行为）
python test_auto_start.py               # → RESULT_OK，14 项断言（启动后自动监控）
python test_audit.py                     # → RESULT_OK，47 项断言（补下载不死锁 + 历史核账 + 计数诊断 + 文案两句拆分）
python test_log_menu.py                  # → RESULT_OK，42 项断言（日志显示 + 账号右键菜单）
python verify_ui.py                     # → _ui.txt（按钮几何、绑定、菜单结构）+ _menu_tree.txt（菜单树）
```

`test_features.py` 覆盖（2026-11-09 那版，全绿）：

- 「本次下载」行 → 文件映射：视频 1 个、图集 2 个、被跳过 0 个；
- 点行打开文件（`shell_open_file` 被替换成记录器，不会真的拉播放器）、连点 1.5 秒去重；
- 图集（多文件）打开所在目录；没有文件时弹提示；
- 文件被手动挪到 `Volume\UIDxxx_昵称_发布作品\` 后，按同名找回；
- `douyin_monitor._fill_report` 按文件名时间戳给每条作品认领 `files`，同秒两条不串；
- 关闭窗口设置写入 `client_config.json`；`minimize` 时窗口 withdraw/iconic 但 `stop_event` 未置位、
  `live_mgr` 还在、`show_from_tray()` 能恢复；`exit` 时窗口销毁、`stop_event` 已置位。

`test_auto_start.py` 覆盖（2026-11-09 补）：默认 `auto_monitor=True`、建 App 后 1.8 秒自动调一次 `toggle_monitor` 且只调一次、菜单开关写配置并落盘、没选平台 / 没有启用账号 / 没登录 Cookie / 已在监控时都不触发、关掉设置后新启动的实例不自动跑。

`test_audit.py` 里另有「作品计数诊断」一组（v1.5.5）：`core.diagnose_posts` 的主页显示的作品数/去重条数/原始条数/末页 has_more/分年分类型/时间范围，以及 `App.log_diagnosis` 的输出里必须有「作品计数诊断」「去重」「翻 N 页」和分页异常结论；`audit_count_text` 的三档文案（一致 / 差 N 条不对外公开 / 资料页没给计数）。

`test_log_menu.py` 覆盖（v1.5.4）：日志级别中文化（`[信息]/[注意]/[错误]`）与三档配色、成功语汇上色、连续重复折叠成「重复 ×N」、只看提醒/错误过滤（只影响界面）、清空日志重置折叠状态；`monitor_cycle` 返回计数、`log_round_summary` 只写一行且不再出现「暂无更新」；右键菜单（`on_tree_context`）项齐全、右键选中该行、多选保留选区并提示「已选 N 个账号」、单账号才可用的项在多选时置灰、直播录制当前值打勾、复制主页链接/账号信息进剪贴板、副标题提示右键。

`test_audit.py` 覆盖（v1.5.2）：**主线程**里调 `ask_main` 会立刻返回（旧实现把 UI 线程自己等死，就是「补下载一点就卡死」那个 bug）、`backfill_missing` 不再走 `ask_main` 且 1 秒内返回、有忙标志与状态栏结果；`find_missing_all` 全量比对（不受 3 天限制、只认下载记录、旧→新排序、limit 生效且报出总缺口）；核账「只报告」不下载、「真补」按批下载并复位 `audit_running`；点「开始监控」会触发核账、关掉开关不触发。

`verify_ui.py` 检查界面结构（不截图）：设置菜单必须是 **6 个二级分类、没有第三层子菜单**（`账号与登录 / 监控与通知 / 下载 / 直播 / 界面与窗口 / 数据与维护`），每类关键项都在、「关闭窗口」两个 radiobutton 的值是 `exit` / `minimize`，新加的「桌面通知」「下载视频/图集」开关能写进配置；「本次下载」卡片两个按钮各 91px 可见、表格 `<Button-1>`/`<Key-Return>` 绑定、`cursor=hand2`。它还会把完整菜单树写进 `_menu_tree.txt`，改菜单后直接拿去更新 README。

## 注意

- 脚本里的 `HERE` 就是沙箱目录，所以**必须**在沙箱里跑，别在 `源代码\` 里直接跑；
- 需要带 tkinter 的 Python（系统 python 3.12 即可）；`verify_ui.py` 会短暂开一个 Tk 窗口；
- 跑完删掉沙箱目录即可（`rmdir /s /q ..\_t_sandbox`）。