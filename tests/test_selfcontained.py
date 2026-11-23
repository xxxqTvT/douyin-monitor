"""剥离外部 DouK / ffmpeg 后的自包含回归（2026-11-12，v1.6.0）。

跑在 tests\\make_sandbox.py 建的沙箱里。覆盖：
- signlib 能导入、能生成 a_bogus / TikTok 签名；
- DouYin 只用内置签名模块，不再把 src / curl_cffi 拉进进程；
- 批量下载账号作品走内置翻页 + 内置下载器，不再有外部 exe / subprocess；
- douk_rt.ensure 返回 signlib 目录（不再找 douk_min 缓存）；
- 内置下载器跨盘保存不再因 relpath 报错（回归）；
- TikTok 作品字段归一化；
- ffmpeg 相关接口保持空实现（直播录制是纯 Python FLV 直录）。
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douyin_client as client  # noqa: E402
import douyin_monitor as core  # noqa: E402
import douk_dl  # noqa: E402
import douk_rt  # noqa: E402
import live_record  # noqa: E402
import signlib  # noqa: E402

RESULT = {"checks": [], "ok": True}


def check(name, cond, extra=""):
    RESULT["checks"].append({"name": name, "ok": bool(cond), "extra": str(extra)[:300]})
    if not cond:
        RESULT["ok"] = False


def make_settings(tmp, cookie="sessionid=test"):
    vol = os.path.join(tmp, "Volume")
    os.makedirs(vol, exist_ok=True)
    path = os.path.join(vol, "settings.json")
    io.open(path, "w", encoding="utf-8").write(json.dumps({
        "cookie": {"sessionid": cookie, "UIFID": "test-uifid", "ttwid": "t"},
        "cookie_tiktok": {"sessionid": "tk"},
        "proxy_tiktok": "",
    }, ensure_ascii=False))
    return path


class FakeClient:
    """假客户端：翻两页作品，验证 douk_download_accounts 的翻页与去重。"""

    def __init__(self):
        self.pages = {
            "0": (["101", "102"], True, "20"),
            "20": (["103"], False, ""),
        }
        self.calls = []

    def resolve_sec_uid(self, spec):
        return "SEC_FAKE"

    def fetch_profile(self, spec):
        return {"sec_uid": "SEC_FAKE", "nickname": "假账号"}

    def fetch_posts_page(self, sec, count=18, cursor="0"):
        self.calls.append((sec, count, cursor))
        ids, more, nxt = self.pages[cursor]
        items = [{"aweme_id": i, "desc": "作品" + i, "create_time": 1,
                  "images": [], "share_url": ""} for i in ids]
        return items, more, nxt


def main():
    tmp = tempfile.mkdtemp(prefix="selfcontained_")
    cfg = {"data_dir": tmp, "request_timeout": 20}
    make_settings(tmp)

    # ---- 1) signlib ----
    check("signlib 有 API_PARAMS", signlib.API_PARAMS.get("aid") == "6383",
          signlib.API_PARAMS.get("aid"))
    qs = signlib.DouYinParams().sign_url(
        "https://www.douyin.com/aweme/v1/web/aweme/post/",
        {"sec_user_id": "SEC", "count": "18", "device_platform": "webapp"},
        None, "GET", signlib.UA, "")
    check("a_bogus 出现在签名串里", "a_bogus=" in qs, qs[-40:])
    sqs = signlib.TikTokParams().sign_url(
        "https://www.tiktok.com/api/post/item_list/",
        {"secUid": "SEC", "count": "10"}, None, "GET", signlib.UA, "")
    check("TikTok 签名串带 X-Bogus", "X-Bogus=" in sqs, sqs[-40:])
    check("TikTok 签名串带 X-Gnarly", "X-Gnarly=" in sqs, sqs[-40:])

    # ---- 2) DouYin 不再依赖 src / curl_cffi ----
    dy = core.DouYin("", timeout=10, data_dir=tmp)
    check("DouYin 用内置签名器", getattr(dy.signer, "__class__").__module__.startswith("signlib"),
          getattr(dy.signer, "__class__").__module__)
    check("基础参数来自 signlib", dy._base_params().get("device_platform") == "webapp")
    check("进程里没有 src 包", "src" not in sys.modules, [m for m in sys.modules if m == "src"][:3])
    check("进程里没有 curl_cffi", "curl_cffi" not in sys.modules,
          [m for m in sys.modules if m.startswith("curl_cffi")][:3])
    check("signed_query 带 a_bogus", "a_bogus=" in dy.signed_query(
        "/aweme/v1/web/aweme/post/", {"sec_user_id": "SEC"}))
    check("resolve_lib_dir 指向 signlib/enc",
          core.resolve_lib_dir("").endswith(os.path.join("signlib", "enc")),
          core.resolve_lib_dir(""))

    # ---- 3) 批量下载账号作品：内置翻页 + 内置下载，无 subprocess ----
    check("没有 _run_douk 了", not hasattr(core, "_run_douk"))
    fake = FakeClient()
    calls = {}

    def fake_download_posts(cfg2, items, dy2=None, platform="douyin", dest="", timeout=None,
                            progress=None):
        calls["items"] = [i["aweme_id"] for i in items]
        calls["platform"] = platform
        return ({"下载视频作品": len(items)}, ["x"], "下载视频作品 %d 个" % len(items))

    orig_client, orig_download = core.make_client, core.download_posts
    core.make_client = lambda cfg2, platform="douyin", timeout=20: fake
    core.download_posts = fake_download_posts
    try:
        stats, files, text = core.douk_download_accounts(
            cfg, ["https://www.douyin.com/user/SEC_FAKE"], timeout=30)
    finally:
        core.make_client, core.download_posts = orig_client, orig_download
    check("批量下载翻了两页", len(fake.calls) == 2, fake.calls)
    check("批量下载按 ID 去重", calls.get("items") == ["101", "102", "103"], calls.get("items"))
    check("批量下载走内置下载器", stats.get("下载视频作品") == 3, stats)
    check("批量下载返回文字", "下载视频作品" in (text or ""), text)

    # ---- 4) douk_rt 不再找 douk_min 缓存 ----
    lib, data = douk_rt.ensure(custom=tmp)
    check("ensure 返回 signlib 目录", os.path.isdir(os.path.join(lib, "enc")), lib)
    check("ensure 数据目录正确", os.path.abspath(data) == os.path.abspath(tmp), data)

    # ---- 5) 跨盘保存 relpath 回归 ----
    recs = {}
    got_dir = tempfile.mkdtemp(prefix="dl_src_")
    f1 = os.path.join(got_dir, "a.mp4")
    io.open(f1, "wb").write(b"x" * 10)
    orig_pick, orig_item = douk_dl.pick_media, douk_dl.download_item
    douk_dl.pick_media = lambda item: ("video", "http://example/a.mp4")
    douk_dl.download_item = lambda item, kind, dest, headers, timeout=60, save_live=True: ([f1], "")
    try:
        douk_dl.download_items({"disk_free_min_mb": 0}, [{"aweme_id": "77", "desc": "t"}],
                               "douyin", got_dir, records=recs,
                               data_root=os.path.join(tmp, "data"))
    finally:
        douk_dl.pick_media, douk_dl.download_item = orig_pick, orig_item
    check("跨盘下载不炸且写入记录", bool(recs.get("douyin:77", {}).get("file")), recs.get("douyin:77"))

    # ---- 6) TikTok 字段归一化 ----
    tk = core.TikTok("", timeout=5, data_dir=tmp)
    item = tk._normalize_item({"id": "9", "desc": "d", "createTime": 3,
                               "author": {"uniqueId": "u"},
                               "imagePost": {"images": [{"imageURL": {"urlList": ["http://i"]}}]}})
    check("TikTok 归一化 aweme_id", item.get("aweme_id") == "9", item)
    check("TikTok 归一化图片", item.get("images") == ["http://i"], item.get("images"))
    check("TikTok 归一化分享链接", "tiktok.com/@u/photo/9" in item.get("share_url", ""),
          item.get("share_url"))

    # ---- 7) ffmpeg 仍是空实现 ----
    check("find_ffmpeg 返回空", not live_record.find_ffmpeg())
    check("list_ffmpeg 返回空", live_record.list_ffmpeg() in ([], (), None, ""))

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(got_dir, ignore_errors=True)

    io.open(os.path.join(HERE, "_selfcontained_res.json"), "w", encoding="utf-8").write(
        json.dumps(RESULT, ensure_ascii=False, indent=2))
    print("RESULT_OK" if RESULT["ok"] else "RESULT_FAIL")
    print("checks=%d failed=%d" % (len(RESULT["checks"]),
                                   sum(1 for c in RESULT["checks"] if not c["ok"])))


if __name__ == "__main__":
    main()