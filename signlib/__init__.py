"""内置签名模块（纯 Python，随程序分发）。

来源与许可（重要）：
    signlib/enc/ 下的 8 个 .pyc 来自 TikTokDownloader
    https://github.com/JoeanAmier/TikTokDownloader
    作者 JoeanAmier，许可证 GPL-3.0，原样搬运未修改。
    本项目因此整体按 GPL-3.0 发布，详见 NOTICE.md 与 LICENSE。

本文件、custom.py 为导入封装与 UA 常量，属本项目自行编写。

原 docstring（保留作说明）：
    以前程序需要用户另外装一个 DouK-Downloader，再借用它 lib 目录里的
    a_bogus / X-Bogus / TikTok 签名模块；现在这些模块直接放在 signlib/enc，
    打包时一起带上：不需要安装 DouK，也不依赖 curl_cffi 和它的 DLL。
    对外只暴露三样东西：DouYinParams / TikTokParams / API_PARAMS。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from .enc.douyin_params import DouYinParams          # noqa: E402
from .enc.tiktok_params import TikTokParams          # noqa: E402
from .custom import USERAGENT                        # noqa: E402

UA = USERAGENT

# 抖音网页接口的固定查询参数（原来从 DouK 的 src.interface.template.API.params 取）
API_PARAMS = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "update_version_code": "170400",
    "pc_client_type": "1",
    "pc_libra_divert": "Mac",
    "support_h265": "1",
    "support_dash": "1",
    "version_code": "290100",
    "version_name": "29.1.0",
    "cookie_enabled": "true",
    "screen_width": "1536",
    "screen_height": "864",
    "browser_language": "zh-CN",
    "browser_platform": "MacIntel",
    "browser_name": "Chrome",
    "browser_version": "146.0.0.0",
    "browser_online": "true",
    "engine_name": "Blink",
    "engine_version": "146.0.0.0",
    "os_name": "Mac OS",
    "os_version": "10.15.7",
    "cpu_core_num": "16",
    "device_memory": "8",
    "platform": "PC",
    "downlink": "10",
    "effective_type": "4g",
    "round_trip_time": "200",
    "uifid": "",
    "msToken": "",
}

__all__ = ["DouYinParams", "TikTokParams", "API_PARAMS", "UA", "USERAGENT"]