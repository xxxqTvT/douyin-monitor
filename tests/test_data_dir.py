"""回归：数据目录必须稳定，不能因并发探测失败回退到 %%LOCALAPPDATA%%。

之前 _writable() 用固定文件名 .write_test，监控线程/核账线程同时探测时互相删文件，
导致 app_dir\data 被判不可写 → 换到 LOCALAPPDATA\DouYinMonitor\data，
于是「下载记录读成 0 条、核账以为全缺、Cookie 读不到」。
"""
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douk_rt  # noqa: E402

RESULT = {"checks": [], "ok": True}


def check(name, cond, extra=""):
    RESULT["checks"].append({"name": name, "ok": bool(cond), "extra": str(extra)[:200]})
    if not cond:
        RESULT["ok"] = False


def main():
    first = douk_rt.data_dir("")
    check("数据目录在程序目录下（源码运行）", first.startswith(HERE), first)
    check("没有落到 LOCALAPPDATA", "DouYinMonitor" not in first or first.startswith(HERE), first)

    seen = []
    lock = threading.Lock()

    def worker():
        for _ in range(20):
            p = douk_rt.data_dir("")
            with lock:
                seen.append(p)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("并发调用结果始终一致", len(set(seen)) == 1, set(seen))
    check("并发调用都等于首次结果", all(p == first for p in seen), set(seen))

    # 目录缓存也覆盖自定义目录
    custom = os.path.join(HERE, "_dirdata_custom")
    a = douk_rt.data_dir(custom)
    b = douk_rt.data_dir(custom)
    check("自定义目录也缓存稳定", a == b == os.path.abspath(custom), (a, b))
    check("自定义目录会被创建", os.path.isdir(custom), custom)

    bad = [c for c in RESULT["checks"] if not c["ok"]]
    print("RESULT_OK" if RESULT["ok"] else "RESULT_FAIL")
    for c in bad:
        print("  FAIL:", c["name"], c.get("extra"))
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
