# 第三方组件与来源声明

本项目包含第三方开源代码与兼容实现。以下逐项说明来源、许可证与改动。
分发本程序（源码或 EXE）时必须保留本文件。

---

## 1. TikTokDownloader（GPL-3.0）— 请求签名模块

`signlib/enc/` 下的预编译字节码来自 **TikTokDownloader**，原样搬运，未修改：

| 文件 | 原始路径 | 作用 |
|---|---|---|
| `signlib/enc/aBogus.pyc` | `src/encrypt/aBogus.py` | 抖音 a_bogus 签名 |
| `signlib/enc/xBogus.pyc` | `src/encrypt/xBogus.py` | 抖音 X-Bogus 签名 |
| `signlib/enc/websign.pyc` | `src/encrypt/websign.py` | 网页端签名 | 
| `signlib/enc/params.pyc` | `src/encrypt/params.py` | 签名公共参数 |
| `signlib/enc/douyin_params.pyc` | `src/encrypt/douyin_params.py` | 抖音参数组装 |
| `signlib/enc/tiktok_params.pyc` | `src/encrypt/tiktok_params.py` | TikTok 参数组装 |
| `signlib/enc/tiktok_sign.pyc` | `src/encrypt/tiktok_sign.py` | TikTok 签名 |
| `signlib/enc/sm3.pyc` | `src/encrypt/sm3.py` | SM3 哈希 |

- 上游项目：https://github.com/JoeanAmier/TikTokDownloader
- 作者：JoeanAmier
- 许可证：**GNU General Public License v3.0**（全文见 `LICENSE`）
- 改动：无。仅重命名为驼峰式文件名（`aBogus.pyc` 等），字节码内容未修改。
- 取证：这些 `.pyc` 内嵌的 `co_filename` 仍为 `D:\a\TikTokDownloader\TikTokDownloader\src\encrypt\*.py`，可自行核对。

> 因为分发包含 GPL-3.0 代码，本项目整体按 **GPL-3.0** 发布。若你希望改用其他许可证，
> 需要先移除 `signlib/enc/` 下的这 8 个字节码文件，改为让用户自备签名模块。

### 与之相关的本项目代码（自行编写，非拷贝）

- `signlib/__init__.py`、`signlib/custom.py`：只做导入封装与 UA 常量
- `douk_min/emoji/`、`douk_min/rich/`：为满足上游模块的 `import` 而写的最小替身
  （`replace_emoji` 正则实现、空渲染桩），不包含上游实现

---

## 2. DouK-Downloader（仅接口兼容，不含其代码）

早期版本通过外部调用 DouK-Downloader 完成下载与登录，现已全部内置。
当前代码中出现的 `douk_*` 名称只是**历史命名与数据格式兼容**，不包含 DouK 的实现代码：

| 位置 | 关系 |
|---|---|
| `douk_dl.py` | 本项目自行实现的下载器；沿用 DouK 的**文件命名规则**（`时间-类型-昵称-标题`），便于老用户继续使用已有文件 |
| `douk_rt.py` | 本项目的数据目录解析；沿用 DouK 的 `settings.json` **结构**，用于迁移旧 Cookie 与下载记录 |
| `douk_min/` | 见上，为本项目编写的依赖替身 |
| 配置项 `douk_dir` / `douk_download_timeout` | 历史字段，保留以兼容旧配置文件 |

未从 DouK-Downloader 复制任何源代码或二进制。其发行包自带的
`frozen_application_license.txt` 属于 cx_Freeze 的 MIT 许可，与本项目无关。

---

## 3. Python 标准库与运行时

- Python 3.12（PSF License）
- Tkinter / ttk（Tcl/Tk License）
- PyInstaller（打包用，GPL-2.0 with exception；生成物不受其约束）

---

## 4. AI 辅助开发声明

本项目在 **AI 编程助手辅助下开发**：

- 使用 DeepSeek 系列模型（DeepSeek Harness / DeepSeek-V4 等）进行代码生成、重构、缺陷定位与文档撰写
- 人（项目作者）负责需求、验收、真机测试与发布决策
- 所有 AI 产出的代码均经过：静态编译检查、26 组自测用例、界面自检、打包后 `--selftest` 真机验证

代码中的注释、提交信息与文档可能同时包含人工与 AI 撰写的内容。

---

## 5. 商标与免责

- 「抖音」「TikTok」为其各自权利人的商标，本项目与字节跳动 / 抖音 / TikTok 无关联
- 本项目为非官方第三方工具，仅用于个人备份自己有权访问的内容
- 使用本工具产生的任何后果由使用者自行承担；请遵守平台服务条款与当地法律