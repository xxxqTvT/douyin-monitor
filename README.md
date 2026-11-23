# 抖音 / TikTok 账号监控

一个 Windows 桌面工具：挂机盯着多个抖音 / TikTok 账号，有新作品自动下载、开播自动录像，并做历史作品核账（补齐漏下的作品）。

原生 Tkinter 界面，无浏览器依赖；打包为单个 EXE（约 9 MB）+ `_internal` 目录。

## 功能

- **多账号监控**：按间隔轮询作品列表，发现新作品自动下载（视频 / 图集 / 实况）
- **历史核账**：按账号核对公开作品与本地文件，缺什么补什么；增量核账 + 定期全量
- **直播录制**：检测开播自动录制原画 FLV，结束后纯 Python 重封装为 MP4（不转码）
- **单独下载**：粘贴分享链接即可下载单条作品，支持短链解析
- **分账号目录**：历史作品按 `UID<抖音号>_<昵称>_发布作品` 归档
- **状态提示**：账号列表按状态着色（监控中 / 直播中 / 刷新中 / 出错 / 未启用）
- **深浅色主题**、托盘常驻、开机自启、剪贴板监听

## 运行环境

- Windows 10 / 11
- Python 3.12（仅源码运行需要；EXE 版不需要装 Python）
- 无第三方运行时依赖（标准库 + `tkinter` + 内置 `signlib`）

## 从源码运行

```bat
python douyin_client.py
```

首次启动后在「设置 → 账号与登录」里扫码登录抖音，再添加要监控的账号。

## 打包 EXE

```bat
pip install pyinstaller
python build_app.py
```

产物在 `dist\抖音监控\`：`抖音监控.exe` + `_internal\`。

## 目录结构

```
douyin_client.py     界面与调度（Tkinter）
douyin_monitor.py    抖音/TikTok 接口、下载、核账、直播检测
douk_dl.py           内置下载器（选最高画质、去重、断点跳过）
douk_rt.py           数据目录解析与写入探测
signlib/             请求签名（预编译 .pyc + 少量源码）
live_record.py       直播录制（FLV 直录）
live_remux.py        FLV → MP4 重封装
link_resolver.py     分享短链解析
browser_login.py     扫码登录
hi_dpi.py            DPI 缩放
tray.py              托盘图标
single_instance.py   单实例
build_app.py         PyInstaller 打包
tests/               自测脚本（26 个用例集 + 界面自检）
```

## 自测

```bat
python tests\make_sandbox.py
cd _t_sandbox
python test_features.py
python verify_ui.py
```

每个测试独立建沙箱，不会动你的真实账号数据。

## 说明

个人自用工具，非官方客户端。抖音接口与签名会随版本变化，登录态过期需重新扫码。

请勿把 `client_config.json`、`client_state.json`、`data\` 提交到仓库——里面有账号、Cookie 和下载记录（已在 `.gitignore` 中排除）。