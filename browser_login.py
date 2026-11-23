# -*- coding: utf-8 -*-
"""扫码登录：自动打开浏览器 → 用户在抖音网页扫码/登录 → 读回 Cookie → 自动关掉浏览器

只用标准库实现（本机没有 websockets 包），所以内置了一个够用的 WebSocket 客户端，
用来跟浏览器的 DevTools 协议（CDP）通信。流程：

1. 找一个可用的浏览器（Chrome / Edge），用**独立的临时用户目录**启动，避免动用户的
   正常浏览器配置，也避免关掉浏览器时影响用户正在看的网页；
2. 打开 https://www.douyin.com/ ，并尽量自动点开「登录」面板，让二维码直接显示出来；
3. 每 1.5 秒通过 CDP 读一次 Cookie，出现 sessionid 就认为登录成功；
4. 读出全部 douyin.com 的 Cookie，通过 CDP 关掉这个浏览器，删除临时用户目录。

入口：login_via_browser(on_status=..., should_stop=...)
"""

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

LOGIN_URL = "https://www.douyin.com/"
LOGIN_URL_TIKTOK = "https://www.tiktok.com/"
SESSION_COOKIE = "sessionid"          # 登录成功后抖音 / TikTok 都会下发这个
POLL_SECONDS = 1.5

# 平台 → (Cookie 域名关键字, 登录页, 显示名)
PLATFORMS = {
    "douyin": ("douyin", LOGIN_URL, "抖音"),
    "tiktok": ("tiktok.com", LOGIN_URL_TIKTOK, "TikTok"),
}

CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)
EDGE_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
)

# 登录按钮的几种可能写法（抖音改版比较勤，多试几个；都点不到就让用户自己点）
LOGIN_CLICK_JS = r"""
(function () {
  var sels = ['[data-e2e="login-button"]', 'div[data-e2e="login-button"]',
              '#douyin-header-menu-login', '[data-e2e="login"]',
              'button[data-e2e="login-button"]'];
  for (var i = 0; i < sels.length; i++) {
    var el = document.querySelector(sels[i]);
    if (el) { el.click(); return 'sel:' + sels[i]; }
  }
  var all = document.querySelectorAll('div,span,button,a');
  for (var j = 0; j < all.length; j++) {
    var t = (all[j].textContent || '').trim();
    if (t === '登录' && all[j].offsetParent !== null) {
      all[j].click(); return 'text:登录';
    }
  }
  return 'none';
})()
"""


def find_browser():
    """返回可用的浏览器路径，优先 Chrome（无扩展干扰），其次 Edge"""
    for path in CHROME_PATHS + EDGE_PATHS:
        if path and os.path.isfile(path):
            return path
    return ""


def _free_port():
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


class WebSocketError(RuntimeError):
    pass


