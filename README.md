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

## 第三方组件与署名

本项目**包含第三方 GPL-3.0 代码**，逐项来源见 [NOTICE.md](NOTICE.md)。

| 组件 | 来源 | 许可 | 用途 |
|---|---|---|---|
| `signlib/enc/*.pyc`（8 个） | [TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader) by JoeanAmier | **GPL-3.0** | 抖音 a_bogus / X-Bogus / TikTok 请求签名，原样搬运未修改 |

- `douk_dl.py`、`douk_rt.py`、`douk_min/` 是**本项目自行实现**，只沿用了老版本 DouK 的文件命名规则与配置结构做兼容，不含其代码。
- 因为分发了 GPL-3.0 的签名模块，**本项目整体按 GPL-3.0 发布**，全文见 [LICENSE](LICENSE)。

如果你希望本项目改用宽松许可证（MIT / Apache-2.0），需要先移除 `signlib/enc/` 那 8 个字节码文件，改成让使用者自备签名模块——那样程序需要额外准备步骤才能跑。

## AI 辅助开发声明

本项目在 **AI 编程助手辅助**下开发：使用 DeepSeek 系列模型完成代码生成、重构、缺陷定位与文档撰写；需求、验收、真机测试与发布决策由作者负责。

所有 AI 产出的代码都经过：静态编译检查 → 26 组自测用例（`tests/`）→ 界面自检 → 打包后 `--selftest` 真机验证。

## 说明

个人自用工具，非官方客户端。抖音接口与签名会随版本变化，登录态过期需重新扫码。

「抖音」「TikTok」为各自权利人的商标，本项目与其无关联。

请勿把 `client_config.json`、`client_state.json`、`data\` 提交到仓库——里面有账号、Cookie 和下载记录（已在 `.gitignore` 中排除）。

## 许可证

GNU General Public License v3.0，见 [LICENSE](LICENSE)。