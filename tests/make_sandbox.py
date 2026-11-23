"""给回归脚本准备一个隔离沙箱：复制源码 + douk_min + 假配置，绝不碰真实目录。

用法（在 源代码 目录下）：
    python tests/make_sandbox.py [沙箱目录，默认 ..\\_t_sandbox]
然后：
    cd <沙箱目录>
    python test_features.py      # 27 项断言 → RESULT_OK
    python verify_ui.py          # 界面结构 → _ui.txt
"""
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 源代码
DEFAULT = os.path.abspath(os.path.join(HERE, "..", "_t_sandbox"))

FAKE_CONFIG = {
    "poll_interval": 300,
    "platform_chosen": True,
    "login_skipped": True,
    "theme": "light",
    "tray_enabled": False,
    "notify_enabled": False,
    "silent_mode": False,
    "clipboard_watch": False,
    "window_geometry": "1180x720+120+80",
    "accounts": [{
        "iid": "row1", "target": "测试A", "nickname": "测试A", "platform": "douyin",
        "enabled": True, "url": "https://www.douyin.com/user/MS4wLjABAAAA_test_a",
    }],
}


def main():
    dest = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    for name in os.listdir(HERE):
        if name.endswith(".py") and os.path.isfile(os.path.join(HERE, name)):
            shutil.copy2(os.path.join(HERE, name), dest)
    for name in ("test_features.py", "test_auto_start.py", "test_audit.py",
             "test_log_menu.py", "test_state_save.py", "test_deep_audit.py", "test_link_download.py", "test_ratelimit.py",
             "test_live_interval.py", "test_platform_fixes.py", "test_selfcontained.py", "test_live_record_local.py", "test_sign_diff.py", "test_live_remux.py", "test_account_link.py", "test_history_dir.py", "test_download_progress.py", "test_refresh_ask.py", "test_clear_records.py", "test_link_panel.py", "test_monitor_watchdog.py", "test_downloader_files.py", "test_data_dir.py", "test_scope.py", "test_hi_dpi.py", "test_live_photo.py", "verify_ui.py"):
        shutil.copy2(os.path.join(HERE, "tests", name), dest)
    signlib = os.path.join(HERE, "signlib")
    if os.path.isdir(signlib):
        shutil.copytree(signlib, os.path.join(dest, "signlib"),
                        ignore=shutil.ignore_patterns("__pycache__"))
    io.open(os.path.join(dest, "client_config.json"), "w", encoding="utf-8").write(
        json.dumps(FAKE_CONFIG, ensure_ascii=False, indent=2))
    io.open(os.path.join(dest, "client_state.json"), "w", encoding="utf-8").write(
        '{"accounts": {}}')
    print("沙箱已就绪：%s" % dest)
    print("下一步：cd \"%s\" && python test_features.py" % dest)


if __name__ == "__main__":
    main()
