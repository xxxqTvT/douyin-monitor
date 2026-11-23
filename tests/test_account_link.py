# -*- coding: utf-8 -*-
"""添加账号：主页链接 / 分享文案 / 短链 的解析自检（不联网，跳转接口打桩）。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import douyin_monitor as core  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS", name)
    else:
        print("  FAIL", name, detail)
        FAILED.append(name)


def main():
    # 1) 从分享文案里抠链接
    share = "8.88 复制打开抖音，看看【某人的作品】 https://v.douyin.com/yK5CI1VnrxU/ 0@0.com :9pm"
    link = core.extract_url(share)
    check("从分享文案里抠出短链", link == "https://v.douyin.com/yK5CI1VnrxU/", link)
    check("普通文字没有链接", core.extract_url("92897238244") == "")
    check("链接后面的中文标点被去掉",
          core.extract_url("看这个 https://v.douyin.com/abc/，谢谢") == "https://v.douyin.com/abc/")

    tmp = tempfile.mkdtemp(prefix="account_link_")
    os.makedirs(os.path.join(tmp, "Volume"), exist_ok=True)
    with open(os.path.join(tmp, "Volume", "settings.json"), "w", encoding="utf-8") as fh:
        fh.write('{"cookie": {}}')
    dy = core.DouYin("", data_dir=tmp)

    # 2) 直接给 www.douyin.com/user/MS4w... 的链接
    direct = ("https://www.douyin.com/user/MS4wLjABAAAAtestSecUid123?from_tab_name=main")
    check("直链里的 sec_uid 直接取出来",
          dy.resolve_sec_uid(direct) == "MS4wLjABAAAAtestSecUid123")

    # 3) 短链：跟跳转后拿到主页链接
    dy.resolve_redirect = lambda url: (
        "https://www.douyin.com/user/MS4wLjABAAAAshortTarget99", "")
    got = dy.resolve_sec_uid(share)
    check("分享短链跟跳转后解析出 sec_uid", got == "MS4wLjABAAAAshortTarget99", got)

    # 4) 短链跳转后是 /user/<抖音号>，再按抖音号查
    dy.resolve_redirect = lambda url: ("https://www.douyin.com/user/wu1216601571", "")
    dy.user_info_by_unique_id = lambda uid: {"sec_uid": "MS4wLjABAAAAbyUid"} if uid == "wu1216601571" else {}
    check("短链跳到 /user/<抖音号> 也能解析",
          dy.resolve_sec_uid("https://v.douyin.com/xyz/") == "MS4wLjABAAAAbyUid")

    # 5) 短链失效：报错，不能悄悄拿搜索结果第一个账号顶替
    def boom(url):
        raise OSError("404")
    dy.resolve_redirect = boom
    dy.get_json = lambda *a, **k: {"user_list": [{"user_info": {"sec_uid": "MS4wLjABAAAAwrong",
                                                                "nickname": "别人"}}]}
    try:
        dy.resolve_sec_uid("https://v.douyin.com/deadlink/")
        check("失效短链应报错", False, "居然返回了账号")
    except RuntimeError as exc:
        check("失效短链报错（不再乱匹配）", "主页链接" in str(exc), str(exc))

    # 6) 普通抖音号：仍走搜索，允许近似匹配兜底
    dy2 = core.DouYin("", data_dir=tmp)
    dy2.user_info_by_unique_id = lambda uid: {}
    dy2.get_json = lambda *a, **k: {"user_list": [{"user_info": {"sec_uid": "MS4wLjABAAAAexact",
                                                                "unique_id": "dy2bhwi6k5vn",
                                                                "nickname": "某人"}}]}
    check("抖音号精确匹配", dy2.resolve_sec_uid("dy2bhwi6k5vn") == "MS4wLjABAAAAexact")

    if FAILED:
        print("RESULT_FAIL", len(FAILED), FAILED)
        sys.exit(1)
    print("RESULT_OK")


if __name__ == "__main__":
    main()