class WebSocket:
    """最小可用的 WebSocket 客户端：文本帧 + ping/pong，不做压缩"""

    def __init__(self, url, timeout=15):
        if not url.startswith("ws://"):
            raise WebSocketError("不支持的地址：%s" % url)
        host_port, _, path = url[5:].partition("/")
        host, _, port = host_port.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = ("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
                   "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                   "Sec-WebSocket-Version: 13\r\n\r\n") % (path, host_port, key)
        self.sock.sendall(request.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WebSocketError("握手时连接被关闭")
            buf += chunk
        head, self._buf = buf.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n")[0]
        if b" 101 " not in status:
            raise WebSocketError("握手失败：%s" % status.decode("latin-1"))

    # ---------------------------------------------------------------- 写
    def send(self, text):
        data = text.encode()
        header = bytearray([0x81])
        size = len(data)
        if size < 126:
            header.append(0x80 | size)
        elif size < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", size)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", size)
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    # ---------------------------------------------------------------- 读
    def _read(self, count):
        while len(self._buf) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("连接已关闭")
            self._buf += chunk
        out, self._buf = self._buf[:count], self._buf[count:]
        return out

    def recv(self):
        while True:
            first, second = self._read(2)
            opcode, size = first & 0x0F, second & 0x7F
            if size == 126:
                size = struct.unpack(">H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(size)
            if opcode == 0x1:                 # 文本
                return payload.decode("utf-8", "replace")
            if opcode == 0x8:                 # 关闭
                raise ConnectionError("浏览器关闭了调试连接")
            if opcode == 0x9:                 # ping → pong
                self.sock.sendall(b"\x8a\x80" + os.urandom(4))
            # pong / 二进制帧直接跳过

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Cdp:
    """在一条 WebSocket 上按 id 收发 CDP 指令"""

    def __init__(self, url, timeout=15):
        self.ws = WebSocket(url, timeout=timeout)
        self._id = 0

    def call(self, method, params=None, timeout=20):
        self._id += 1
        msg_id = self._id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            message = json.loads(self.ws.recv())
            if message.get("id") != msg_id:
                continue                      # 事件或其它响应，跳过
            if "error" in message:
                raise RuntimeError("%s 失败：%s" % (method, message["error"].get("message")))
            return message.get("result") or {}
        raise TimeoutError("%s 超时" % method)

    def close(self):
        self.ws.close()


def _wait_targets(base_url, proc, limit=40.0):
    """等浏览器把调试端口起来，返回页面目标列表"""
    end = time.time() + limit
    last_error = None
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError("浏览器启动后立刻退出了（返回码 %s）" % proc.returncode)
        try:
            with urllib.request.urlopen(base_url + "/json/list", timeout=2) as resp:
                targets = json.load(resp)
            if targets:
                return targets
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = exc
        time.sleep(0.4)
    raise RuntimeError("等不到浏览器调试端口：%s" % last_error)


def _pick_page(targets, host="douyin.com"):
    pages = [t for t in targets
             if t.get("type") == "page" and not (t.get("url") or "").startswith("chrome-extension")]
    for page in pages:
        if host in (page.get("url") or ""):
            return page
    return pages[0] if pages else None


def _close_browser(base_url, proc, wait=4.0):
    """优先走 CDP 的 Browser.close，不行再按进程树强杀"""
    try:
        with urllib.request.urlopen(base_url + "/json/version", timeout=3) as resp:
            info = json.load(resp)
        browser_ws = info.get("webSocketDebuggerUrl")
        if browser_ws:
            cdp = Cdp(browser_ws, timeout=5)
            try:
                cdp.call("Browser.close", timeout=5)
            finally:
                cdp.close()
    except Exception:
        pass
    end = time.time() + wait
    while time.time() < end:
        if proc.poll() is not None:
            return True
        time.sleep(0.2)
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    return False


def _cleanup(profile, tries=6):
    for _ in range(tries):
        if not os.path.isdir(profile):
            return
        shutil.rmtree(profile, ignore_errors=True)
        if not os.path.isdir(profile):
            return
        time.sleep(0.5)


def _cookie_dict(cookies, domain_key="douyin"):
    """只留目标站点的 Cookie（抖音 / TikTok），按 name=value 整理成字典"""
    now = time.time()
    out = {}
    for item in cookies:
        name = item.get("name") or ""
        if not name:
            continue
        domain = item.get("domain") or ""
        if domain_key not in domain:
            continue
        expires = item.get("expires")
        if isinstance(expires, (int, float)) and 0 < expires < now:
            continue
        out[name] = item.get("value") or ""
    return out


def login_via_browser(on_status=None, should_stop=None, timeout=300,
                      browser="", on_ready=None, platform="douyin"):
    """打开浏览器让用户扫码登录，成功后返回 Cookie。

    platform: "douyin" 打开 douyin.com，"tiktok" 打开 tiktok.com。
    返回 {"ok": bool, "cookie": dict, "message": str, "browser": str}
    on_status(text) 汇报进度；should_stop() 返回 True 时中止；
    on_ready(cdp) 是测试用的钩子（生产环境不传）。
    """
    say = on_status or (lambda _text: None)
    stop = should_stop or (lambda: False)
    platform = (platform or "douyin").lower()
    domain_key, login_url, label = PLATFORMS.get(platform, PLATFORMS["douyin"])
    result = {"ok": False, "cookie": {}, "message": "", "browser": "",
              "platform": platform}

    exe = browser or find_browser()
    result["browser"] = exe
    if not exe:
        result["message"] = "没有找到 Chrome 或 Edge 浏览器，请安装后再试"
        return result

    port = _free_port()
    profile = tempfile.mkdtemp(prefix="DouYinLogin_")
    args = [
        exe,
        "--remote-debugging-port=%d" % port,
        "--user-data-dir=%s" % profile,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-extensions",
        "--window-size=1180,800",
        "--new-window",
        login_url,
    ]
    say("正在打开浏览器…")
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        _cleanup(profile)
        result["message"] = "打开浏览器失败：%s" % exc
        return result

    base_url = "http://127.0.0.1:%d" % port
    cdp = None
    try:
        say("正在等浏览器启动…")
        targets = _wait_targets(base_url, proc)
        page = _pick_page(targets, domain_key)
        if not page:
            result["message"] = "浏览器没有打开可用的页面"
            return result
        cdp = Cdp(page["webSocketDebuggerUrl"], timeout=15)
        cdp.call("Network.enable", timeout=10)
        # 尽量把登录面板点出来，让二维码直接显示
        try:
            cdp.call("Runtime.evaluate",
                     {"expression": LOGIN_CLICK_JS, "returnByValue": True}, timeout=8)
        except Exception:
            pass
        if on_ready:
            on_ready(cdp)

        say("请在弹出的浏览器窗口里扫码登录%s…" % label)
        started = time.time()
        last_hint = 0.0
        while time.time() - started < timeout:
            if stop():
                result["message"] = "已取消登录"
                return result
            try:
                cookies = cdp.call("Network.getAllCookies", timeout=15)["cookies"]
            except (ConnectionError, TimeoutError, OSError):
                result["message"] = "浏览器窗口被关掉了，登录中止"
                return result
            jar = _cookie_dict(cookies, domain_key)
            if jar.get(SESSION_COOKIE):
                result["ok"] = True
                result["cookie"] = jar
                result["message"] = "登录成功，共取到 %d 项 Cookie" % len(jar)
                return result
            waited = time.time() - started
            if waited - last_hint >= 15:
                last_hint = waited
                say("请扫码登录…（已等待 %d 秒）" % int(waited))
            time.sleep(POLL_SECONDS)
        result["message"] = "等待登录超时（%d 秒），可以再试一次" % int(timeout)
        return result
    except Exception as exc:                     # 网络/CDP 各种异常兜底
        result["message"] = "登录失败：%s" % exc
        return result
    finally:
        if cdp is not None:
            cdp.close()
        _close_browser(base_url, proc)
        _cleanup(profile)


if __name__ == "__main__":                       # 手动试跑：只验证能不能连上浏览器
    def status(text):
        print("[status]", text, flush=True)

    out = login_via_browser(on_status=status, timeout=20)
    print(json.dumps({k: (v if k != "cookie" else len(v)) for k, v in out.items()},
                     ensure_ascii=False, indent=1))