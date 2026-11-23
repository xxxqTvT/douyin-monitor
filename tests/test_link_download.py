# -*- coding: utf-8 -*-
"""回归：单独下载链接（v1.5.14）

覆盖三处修复：
  ① 抖音分享短链（v.douyin.com / iesdouyin share 落地页）能挖出作品 ID；
  ② TikTok 不再塞进「内置接口」组（内置客户端没有 fetch_detail），改走解析器；
  ③ 失败链接会在返回文本里带上具体原因，不再只报一句「没有下载文件」。
全部离线：网络、下载、解析器都换成本地假实现。
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douyin_monitor as core  # noqa: E402

RESULT = {"checks": [], "ok": True}
CFG = os.path.join(HERE, "client_config.json")


def check(name, cond, extra=""):
    RESULT["checks"].append({"name": name, "ok": bool(cond), "extra": str(extra)[:300]})
    if not cond:
        RESULT["ok"] = False


def cfg_base():
    return {"data_dir": "", "save_dir": "", "douk_dir": "", "tiktok_proxy": "",
            "proxy": "", "request_timeout": 20, "douk_download_timeout": 900,
            "link_quality": "", "default_platform": "douyin"}


# ---------------- ① post_id_from：各种链接形态 ----------------
cases = [
    ("https://www.douyin.com/video/7554734706693393710", "7554734706693393710"),
    ("https://www.douyin.com/note/7554734706693393710", "7554734706693393710"),
    ("https://www.iesdouyin.com/share/video/7554734706693393710/?region=CN", "7554734706693393710"),
    ("https://www.iesdouyin.com/share/slides/7554734706693393710/", "7554734706693393710"),
    ("https://www.douyin.com/user/xxx?modal_id=7554734706693393710", "7554734706693393710"),
    ("https://www.tiktok.com/@user/video/7554734706693393710", "7554734706693393710"),
]
for url, want in cases:
    check("post_id 认得 %s" % url.split("/")[2],
          core.post_id_from(url) == want, core.post_id_from(url))

html = '<script>window.__ROUTER_DATA__={"aweme_id":"7554734706693393710"}</script>'
check("post_id 从落地页 HTML 挖 ID",
      core.post_id_from("https://www.douyin.com/", html) == "7554734706693393710",
      core.post_id_from("https://www.douyin.com/", html))
check("post_id 对无效链接返回空", core.post_id_from("https://example.com/a") == "",
      core.post_id_from("https://example.com/a"))

# 非短链不触发网络请求
_final, _html = core.expand_post_url("https://www.douyin.com/video/7554734706693393710")
check("expand_post_url 对长链原样返回", _final.endswith("7554734706693393710") and _html == "",
      (_final, _html))


# ---------------- ② douk_download：短链展开后按 ID 取详情 ----------------
class FakeClient:
    platform = "douyin"

    def __init__(self):
        self.ids = []

    def fetch_detail(self, aid):
        self.ids.append(aid)
        return {"aweme_id": aid, "desc": "短链作品", "create_time": 1700000000,
                "video": {"play_addr": {"url_list": ["https://x/video.mp4"]}}}


fake_client = FakeClient()
orig_expand = core.expand_post_url
orig_client_for = core._client_for
orig_download_posts = core.download_posts
orig_aweme = core.douk_dl.aweme_id


def fake_expand(url, timeout=15):
    if "v.douyin.com" in url:
        return ("https://www.iesdouyin.com/share/video/7554734706693393710/?region=CN",
                '<script>{"aweme_id":"7554734706693393710"}</script>')
    return url, ""


def fake_download_posts(cfg, items, client, platform, timeout=None, dest="", alt_dirs=()):
    return {"下载视频作品": len(items)}, [], "下载视频作品 %d 个" % len(items)


core.expand_post_url = fake_expand
core._client_for = lambda cfg, platform, dy=None, timeout=None: fake_client
core.download_posts = fake_download_posts
try:
    short = "https://v.douyin.com/iRNBho6f/"
    stats, files, text = core.douk_download(cfg_base(), [short], platform="douyin")
    check("短链展开后能取到详情", fake_client.ids == ["7554734706693393710"], fake_client.ids)
    check("短链下载统计为 1 个视频", stats.get("下载视频作品") == 1, stats)

    # 一条坏链接：文本里要带「失败：链接｜原因」
    fake_client.ids = []
    stats2, files2, text2 = core.douk_download(cfg_base(), ["https://example.com/nonsense"],
                                        platform="douyin")
    check("坏链接返回失败明细", "失败：" in text2 and "没认出作品 ID" in text2, text2)
    check("坏链接没有文件", stats2.get("下载视频作品", 0) == 0, stats2)
finally:
    core.expand_post_url = orig_expand
    core._client_for = orig_client_for
    core.download_posts = orig_download_posts


# ---------------- ③ download_links 分组：TikTok 不再进内置组 ----------------
calls = {"native": [], "resolver": []}
orig_douk = core.douk_download
orig_resolver = core.link_resolver if hasattr(core, "link_resolver") else None
sys.modules.pop("link_resolver", None)
import link_resolver as LR  # noqa: E402


def fake_douk(cfg, urls, timeout=None, platform="douyin", dy=None, dest="", alt_dirs=()):
    calls["native"].append((platform, list(urls)))
    return {"下载视频作品": len(urls)}, [], "下载视频作品 %d 个" % len(urls)


def fake_many(urls, dest, proxy="", timeout=60, quality="", progress=None):
    calls["resolver"].append(list(urls))
    return {"files": [], "results": [{"url": u, "platform": "TikTok", "ok": True,
                                      "title": "t", "files": 0, "seconds": 0}
                                     for u in urls]}


core.douk_download = fake_douk
LR.download_many = fake_many
try:
    links = [
        "https://www.douyin.com/video/7554734706693393710",
        "https://www.tiktok.com/@user/video/7554734706693393710",
        "https://vm.tiktok.com/ZMhxxxx/",
        "https://www.bilibili.com/video/BV1GJ411x7h7",
    ]
    stats, files, text = core.download_links(cfg_base(), links, timeout=30)
    native_urls = [u for _p, us in calls["native"] for u in us]
    resolver_urls = [u for us in calls["resolver"] for u in us]
    check("抖音链接走内置组", native_urls == [links[0]], native_urls)
    check("TikTok 长链走解析器", links[1] in resolver_urls, resolver_urls)
    check("TikTok 短链走解析器", links[2] in resolver_urls, resolver_urls)
    check("B 站链接走解析器", links[3] in resolver_urls, resolver_urls)
    check("TikTok 没被塞进内置组", all("tiktok.com" not in u for u in native_urls), native_urls)
    check("统计含其它平台作品", stats.get("下载其它平台作品") == 3, stats)
finally:
    core.douk_download = orig_douk


# ---------------- ④ is_native_link：只有抖音算内置 ----------------
check("is_native 抖音=True", core.is_native_link("douyin") is True)
check("is_native TikTok=False", core.is_native_link("tiktok") is False,
      core.is_native_link("tiktok"))
check("is_native TikTok 短链=False",
      core.is_native_link("https://vm.tiktok.com/ZMhxxxx/") is False)
check("is_native B站=False", core.is_native_link("https://www.bilibili.com/video/BV1") is False)

# ---------------- ⑤ expand_post_url 跟随 302，短链整段闭环 ----------------
import urllib.request as _ureq


class _FakeResp:
    def __init__(self, final, body):
        self._final, self._body = final, body.encode("utf-8")

    def geturl(self):
        return self._final

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_orig_urlopen = _ureq.urlopen


def _fake_urlopen(req, timeout=None):
    return _FakeResp(
        "https://www.iesdouyin.com/share/video/7554734706693393710/?region=CN",
        '<html><script>{"aweme_id":"7554734706693393710"}</script></html>')


_ureq.urlopen = _fake_urlopen
_client2 = FakeClient()
core._client_for = lambda cfg, platform, dy=None, timeout=None: _client2
core.download_posts = fake_download_posts
try:
    final, html = core.expand_post_url("https://v.douyin.com/AbCdEf1/", timeout=5)
    check("302 短链跟到落地页", "share/video/7554734706693393710" in final, final)
    check("302 落地页挖出作品 ID",
          core.post_id_from(final, html) == "7554734706693393710",
          core.post_id_from(final, html))
    # 完整闭环：短链 -> 展开 -> 取详情 -> 下载统计
    _client2.ids = []
    stats3, files3, text3 = core.douk_download(cfg_base(),
                                        ["https://v.douyin.com/AbCdEf1/"],
                                        platform="douyin", timeout=5)
    check("短链整段闭环取到详情", _client2.ids == ["7554734706693393710"], _client2.ids)
    check("短链整段闭环下载 1 个", stats3.get("下载视频作品") == 1, stats3)
finally:
    _ureq.urlopen = _orig_urlopen
    core._client_for = orig_client_for
    core.download_posts = orig_download_posts


print("checks=%d failed=%d" % (len(RESULT["checks"]),
                               sum(1 for c in RESULT["checks"] if not c["ok"])))
for c in RESULT["checks"]:
    if not c["ok"]:
        print("  FAIL: %s  %s" % (c["name"], c["extra"]))
print("RESULT_OK" if RESULT["ok"] else "RESULT_FAIL")
