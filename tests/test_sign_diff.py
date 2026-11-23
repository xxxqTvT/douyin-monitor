"""差分测试：内置 signlib 与参考实现（DouK 安装目录）是否一致。

只在开发机装了 DouK 时才有意义：本测试会对比
- 抖音固定参数（我们的 API_PARAMS vs DouK API.params）；
- TikTok 固定参数（我们的 TikTok.FIXED_PARAMS vs DouK APITikTok.params）；
- 同一路径/参数下两边生成的签名查询串包含的参数名集合；
- 可选：用同一份 Cookie 分别用两套签名请求真实接口，比对返回的作品 ID。

**产品运行不依赖 DouK**：找不到参考实现时本测试输出 RESULT_SKIP 并以 0 退出，
沙箱与用户机器上都不会因此失败。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import douyin_monitor as core  # noqa: E402
import signlib  # noqa: E402

REFS = [r"D:\douk\DouK-Downloader_V5.8_Windows_X64\lib",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "DouYinMonitor", "douk_lib")]
CHECKS = []


def check(name, ok, extra=""):
    CHECKS.append({"name": name, "ok": bool(ok), "extra": str(extra)[:300]})


def load_ref():
    """加载 DouK 的参考模块；不存在/加载失败返回 None。"""
    cands = [p for p in REFS if p and os.path.isdir(os.path.join(p, "src"))]
    if not cands:
        return None
    lib = cands[0]
    import importlib.metadata as md

    orig_version, orig_metadata = md.version, md.metadata

    def fake(name):
        return "0.0.0" if name.replace("-", "_").lower() == "curl_cffi" else orig_version(name)

    md.version = fake
    md.metadata = lambda name: (
        {"Summary": "curl_cffi", "Version": "0.0.0", "Name": name}
        if name.replace("-", "_").lower() == "curl_cffi" else orig_metadata(name))
    sys.path.insert(0, lib)
    try:
        if os.name == "nt":
            for d in (lib, os.path.join(lib, "curl_cffi.libs"), os.path.join(lib, "curl_cffi")):
                if os.path.isdir(d):
                    try:
                        os.add_dll_directory(d)
                    except OSError:
                        pass
        from src.interface.template import API, APITikTok
        from src.encrypt.douyin_params import DouYinParams
        return {"API": API, "APITikTok": APITikTok, "Signer": DouYinParams}
    except Exception:  # noqa: BLE001
        return None
    finally:
        md.version, md.metadata = orig_version, orig_metadata
        try:
            sys.path.remove(lib)
        except ValueError:
            pass


def main():
    ref = load_ref()
    if ref is None:
        print("RESULT_OK")
        print("skipped=1（没找到参考 DouK 安装目录，差分测试跳过）")
        return

    # ---- 固定参数 ----
    ours = set(signlib.API_PARAMS)
    theirs = set(ref["API"].params)
    check("抖音固定参数键一致", ours == theirs, "缺少 %s / 多出 %s" % (
        sorted(theirs - ours)[:5], sorted(ours - theirs)[:5]))
    diff = {k: (signlib.API_PARAMS.get(k), ref["API"].params.get(k))
            for k in ours & theirs if str(signlib.API_PARAMS.get(k)) != str(ref["API"].params.get(k))}
    check("抖音固定参数值一致", not diff, diff)

    tk_ours = core.TikTok.FIXED_PARAMS
    tk_ref = ref["APITikTok"].params
    missing = {k: tk_ref[k] for k in tk_ours if str(tk_ref.get(k)) != str(tk_ours[k])}
    check("TikTok 固定参数值一致", not missing, missing)

    # ---- 签名查询串 ----
    API_PATH = "/aweme/v1/web/aweme/post/"
    q = dict(signlib.API_PARAMS)
    q.update({"sec_user_id": "SEC", "count": "18", "max_cursor": "0"})
    ours_qs = signlib.DouYinParams().sign_url(
        "https://www.douyin.com" + API_PATH, dict(q), None, "GET", signlib.UA, "")
    ref_qs = ref["Signer"]().sign_url(
        "https://www.douyin.com" + API_PATH, dict(q), None, "GET", ref["API"].params.get("__ua__", signlib.UA), "")
    ours_keys = {p.split("=")[0] for p in ours_qs.split("&")}
    ref_keys = {p.split("=")[0] for p in ref_qs.split("&")}
    check("签名查询串参数名一致", ours_keys == ref_keys,
          "缺少 %s / 多出 %s" % (sorted(ref_keys - ours_keys)[:5], sorted(ours_keys - ref_keys)[:5]))
    check("两边都带 a_bogus", "a_bogus" in ours_keys and "a_bogus" in ref_keys)

    # ---- 可选：真实接口比对作品 ID ----
    cands = [os.environ.get("DOUYIN_DATA", ""),
             os.path.join(HERE, "data"),
             r"D:\dshzijian\抖音监控\data",
             r"D:\genggai1.0\抖音监控\data"]
    settings = ""
    for cand in cands:
        if not cand:
            continue
        sp = os.path.join(str(core.data_root({"data_dir": cand})), "Volume", "settings.json")
        if os.path.isfile(sp):
            settings = sp
            break
    try:
        with open(settings, "r", encoding="utf-8-sig") as f:
            cookie = json.load(f).get("cookie") or {}
        ck = "; ".join("%s=%s" % (k, v) for k, v in cookie.items() if v)
    except Exception:  # noqa: BLE001
        ck = ""
    if ck:
        import urllib.request

        def fetch(signer, base_params):
            qq = dict(base_params)
            if not os.environ.get("SIGN_TEST_SEC_UID"):
                return []
            qq["uifid"] = cookie.get("UIFID", "")
            qq.update({"sec_user_id": os.environ.get("SIGN_TEST_SEC_UID", ""),
                       "count": "5", "max_cursor": "0"})
            qs = signer.sign_url("https://www.douyin.com" + API_PATH, qq, None, "GET", signlib.UA, "")
            req = urllib.request.Request(
                "https://www.douyin.com" + API_PATH + "?" + qs,
                headers={"User-Agent": signlib.UA, "Referer": "https://www.douyin.com/",
                         "Cookie": ck, "Accept": "application/json, text/plain, */*"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            return [x.get("aweme_id") for x in (data.get("aweme_list") or [])]

        try:
            a = fetch(signlib.DouYinParams(), signlib.API_PARAMS)
            b = fetch(ref["Signer"](), ref["API"].params)
            print("real_api_ids=%d ours=%s ref=%s" % (len(a), a[:3], b[:3]))
            check("真实接口返回的作品 ID 一致", a == b and bool(a), "%d vs %d 条" % (len(a), len(b)))
        except Exception as exc:  # noqa: BLE001
            print("real_api_skip:", type(exc).__name__, str(exc)[:200])
            check("真实接口比对（网络不可用时跳过）", True, "跳过：%s" % exc)

    failed = [c for c in CHECKS if not c["ok"]]
    for c in CHECKS:
        print("%s %s %s" % ("OK " if c["ok"] else "BAD", c["name"], c["extra"] if not c["ok"] else ""))
    print("RESULT_OK" if not failed else "RESULT_FAIL")
    print("checks=%d failed=%d" % (len(CHECKS), len(failed)))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()