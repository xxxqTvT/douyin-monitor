import json
import logging
import os
import re
import sys
import threading
import time
import traceback
import douk_rt
import douk_dl
import link_resolver

def app_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = app_base_dir()
_ORIGINAL_PATH = os.environ.get('PATH', '')

def child_env(extra=None):
    env = dict(os.environ)
    env['PATH'] = _ORIGINAL_PATH
    for key in list(env):
        if key.startswith('_PYI') or key in ('PYTHONHOME', 'PYTHONPATH', '_MEIPASS'):
            env.pop(key, None)
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env

_NOTIFY_HOOK = None

def set_notify_hook(fn):
    global _NOTIFY_HOOK
    _NOTIFY_HOOK = fn

def notify_user(title, message, warning=False):
    level = log.warning if warning else log.info
    level('%s：%s', title, message)
    hook = _NOTIFY_HOOK
    if hook:
        try:
            hook(title, message, warning)
        except Exception as exc:
            log.warning('桌面通知失败：%s', exc)

def cleanup_data(cfg, days=0, dry_run=False):
    import time as _time
    import douk_dl
    root = douk_volume_dir(cfg)
    cutoff = _time.time() - days * 86400 if days and days > 0 else None
    removed_files, freed, skipped = 0, 0, 0
    if cutoff and os.path.isdir(root):
        for base, _dirs, names in os.walk(root):
            for name in names:
                full = os.path.join(base, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if st.st_mtime >= cutoff:
                    continue
                if dry_run:
                    removed_files += 1
                    freed += st.st_size
                    continue
                try:
                    os.remove(full)
                    removed_files += 1
                    freed += st.st_size
                except OSError as exc:
                    skipped += 1
                    log.warning('删不掉 %s：%s', full, exc)
                    continue
        for base, _dirs, _names in os.walk(root, topdown=False):
            if os.path.abspath(base) == os.path.abspath(root):
                continue
            try:
                if not os.listdir(base):
                    os.rmdir(base)
            except OSError:
                continue
    records_path = douk_dl.records_path(data_root(cfg))
    records = douk_dl.load_records(data_root(cfg))
    stale = []
    for key, info in list(records.items()):
        path = ''
        if isinstance(info, dict):
            path = str(info.get('file') or info.get('path') or '')
        if path and not os.path.isabs(path):
            path = os.path.join(data_root(cfg), path)
        if not path:
            continue
        if os.path.exists(path):
            continue
        stale.append(key)
    if stale:
        if not dry_run:
            for key in stale:
                records.pop(key, None)
            douk_dl.save_records(records_path, records)
    return {'files': removed_files, 'freed': freed, 'records': len(stale), 'skipped': skipped, 'dry_run': bool(dry_run), 'days': days}

def platform_of(obj):
    value = ''
    if isinstance(obj, dict):
        value = str(obj.get('platform') or '')
    elif obj:
        value = str(obj)
    value = value.strip().lower()
    if value in PLATFORMS:
        return value
    return DEFAULT_PLATFORM

def platform_label(platform):
    return PLATFORMS.get(platform_of(platform), PLATFORMS[DEFAULT_PLATFORM])

def platform_of_url(url):
    return 'tiktok' if TIKTOK_DOMAIN_RE.search(url or '') else 'douyin'

log = logging.getLogger('douyin_monitor')

class RateLimited(RuntimeError):
    pass

_RATE_LOCK = threading.Lock()
_RATE_UNTIL = 0.0
_RATE_STRIKES = 0
_LAST_POST_REQ = 0.0
_MIN_POST_GAP = 0.2

def rate_limit_remaining():
    with _RATE_LOCK:
        return max(0.0, _RATE_UNTIL - time.time())

def _rate_trip(detail=''):
    global _RATE_STRIKES, _RATE_UNTIL
    with _RATE_LOCK:
        _RATE_STRIKES += 1
        mins = min(60, 10 * 2 ** min(_RATE_STRIKES - 1, 3))
        _RATE_UNTIL = time.time() + mins * 60.0
        strikes = _RATE_STRIKES
    log.warning('抖音作品列表接口被限流（返回空响应%s）。已自动降频：暂停翻页请求 %d 分钟，到期自动试探恢复（连续第 %d 次）。期间已下载内容不受影响，详情/主页/单独下载仍可用。', detail, mins, strikes)

def _rate_clear():
    global _RATE_STRIKES, _RATE_UNTIL
    with _RATE_LOCK:
        _RATE_STRIKES = 0
        _RATE_UNTIL = 0.0

def _throttle_post():
    global _LAST_POST_REQ
    with _RATE_LOCK:
        delta = time.time() - _LAST_POST_REQ
        wait = _MIN_POST_GAP - delta
        if 0 < wait < _MIN_POST_GAP:
            time.sleep(wait)
        _LAST_POST_REQ = time.time()

PLATFORMS = {'douyin': '抖音', 'tiktok': 'TikTok'}
DEFAULT_PLATFORM = 'douyin'
LIVE_QUALITY_LABELS = (('ORIGIN', '源画质'), ('FULL_HD1', '原画'), ('HD1', '高清'), ('SD1', '标清'), ('SD2', '流畅'))
LIVE_QUALITY_NAMES = dict(LIVE_QUALITY_LABELS)
# 菜单里给每个档位标注「相对于什么分辨率」，方便用户判断该选哪个
LIVE_QUALITY_HINTS = {
    'ORIGIN': '原始流最高，随主播推流，常见 1080p',
    'FULL_HD1': '平台原画，多数 720p，部分 1080p',
    'HD1': '约 540p，960×540',
    'SD1': '约 360p，640×360',
    'SD2': '约 270p，480×270',
}
POST_PATH = '/aweme/v1/web/aweme/post/'
PROFILE_PATH = '/aweme/v1/web/user/profile/other/'
DETAIL_PATH = '/aweme/v1/web/aweme/detail/'
LIVE_ENTER_PATH = '/webcast/room/web/enter/'
SEARCH_USER_PATH = '/aweme/v1/web/discover/search/'
COOKIE_KEYS = {'douyin': 'cookie', 'tiktok': 'cookie_tiktok'}
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36'
MOBILE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
URL_RE = re.compile("https?://[A-Za-z0-9\\-._~:/?#\\[\\]@!$&'()*+,;=%]+")
SEC_UID_RE = re.compile('MS4wLjABAAAA[A-Za-z0-9_\\-]+')
USER_PATH_RE = re.compile('(?:douyin|iesdouyin)\\.com/(?:share/)?user/([A-Za-z0-9_.\\-]+)')
SHORT_LINK_RE = re.compile('(?:v\\.douyin\\.com|v\\.iesdouyin\\.com|iesdouyin\\.com/share)', re.I)

def extract_url(text):
    m = URL_RE.search(text or '')
    if m:
        return m.group(0).rstrip('.,;:!?"\'）)】]')
    return ''

POST_ID_RE = re.compile('/(?:video|note|slides|photo)/(\\d{15,25})|modal_id=(\\d{15,25})|"aweme_id"\\s*:\\s*"?(\\d{15,25})|/share/(?:video|slides|photo)/(\\d{15,25})')
DETAIL_PARAMS = {'aid': '6383', 'channel': 'channel_pc_web', 'pc_client_type': '1', 'version_code': '190500', 'version_name': '19.5.0', 'cookie_enabled': 'true', 'screen_width': '1536', 'screen_height': '864', 'browser_language': 'zh-CN', 'browser_platform': 'Win32', 'browser_name': 'Chrome', 'browser_version': '146.0.0.0', 'browser_online': 'true', 'engine_name': 'Blink', 'engine_version': '146.0.0.0', 'os_name': 'Windows', 'os_version': '10', 'cpu_core_num': '8', 'device_memory': '8', 'platform': 'PC'}

def read_json(path, default=None):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception as exc:
        log.warning('JSON 文件读取失败（%s）：%s', path, exc)
        return default

def write_json(path, data, indent=1):
    path = os.fspath(path)
    folder = os.path.dirname(os.path.abspath(path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    body = json.dumps(data, ensure_ascii=False, indent=indent)
    last = None
    for attempt in range(5):
        tmp = '%s.%d.%d.tmp' % (path, os.getpid(), threading.get_ident())
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            last = exc
            try:
                if os.path.exists(tmp):
                    os.chmod(tmp, 438)
                    os.remove(tmp)
            except OSError:
                pass
            time.sleep(0.08 * (attempt + 1))
        return
    with open(path, 'w', encoding='utf-8') as f:
        f.write(body)
    log.warning('原子替换失败（%s），已改为直接写入：%s', last, path)

def load_config(path):
    cfg = read_json(path, {}) or {}
    cfg.setdefault('douk_dir', '')
    cfg.setdefault('sec_user_id', '')
    cfg.setdefault('poll_interval', 300)
    cfg.setdefault('live_check', True)
    cfg.setdefault('download_existing_on_first_run', False)
    cfg.setdefault('download_video', True)
    cfg.setdefault('download_images', True)
    cfg.setdefault('download_live_url', True)
    cfg.setdefault('max_posts_per_poll', 5)
    cfg.setdefault('request_timeout', 20)
    cfg.setdefault('state_file', os.path.join(BASE_DIR, 'state.json'))
    cfg.setdefault('log_file', os.path.join(BASE_DIR, 'monitor.log'))
    cfg.setdefault('live_url_dir', os.path.join(BASE_DIR, '直播地址'))
    cfg.setdefault('webhook_url', '')
    cfg.setdefault('douk_download_timeout', 900)
    cfg.setdefault('live_quality', 'FULL_HD1')
    for key in ('state_file', 'log_file', 'live_url_dir'):
        if os.path.isabs(cfg[key]):
            continue
        cfg[key] = os.path.join(BASE_DIR, cfg[key])
    return cfg

def setup_logging(log_file):
    log.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', '%Y-%m-%d %H:%M:%S')
    try:
        from logging.handlers import RotatingFileHandler
        fh = RotatingFileHandler(log_file, maxBytes=2097152, backupCount=2, encoding='utf-8')
    except Exception:
        fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.handlers[:] = [fh, sh]
    try:
        sys.stdout.reconfigure(errors='backslashreplace')
    except Exception:
        return

def resolve_lib_dir(douk_dir=''):
    try:
        import signlib
        return os.path.join(os.path.dirname(os.path.abspath(signlib.__file__)), 'enc')
    except Exception:
        return ''

class DouYin:
    platform = 'douyin'
    def __init__(self, douk_dir='', timeout=20, data_dir=''):
        self.douk_dir = douk_dir
        import signlib
        self.signer = signlib.DouYinParams()
        self.base_params = dict(signlib.API_PARAMS)
        self.timeout = timeout
        try:
            import douk_rt
            self.settings_path = douk_rt.settings_path(str(data_dir or ''))
        except Exception:
            self.settings_path = os.path.join(str(data_dir or ''), 'Volume', 'settings.json')
        self.http_headers = {}
        self.reload_cookie()

    def reload_cookie(self):
        if not os.path.isfile(self.settings_path):
            raise RuntimeError(f'找不到登录配置文件：{self.settings_path}')
        with open(self.settings_path, 'r', encoding='utf-8-sig') as f:
            self.settings = json.load(f)
        cookie = self.settings.get('cookie') or {}
        if isinstance(cookie, str):
            cookie = dict((p.split('=', 1) for p in cookie.split('; ') if '=' in p))
        self.cookie_map = cookie
        self.cookie_str = '; '.join((f'{k}={v}' for k, v in cookie.items() if v))
        if not self.cookie_map.get('sessionid'):
            log.warning('尚未登录抖音（settings.json 无 sessionid），请先扫码登录')

    def _base_params(self):
        return dict(self.base_params)

    def signed_query(self, path, params, host='https://www.douyin.com'):
        q = self._base_params()
        q['uifid'] = self.cookie_map.get('UIFID', '')
        q.update(params)
        return self.signer.sign_url(host + path, q, None, 'GET', UA, '')

    def get_json(self, path, params, host='https://www.douyin.com', referer=None):
        from urllib.request import Request, urlopen
        is_post = path == POST_PATH
        if is_post:
            left = rate_limit_remaining()
            if left > 0:
                raise RateLimited('作品列表接口冷却中，剩余约 %d 分钟' % (left / 60 + 1))
            _throttle_post()
        qs = self.signed_query(path, params, host)
        headers = {'User-Agent': UA, 'Accept': 'application/json, text/plain, */*', 'Referer': referer or ('https://www.douyin.com/' if 'douyin.com' in host and 'live.' not in host else 'https://live.douyin.com/'), 'Cookie': self.cookie_str}
        req = Request(host + path + '?' + qs, headers=headers)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except Exception as exc:
            raise RuntimeError(classify_error(exc, 'douyin')) from exc
        if is_post and (not raw or not raw.strip()):
            _rate_trip('（%s）' % path)
            raise RateLimited('作品列表接口返回空响应（风控限流）')
        try:
            out = json.loads(raw.decode('utf-8', 'replace'))
        except Exception as exc:
            raise RuntimeError(classify_error(exc, 'douyin')) from exc
        if is_post:
            _rate_clear()
        return out

    def user_info_by_unique_id(self, unique_id):
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        url = 'https://www.iesdouyin.com/web/api/v2/user/info/?' + urlencode({'unique_id': unique_id})
        req = Request(url, headers={'User-Agent': MOBILE_UA, 'Accept': 'application/json'})
        try:
            with urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode('utf-8', 'replace'))
        except Exception as exc:
            log.debug('user_info 接口不可用：%s', exc)
            return {}
        info = data.get('user_info') or {}
        if info.get('sec_uid'):
            return info
        return {}

    def resolve_redirect(self, url):
        from urllib.request import Request, urlopen
        req = Request(url, headers={'User-Agent': UA, 'Referer': 'https://www.douyin.com/'})
        with urlopen(req, timeout=10) as resp:
            final = resp.geturl()
            body = resp.read(80000).decode('utf-8', 'ignore')
        return final, body

    def resolve_sec_uid(self, spec):
        raw = (spec or '').strip()
        url = extract_url(raw)
        if url:
            raw = url
        m = SEC_UID_RE.search(raw)
        if m:
            return m.group(0)
        m = USER_PATH_RE.search(raw)
        if m:
            spec = m.group(1)
        else:
            spec = raw
        if SHORT_LINK_RE.search(raw):
            try:
                final, body = self.resolve_redirect(raw)
            except Exception as e:
                raise RuntimeError('打不开这个主页链接（短链可能失效），请重新复制：%s' % e)
            m = SEC_UID_RE.search(final) or SEC_UID_RE.search(body)
            if m:
                return m.group(0)
            m = USER_PATH_RE.search(final)
            if m:
                spec = m.group(1)
            else:
                raise RuntimeError('这个主页链接里没找到账号（短链可能过期了），请复制 www.douyin.com/user/... 形式的链接')
        if spec and not spec.startswith('http'):
            info = self.user_info_by_unique_id(spec)
            if info.get('sec_uid'):
                return info['sec_uid']
        d = self.get_json(SEARCH_USER_PATH, {'keyword': spec, 'search_channel': 'aweme_user_web', 'search_source': 'normal_search', 'query_correct_type': '1', 'is_filter_search': '0', 'from_group_id': '', 'offset': '0', 'count': '10', 'need_filter_settings': '1', 'list_type': 'single'})
        users = d.get('user_list') or []
        cands = []
        for it in users:
            u = it.get('user_info') or it
            cands.append((u.get('unique_id') or '', u.get('short_id') or '', u.get('nickname') or '', u.get('sec_uid') or ''))
        for uniq, short, nick, sec in cands:
            if spec not in (uniq, short, nick):
                continue
            if not sec:
                continue
            return sec
        if cands and cands[0][3] and not url:
            log.warning('未精确匹配到 %s，使用搜索结果第一个账号：%s', spec, cands[0][2])
            return cands[0][3]
        raise RuntimeError(f'无法解析目标账号：{spec}')

    def fetch_profile(self, sec_uid):
        d = self.get_json(PROFILE_PATH, {'sec_user_id': sec_uid, 'publish_video_strategy_type': '2', 'need_fields': '', 'from_user_page': '1'}, referer=f'https://www.douyin.com/user/{sec_uid}')
        return d.get('user') or {}

    def fetch_detail(self, aweme_id):
        d = self.get_json(DETAIL_PATH, dict(DETAIL_PARAMS, aweme_id=str(aweme_id)), referer='https://www.douyin.com/')
        if d.get('status_code') not in (0, None):
            raise RuntimeError('获取作品详情失败：status_code=%s（作品可能已删除/私密，或 Cookie 失效）' % d.get('status_code'))
        return d.get('aweme_detail') or {}

    def fetch_posts_page(self, sec_uid, count=18, cursor='0'):
        d = self.get_json(POST_PATH, {'sec_user_id': sec_uid, 'max_cursor': str(cursor or '0'), 'locate_query': 'false', 'show_live_replay_strategy': '1', 'need_time_list': '1', 'time_list_query': '0', 'whale_cut_token': '', 'cut_version': '1', 'count': str(count), 'publish_video_strategy_type': '2', 'from_user_page': '1'}, referer='https://www.douyin.com/user/%s' % sec_uid)
        if d.get('status_code') not in (0, None):
            raise RuntimeError(f'获取作品列表失败：status_code={d.get('status_code')!s} {d.get('status_msg') or ''!s}（Cookie 可能已失效）')
        return d.get('aweme_list') or [], bool(d.get('has_more')), str(d.get('max_cursor') or '0')

    def fetch_posts(self, sec_uid, count=18):
        d = self.get_json(POST_PATH, {'sec_user_id': sec_uid, 'max_cursor': '0', 'locate_query': 'false', 'show_live_replay_strategy': '1', 'need_time_list': '1', 'time_list_query': '0', 'whale_cut_token': '', 'cut_version': '1', 'count': str(count), 'publish_video_strategy_type': '2', 'from_user_page': '1'}, referer=f'https://www.douyin.com/user/{sec_uid}')
        if d.get('status_code') not in (0, None):
            raise RuntimeError(f'获取作品列表失败：status_code={d.get('status_code')} {d.get('status_msg') or ''}（Cookie 可能已失效）')
        return d.get('aweme_list') or []

    def fetch_all_post_ids(self, sec_uid, count=18, max_pages=60):
        ids = []
        cursor = '0'
        seen_cursor = set()
        for _ in range(max_pages):
            d = self.get_json(POST_PATH, {'sec_user_id': sec_uid, 'max_cursor': cursor, 'locate_query': 'false', 'show_live_replay_strategy': '1', 'need_time_list': '1', 'time_list_query': '0', 'whale_cut_token': '', 'cut_version': '1', 'count': str(count), 'publish_video_strategy_type': '2', 'from_user_page': '1'}, referer='https://www.douyin.com/user/%s' % sec_uid)
            if d.get('status_code') not in (0, None):
                raise RuntimeError('获取作品列表失败：status_code=%s' % d.get('status_code'))
            for w in d.get('aweme_list') or []:
                aid = str(w.get('aweme_id') or '')
                if aid:
                    ids.append(aid)
            nxt = str(d.get('max_cursor') or '0')
            if not d.get('has_more') or nxt == '0' or nxt in seen_cursor:
                break
            seen_cursor.add(nxt)
            cursor = nxt
        return ids

    def fetch_live(self, web_rid):
        params = {'aid': '6383', 'app_name': 'douyin_web', 'live_id': '1', 'device_platform': 'web', 'language': 'zh-CN', 'enter_from': 'link_share', 'cookie_enabled': 'true', 'screen_width': '1536', 'screen_height': '864', 'browser_language': 'zh-CN', 'browser_platform': 'MacIntel', 'browser_name': 'Chrome', 'browser_version': '146.0.0.0', 'web_rid': str(web_rid), 'enter_source': '', 'is_need_double_stream': 'false', 'insert_task_id': '', 'live_reason': '', 'msToken': self.cookie_map.get('msToken', '')}
        d = self.get_json(LIVE_ENTER_PATH, params, host='https://live.douyin.com', referer='https://live.douyin.com/')
        rooms = (d.get('data') or {}).get('data') or []
        if not rooms:
            return
        room = rooms[0]
        if room.get('status') != 2:
            return
        streams = room.get('stream_url') or {}
        return {'room_id': str(room.get('id') or room.get('room_id') or ''), 'title': room.get('title') or '', 'nickname': (room.get('owner') or {}).get('nickname') or '', 'flv': streams.get('flv_pull_url') or {}, 'hls': streams.get('hls_pull_url_map') or {}, 'hls_default': streams.get('hls_pull_url') or ''}


def _brief_exc(exc):
    text = str(exc) or exc.__class__.__name__
    text = re.sub('\\s+', ' ', text).strip()
    return text[:180]

def classify_error(exc, platform=DEFAULT_PLATFORM):
    name = platform_label(platform)
    raw = _brief_exc(exc)
    low = raw.lower()
    if isinstance(exc, RateLimited):
        return f'{name!s} 作品列表接口被限流了（{raw!s}）；程序已自动降频暂停，稍后会自动恢复，无需操作'
    if isinstance(exc, TimeoutError) or 'timed out' in low or 'timeout' in low:
        return f'{name!s} 接口超时：{raw!s}（网络慢，或者代理卡住了）'
    if '10054' in raw or 'connection reset' in low or 'reset by peer' in low:
        return f'{name!s} 连接被重置（本机访问不了该网站，TikTok 需要配代理）：{raw!s}'
    if '10060' in raw or '10061' in raw or 'refused' in low:
        return f'{name!s} 连不上服务器（对方端口或代理没开）：{raw!s}'
    if '11001' in raw or 'getaddrinfo' in low or 'name or service not known' in low:
        return f'{name!s} 域名解析失败（检查网络 / 代理）：{raw!s}'
    if ' 401' in raw or ' 403' in raw or ' 404' in raw:
        return f'{name!s} 接口被拒绝（Cookie 很可能已失效，建议重新扫码登录）：{raw!s}'
    if 'certificate' in low or 'sslv' in low or 'ssl:' in low:
        return f'{name!s} HTTPS 握手失败（代理证书有问题？）：{raw!s}'
    if 'expecting value' in low or 'json' in low and 'decode' in low:
        return f'{name!s} 返回的不是正常数据（可能被风控拦了，稍后再试）：{raw!s}'
    if 'nonetype' in low and 'attribute' in low:
        return f'{name!s} 接口没有返回数据（网络不通 / 代理没生效 / Cookie 失效）：{raw!s}'
    return f'{name!s} 请求失败：{raw!s}'

def probe_proxy(proxy='', url='', timeout=8, platform=DEFAULT_PLATFORM):
    import time as _time
    from urllib.request import (ProxyHandler, Request, build_opener)
    platform = platform_of(platform)
    target = url or ('https://www.tiktok.com/' if platform == 'tiktok' else 'https://www.douyin.com/')
    px = (proxy or '').strip()
    if px and '://' not in px:
        px = 'http://' + px
    handler = ProxyHandler({'http': px, 'https': px} if px else {})
    req = Request(target, headers={'User-Agent': BROWSER_UA, 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8'})
    t0 = _time.time()
    try:
        with build_opener(handler).open(req, timeout=timeout) as resp:
            size = len(resp.read(2048) or b'')
            cost = _time.time() - t0
            return True, '连通正常（HTTP %s，%d 字节，耗时 %.1fs）' % (getattr(resp, 'status', '?'), size, cost)
    except Exception as exc:
        return False, classify_error(exc, platform)

# Cookie 校验用的公开示例账号（不是凭据，只用于探测登录态是否有效）
VERIFY_SAMPLE_SEC_UID = 'MS4wLjABAAAA8U_l6rBzmy7bcy6xOJel4v0RzoR_wfAubGPeJimN__4'

ANSI_RE = re.compile('\\x1b\\[[0-9;?]*[A-Za-z]')

class TikTok:
    platform = 'tiktok'
    HOST = 'https://www.tiktok.com'
    LIVE_HOST = 'https://webcast.us.tiktok.com'
    FIXED_PARAMS = {'aid': '1988', 'app_language': 'en', 'app_name': 'tiktok_web', 'browser_language': 'zh-SG', 'browser_name': 'Mozilla', 'browser_online': 'true', 'browser_platform': 'MacIntel', 'browser_version': '5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36', 'channel': 'tiktok_web', 'cookie_enabled': 'true', 'data_collection_enabled': 'true', 'device_id': '', 'device_platform': 'web_pc', 'enable_cache': 'true', 'focus_state': 'true', 'from_page': 'user', 'history_len': '4', 'is_fullscreen': 'false', 'is_page_visible': 'true', 'language': 'en', 'os': 'mac', 'priority_region': 'US', 'referer': '', 'region': 'US', 'screen_height': '864', 'screen_width': '1536', 'tz_name': 'Asia/Shanghai', 'user_is_login': 'true', 'webcast_language': 'en', 'msToken': ''}
    def __init__(self, douk_dir='', timeout=20, proxy='', data_dir=''):
        import signlib
        self.douk_dir = douk_dir
        self.signer = signlib.TikTokParams()
        self.timeout = timeout
        self.proxy = (proxy or '').strip()
        try:
            import douk_rt
            self.settings_path = douk_rt.settings_path(str(data_dir or ''))
        except Exception:
            self.settings_path = os.path.join(str(data_dir or ''), 'Volume', 'settings.json')
        self.reload_cookie()

    def reload_cookie(self):
        with open(self.settings_path, 'r', encoding='utf-8-sig') as f:
            self.settings = json.load(f)
        cookie = self.settings.get('cookie_tiktok') or {}
        if isinstance(cookie, str):
            cookie = parse_cookie_text(cookie)
        self.cookie_map = {str(k): str(v) for k, v in (cookie or {}).items() if v not in (None, '')}
        self.cookie_str = cookie_to_text(self.cookie_map)
        if not self.proxy:
            proxy = self.settings.get('proxy_tiktok')
            self.proxy = proxy.strip() if isinstance(proxy, str) else ''
        if not self.cookie_map.get('sessionid'):
            log.warning('尚未登录 TikTok（cookie_tiktok 没有 sessionid），TikTok 账号可能取不到数据')

    def _opener(self):
        from urllib.request import ProxyHandler, build_opener
        if self.proxy:
            return build_opener(ProxyHandler({'http': self.proxy, 'https': self.proxy}))
        return build_opener()

    def get_json(self, url, params, host=''):
        base = (host or self.HOST) + url
        q = dict(self.FIXED_PARAMS)
        q.update({str(k): '' if v is None else str(v) for k, v in (params or {}).items()})
        qs = self.signer.sign_url(base, q, None, 'GET', signlib_ua(), '')
        from urllib.request import Request
        headers = {'User-Agent': signlib_ua(), 'Accept': '*/*', 'Accept-Language': 'en-US,en;q=0.9', 'Referer': 'https://www.tiktok.com/explore'}
        if self.cookie_str:
            headers['Cookie'] = self.cookie_str
        req = Request(base + '?' + qs, headers=headers)
        try:
            with self._opener().open(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except Exception as exc:
            raise RuntimeError(classify_error(exc, 'tiktok')) from exc
        try:
            return json.loads(raw.decode('utf-8', 'replace'))
        except Exception as exc:
            raise RuntimeError('TikTok 接口返回的不是 JSON（可能被风控）：%s' % exc) from exc

    def fetch_profile(self, spec):
        spec = (spec or '').strip()
        m = re.search('tiktok\\.com/@([A-Za-z0-9_.\\-]+)', spec)
        if m:
            spec = m.group(1)
        spec = spec.lstrip('@')
        if spec.startswith('http'):
            m = re.search('MS4wLjABAAAA[A-Za-z0-9_\\-]+', spec)
            spec = m.group(0) if m else spec.rstrip('/').rsplit('/', 1)[-1].lstrip('@')
        kwargs = {'secUid': spec} if spec.startswith('MS4w') else {'uniqueId': spec}
        data = self.get_json('/api/user/detail/', kwargs)
        info = data if isinstance(data, dict) else {}
        if 'userInfo' in info:
            info = info['userInfo']
        user = info.get('user') or {}
        stats = info.get('stats') or {}
        room = str(user.get('roomId') or '0')
        if room == '0':
            return {'nickname': user.get('nickname') or '', 'unique_id': user.get('uniqueId') or '', 'sec_uid': user.get('secUid') or '', 'uid': str(user.get('id') or ''), 'short_id': '', 'signature': user.get('signature') or '', 'follower_count': stats.get('followerCount') or 0, 'aweme_count': stats.get('videoCount') or 0, 'live_status': 1 if room not in ('', '0') else 0, 'room_id': '', 'platform': 'tiktok'}
        return {'nickname': None, 'unique_id': None, 'sec_uid': None, 'uid': None, 'short_id': None, 'signature': None, 'follower_count': None, 'aweme_count': None, 'live_status': None, 'room_id': room, 'platform': 'tiktok'}

    def fetch_posts(self, sec_uid, count=18):
        data = self.get_json('/api/post/item_list/', {'secUid': sec_uid, 'count': count, 'cursor': '0', 'coverFormat': '2', 'post_item_list_request_type': '0'})
        raw = (data or {}).get('itemList') or (data or {}).get('item_list') or []
        return [self._normalize_item(x) for x in raw if isinstance(x, dict)]

    def fetch_posts_page(self, sec_uid, count=18, cursor='0'):
        data = self.get_json('/api/post/item_list/', {'secUid': sec_uid, 'count': count, 'cursor': cursor, 'coverFormat': '2', 'post_item_list_request_type': '0'})
        raw = (data or {}).get('itemList') or (data or {}).get('item_list') or []
        items = [self._normalize_item(x) for x in raw if isinstance(x, dict)]
        nxt = str((data or {}).get('cursor') or '')
        more = bool((data or {}).get('hasMore'))
        if not nxt or nxt == cursor:
            more = False
        return items, more, nxt

    def _normalize_item(self, raw):
        if not isinstance(raw, dict):
            return {}
        aid = str(raw.get('id') or '')
        author = raw.get('author') or {}
        unique = author.get('uniqueId') or ''
        images = []
        for img in (raw.get('imagePost') or {}).get('images') or []:
            urls = ((img or {}).get('imageURL') or {}).get('urlList') or []
            if urls:
                images.append(urls[0])
        kind_path = 'photo' if images else 'video'
        share = raw.get('shareUrl') or (f'https://www.tiktok.com/@{unique or 'user'!s}/{kind_path!s}/{aid!s}' if aid else '')
        return {'aweme_id': aid, 'desc': raw.get('desc') or '', 'create_time': int(raw.get('createTime') or 0), 'images': images, 'share_url': share, 'author_id': unique, 'platform': 'tiktok'}

    def fetch_live(self, room_id):
        data = self.get_json('/webcast/room/enter/', {'room_id': str(room_id), 'aid': '1988', 'app_language': 'en', 'webcast_language': 'en'}, host=self.LIVE_HOST)
        if not isinstance(data, dict):
            return
        room = data.get('data') or {}
        streams = room.get('stream_url') or {}
        if not streams:
            return
        flv = streams.get('flv_pull_url') or {}
        hls = streams.get('hls_pull_url_map') or {}
        return {'room_id': str(room.get('id') or room_id), 'title': room.get('title') or '', 'nickname': (room.get('owner') or {}).get('nickname') or '', 'flv': flv if isinstance(flv, dict) else {}, 'hls': hls if isinstance(hls, dict) else {}, 'hls_default': streams.get('hls_pull_url') or ''}


def signlib_ua():
    try:
        import signlib
    except Exception:
        return UA
    return signlib.UA

def make_client(cfg, platform=DEFAULT_PLATFORM, timeout=20):
    data_dir = str(cfg.get('data_dir') or '')
    if platform_of(platform) == 'tiktok':
        return TikTok('', timeout=timeout or 20, proxy=cfg.get('tiktok_proxy') or '', data_dir=data_dir)
    return DouYin('', timeout=timeout or 20, data_dir=data_dir)

def verify_cookie_tiktok(cfg, sec_user_id='', timeout=20):
    try:
        tk = make_client(cfg, 'tiktok', timeout=timeout)
    except Exception as exc:
        return False, 'TikTok 初始化失败：%s' % exc
    if not tk.cookie_map.get('sessionid'):
        return (False, 'TikTok Cookie 里没有 sessionid，不算登录状态')
    spec = (sec_user_id or 'tiktok').strip() or 'tiktok'
    try:
        profile = tk.fetch_profile(spec)
    except Exception as exc:
        return False, classify_error(exc, 'tiktok')
    nick = profile.get('nickname') or ''
    if profile.get('sec_uid') or nick:
        return True, 'TikTok Cookie 可用（测试账号：%s）' % (nick or spec)
    return (False, 'TikTok 接口没有返回账号信息，Cookie 可能已失效')

def account_url(sec_user_id, platform=DEFAULT_PLATFORM):
    if platform_of(platform) == 'tiktok':
        return 'https://www.tiktok.com/@%s' % sec_user_id
    return 'https://www.douyin.com/user/%s' % sec_user_id

def douk_download(cfg, urls, timeout=None, platform=DEFAULT_PLATFORM, dy=None, dest='', alt_dirs=()):
    platform = platform_of(platform)
    client = _client_for(cfg, platform, dy=dy, timeout=timeout)
    items, failed = [], []
    for url in urls or []:
        url = (url or '').strip()
        if not url:
            continue
        final, html = expand_post_url(url, timeout=timeout or 15)
        aid = post_id_from(final or url, html)
        if not aid:
            failed.append((url, '没认出作品 ID'))
            continue
        fetch = getattr(client, 'fetch_detail', None)
        if fetch is None:
            failed.append((url, '该平台没有详情接口，暂不支持这样单独下载'))
            continue
        try:
            detail = fetch(aid)
        except Exception as exc:
            failed.append((url, classify_error(exc, platform)))
            continue
        if not detail:
            failed.append((url, '详情接口没有返回数据（作品可能已删除或私密）'))
            continue
        items.append(detail)
    stats, files, text = download_posts(cfg, items, client, platform, dest=dest, timeout=timeout,
                                        alt_dirs=alt_dirs)
    if failed:
        lines = [f'失败：{u!s}｜{reason!s}' for u, reason in failed]
        text = (text + '\n' if text else '') + '\n'.join(lines)
    return stats, files, text

def douk_download_accounts(cfg, account_urls, timeout=None, platform=DEFAULT_PLATFORM, progress=None,
                           dest=''):
    platform = platform_of(platform)
    client = _client_for(cfg, platform, timeout=timeout)
    items, failed = [], []
    seen = set()
    max_pages = int(cfg.get('account_max_pages') or 400)
    for spec in account_urls or []:
        spec = (spec or '').strip()
        if not spec:
            continue
        try:
            if platform == 'tiktok':
                profile = client.fetch_profile(spec)
            else:
                profile = client.fetch_profile(client.resolve_sec_uid(spec))
        except Exception as exc:
            failed.append((spec, classify_error(exc, platform)))
        sec = profile.get('sec_uid') or ''
        if not sec:
            failed.append((spec, '没解析出 sec_uid'))
            continue
        expected = int(profile.get('aweme_count') or 0)
        cursor, pages, got_here = '0', 0, 0
        while pages < max_pages:
            page, more, nxt = [], False, ''
            for attempt in range(3):
                try:
                    if hasattr(client, 'fetch_posts_page'):
                        page, more, nxt = client.fetch_posts_page(sec, count=18, cursor=cursor)
                    else:
                        page, more, nxt = client.fetch_posts(sec, count=18), False, ''
                    if page or not more:
                        break
                except Exception as exc:
                    if attempt >= 2:
                        failed.append((spec, classify_error(exc, platform)))
                    break
                time.sleep(1.2)
            if not page and more:
                failed.append((spec, '翻页中途只拿到空列表，可能被限流；已下载的保留，稍后可再点一次'))
                break
            fresh = [x for x in page or []
                 if str(x.get('aweme_id') or '') not in seen]
            for x in fresh:
                seen.add(str(x.get('aweme_id') or ''))
            items.extend(fresh)
            got_here += len(fresh)
            pages += 1
            if not more or not nxt or nxt == cursor or not page:
                break
            cursor = nxt
        if not expected:
            continue
        if not got_here < expected:
            continue
        log.info('账号 %s：接口返回 %d 个作品（主页显示 %d 个，少的可能是私密/已删/置顶重复）', profile.get('nickname') or spec, got_here, expected)
        continue
    if not items:
        text = '没有取到作品' + ('；失败：' + '；'.join(('%s｜%s' % f for f in failed)) if failed else '')
        return {k: 0 for k in ('下载视频作品', '跳过视频作品', '下载图集作品', '跳过图集作品', '下载实况作品', '跳过实况作品')}, [], text
    stats, files, text = download_posts(cfg, items, client, platform, timeout=timeout,
                                        dest=dest, progress=progress)
    if failed:
        text = (text + '\n' if text else '') + '；'.join(('%s｜%s' % f for f in failed))
    return stats, files, text

LINK_SPLIT_RE = re.compile('[\\s,，、;；|]+')
DOUYIN_DOMAIN_RE = re.compile('(?:v\\.douyin\\.com|douyin\\.com|iesdouyin\\.com)', re.I)
TIKTOK_DOMAIN_RE = re.compile('(?:vm\\.tiktok\\.com|vt\\.tiktok\\.com|tiktok\\.com)', re.I)
ANY_DOMAIN_RE = re.compile('(?:douyin\\.com|iesdouyin\\.com|tiktok\\.com)', re.I)

def link_key(url):
    text = (url or '').strip()
    m = re.search('/(?:video|note|slides|photo)/(\\d{6,})', text)
    if m:
        return m.group(1)
    m = re.search('modal_id=(\\d{6,})', text)
    if m:
        return m.group(1)
    m = re.search('(v\\.douyin\\.com/[A-Za-z0-9]+)', text, re.I)
    if m:
        return m.group(1).lower().rstrip('/')
    m = re.search('((?:vm|vt)\\.tiktok\\.com/[A-Za-z0-9]+)', text, re.I)
    if m:
        return m.group(1).lower().rstrip('/')
    return text.split('?')[0].rstrip('/').lower()

def extract_links(text):
    out, seen = [], set()
    for token in LINK_SPLIT_RE.split((text or '').replace('　', ' ')):
        token = token.strip().strip('"\'<>()[]{}（）【】')
        if not token:
            continue
        if re.fullmatch('\\d{15,25}', token):
            token = 'https://www.douyin.com/video/' + token
        else:
            if not DOUYIN_DOMAIN_RE.search(token):
                continue
            if not token.lower().startswith(('http://', 'https://')):
                token = 'https://' + token.lstrip('/')
        key = link_key(token)
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return out

def douk_settings_path(cfg):
    cfg = cfg or {}
    try:
        import douk_rt
        return douk_rt.settings_path(str(cfg.get('data_dir') or ''))
    except Exception:
        return os.path.join(str(cfg.get('douk_dir') or ''), 'Volume', 'settings.json')

def load_douk_settings(cfg):
    path = douk_settings_path(cfg)
    if not os.path.isfile(path):
        raise RuntimeError('找不到 DouK 配置文件：%s' % path)
    with open(path, 'r', encoding='utf-8-sig') as f:
        return json.load(f)

def load_cookie_map(cfg, platform=DEFAULT_PLATFORM):
    data = load_douk_settings(cfg)
    cookie = data.get(COOKIE_KEYS.get(platform_of(platform), 'cookie')) or {}
    if isinstance(cookie, str):
        cookie = parse_cookie_text(cookie)
    if not isinstance(cookie, dict):
        return {}
    return {str(k): str(v) for k, v in cookie.items() if v not in (None, '')}

def cookie_to_text(cookie):
    return '; '.join((f'{k!s}={v!s}' for k, v in cookie.items()))

def parse_cookie_text(text):
    text = (text or '').strip()
    if text.lower().startswith('cookie:'):
        text = text[7:]
    cookie = {}
    for part in re.split('[;\\n\\r]+', text):
        part = part.strip().strip('",')
        if not part or '=' not in part:
            continue
        key, val = part.split('=', 1)
        key, val = key.strip(), val.strip().strip('"')
        if key:
            cookie[key] = val
    return cookie

def save_cookie_text(cfg, text, platform=DEFAULT_PLATFORM):
    cookie = parse_cookie_text(text)
    if not cookie:
        raise ValueError('没有解析到任何 Cookie 键值对')
    platform = platform_of(platform)
    path = douk_settings_path(cfg)
    try:
        data = load_douk_settings(cfg)
    except (RuntimeError, FileNotFoundError):
        data = {}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data[COOKIE_KEYS.get(platform, 'cookie')] = cookie if platform == 'douyin' else cookie_to_text(cookie)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8-sig') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    log.info('%s Cookie 已写入：%s（%d 项）', platform_label(platform), path, len(cookie))
    return len(cookie)

def cookie_summary(cfg, platform=DEFAULT_PLATFORM):
    try:
        cookie = load_cookie_map(cfg, platform)
    except Exception as exc:
        return '读取失败：%s' % exc
    if not cookie:
        return '未设置'
    sid = cookie.get('sessionid') or ''
    if sid:
        return '已设置（%d 项，sessionid=%s…）' % (len(cookie), sid[:6])
    return '已设置（%d 项，但没有 sessionid，可能未登录）' % len(cookie)

def verify_cookie(cfg, sec_user_id='', timeout=20, platform=DEFAULT_PLATFORM):
    if platform_of(platform) == 'tiktok':
        return verify_cookie_tiktok(cfg, sec_user_id=sec_user_id, timeout=timeout)
    try:
        dy = DouYin('', timeout=timeout or 20, data_dir=str(cfg.get('data_dir') or ''))
    except Exception as exc:
        return False, '初始化失败：%s' % exc
    if not dy.cookie_map.get('sessionid'):
        return (False, 'Cookie 里没有 sessionid，不算登录状态')
    # 只用来确认 Cookie 能否取到资料；任意公开账号都可，可用环境变量覆盖
    sec = (sec_user_id or os.environ.get('DOUYIN_VERIFY_SEC_UID')
           or VERIFY_SAMPLE_SEC_UID)
    try:
        profile = dy.fetch_profile(sec)
    except Exception as exc:
        return False, '请求抖音接口失败：%s' % exc
    nick = profile.get('nickname') or ''
    if profile.get('sec_uid') or nick:
        return True, 'Cookie 可用（测试账号：%s）' % (nick or sec[:12])
    return (False, '接口没有返回账号信息，Cookie 可能已失效')

def clear_download_records(cfg, sec_user_id, platform=DEFAULT_PLATFORM, dy=None):
    """清空某账号全部作品在本程序「下载记录.json」里的记录。

    返回 (作品总数, 实际删除的记录数)。已下载的文件不会被删除；
    清空后该账号的历史作品可以重新下载（文件还在时会覆盖同名文件）。
    """
    dy = dy or DouYin("", timeout=cfg.get("request_timeout", 20),
                      data_dir=str(cfg.get("data_dir") or ""))
    platform = platform_of(platform)
    ids = [str(i) for i in (dy.fetch_all_post_ids(sec_user_id) or []) if i]
    if not ids:
        return 0, 0
    import douk_dl
    root = data_root(cfg)
    records = douk_dl.load_records(root)
    deleted = 0
    for aid in ids:
        for prefix in (platform, "douyin", "tiktok"):
            key = "%s:%s" % (prefix, aid)
            if key in records:
                records.pop(key, None)
                deleted += 1
                break
    if deleted:
        douk_dl.save_records(root, records)
    return len(ids), deleted


def download_account_history(cfg, sec_user_id, timeout=None, nickname='', platform=DEFAULT_PLATFORM, progress=None):
    dest = (cfg.get('save_dir') or '').strip()
    return douk_download_accounts(cfg, [account_url(sec_user_id, platform)], timeout=timeout,
                                  platform=platform, progress=progress, dest=dest)

def link_platform(url):
    try:
        import link_resolver
        return link_resolver.detect(url)
    except Exception:
        return 'tiktok' if TIKTOK_DOMAIN_RE.search(url or '') else 'douyin', platform_label(platform_of_url(url))

def is_native_link(key_or_url):
    try:
        import link_resolver
        return link_resolver.is_native(key_or_url)
    except Exception:
        key = key_or_url
        return key in ('douyin', 'tiktok', 'tiktok_short')

def live_record_dir(cfg):
    custom = str(cfg.get('live_record_dir') or '').strip()
    if custom:
        return custom
    return os.path.join(data_root(cfg), '直播录像')

def other_platform_dir(cfg):
    custom = (cfg.get('save_dir') or '').strip()
    if custom:
        return custom
    return os.path.join(douk_volume_dir(cfg), '其它平台')

def download_links(cfg, links, timeout=None):
    t0 = time.time()
    native, others = [], []
    for link in links or []:
        key, _name = link_platform(link)
        if is_native_link(key):
            native.append(link)
        else:
            others.append(link)
    stats, texts, files = {}, [], []
    if native:
        one_stats, one_files, one_text = douk_download(cfg, native, timeout=timeout, platform='douyin',
                                                      dest=(cfg.get('save_dir') or '').strip())
        for key, value in (one_stats or {}).items():
            stats[key] = stats.get(key, 0) + int(value or 0)
        files.extend(one_files or [])
        texts.append(one_text)

    if others:
        try:
            import link_resolver
        except Exception as exc:
            texts.append('其它平台解析模块不可用：%s' % exc)
            link_resolver = None
        if link_resolver is not None:
            proxy = str(cfg.get('tiktok_proxy') or cfg.get('proxy') or '').strip()
            target = other_platform_dir(cfg)
            res = link_resolver.download_many(others, target, proxy=proxy, timeout=int(cfg.get('request_timeout') or 30) * 2, quality=str(cfg.get('link_quality') or ''))
            ok = [r for r in res['results'] if r.get('ok')]
            bad = [r for r in res['results'] if not r.get('ok')]
            if ok:
                stats['下载其它平台作品'] = len(ok)
            if bad:
                stats['其它平台失败'] = len(bad)
            files.extend(res['files'])
            for r in res['results']:
                if r.get('ok'):
                    texts.append('%s 下载完成：%s（%d 个文件，%ss）' % (r['platform'], r.get('title') or r['url'], r.get('files') or 0, r.get('seconds')))
                else:
                    texts.append(f'{r['platform']!s} 失败：{r.get('msg')!s}')
    return stats, files, '\n'.join(texts)

def douk_volume_dir(cfg):
    try:
        import douk_rt
        return douk_rt.volume_dir(str((cfg or {}).get('data_dir') or ''))
    except Exception:
        return os.path.join(str((cfg or {}).get('douk_dir') or ''), 'Volume')

def account_storage_dirs(cfg, nickname='', uid=''):
    vol = douk_volume_dir(cfg)
    nick = (nickname or '').strip()
    uid = str(uid or '').strip()
    out = []
    if not os.path.isdir(vol):
        return out
    suffix = '_%s_发布作品' % nick if nick else None
    prefix = 'UID%s_' % uid if uid else ''
    for name in os.listdir(vol):
        p = os.path.join(vol, name)
        if not os.path.isdir(p):
            continue
        if '_发布作品' not in name:
            continue
        if prefix:
            if name.startswith(prefix):
                out.append(p)
            continue
        if not suffix or name.endswith(suffix):
            out.append(p)
    return sorted(out)

def recent_downloads(cfg, since_ts, nickname='', uid=''):
    dirs = [os.path.join(douk_volume_dir(cfg), 'Download')]
    dirs.extend(account_storage_dirs(cfg, nickname, uid))
    custom = (cfg.get('save_dir') or '').strip()
    if custom:
        dirs.append(custom)
    out = []
    for folder in dirs:
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                p = os.path.join(folder, name)
                try:
                    if os.path.isfile(p) and os.path.getmtime(p) >= since_ts:
                        out.append(os.path.abspath(p))
                except OSError:
                    continue
    return sorted(set(out))

def backup_account_downloads(cfg, nickname, save_dir="", uid="", platform=DEFAULT_PLATFORM):
    """把某账号已下载的作品文件移到 Volume/_旧文件备份/<时间>/ 下。

    覆盖三处：该账号的「历史下载」文件夹、旧的 DouK 账号目录、以及 Download / 自定义保存位置里
    文件名带这个昵称的文件。想完整重新下载时先把旧文件移走（否则会覆盖同名文件）。
    返回 (备份目录, 移走的目录个数, 移走的文件个数)。
    """
    import shutil

    vol = douk_volume_dir(cfg)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest_root = os.path.join(vol, "_旧文件备份", stamp)
    moved_dirs = 0
    moved_files = 0
    nick = (nickname or "").strip()

    def move_dir(src, label):
        nonlocal moved_dirs
        try:
            os.makedirs(dest_root, exist_ok=True)
            shutil.move(src, os.path.join(dest_root, label or os.path.basename(src)))
            moved_dirs += 1
        except OSError as exc:
            log.warning("移动旧文件目录失败：%s (%s)", src, exc)

    def move_file(src, folder_name):
        nonlocal moved_files
        try:
            folder = os.path.join(dest_root, folder_name)
            os.makedirs(folder, exist_ok=True)
            shutil.move(src, os.path.join(folder, os.path.basename(src)))
            moved_files += 1
        except OSError as exc:
            log.warning("移动旧文件失败：%s (%s)", src, exc)

    # 1) 该账号的「历史下载」文件夹
    hist = history_account_dir(cfg, {"nickname": nick, "unique_id": uid, "platform": platform})
    if os.path.isdir(hist):
        move_dir(hist, "历史下载_%s" % nick)
    # 2) 旧 DouK 的账号作品目录
    for src in account_storage_dirs(cfg, nick):
        if os.path.isdir(src):
            move_dir(src, os.path.basename(src))
    # 3) Download / 自定义保存位置：按文件名里的昵称挑
    custom = (save_dir or "").strip()
    roots = [os.path.join(douk_volume_dir(cfg), "Download")]
    if custom and os.path.isdir(custom):
        roots.append(custom)
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            src = os.path.join(root, name)
            if not os.path.isfile(src):
                continue
            same = _nick_from_filename(name) == nick if nick else False
            if not (same or (nick and nick in name)):
                continue
            move_file(src, "保存位置_%s" % nick)
    if not moved_dirs and not moved_files:
        return "", 0, 0
    log.info("已把旧文件移到备份目录：%s", dest_root)
    return dest_root, moved_dirs, moved_files


def download_dir(cfg):
    custom = (cfg.get('save_dir') or '').strip()
    if custom:
        return os.path.abspath(custom)
    return os.path.join(data_root(cfg), 'Volume', 'Download')

def move_downloads(cfg, files, dest):
    import shutil
    dest = os.path.abspath(dest)
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as exc:
        log.warning('保存位置不可写：%s（%s）；这次的作品先留在下载目录 %s', dest, exc, os.path.join(data_root(cfg), 'Volume', 'Download'))
        return []
    moved = []
    failed = []
    for path in files:
        s = path if os.path.isabs(path) else os.path.join(data_root(cfg), 'Volume', 'Download', path)
        if not os.path.isfile(s):
            continue
        if os.path.normcase(os.path.abspath(os.path.dirname(s))) == os.path.normcase(dest):
            moved.append(os.path.basename(s))
            continue
        name = os.path.basename(s)
        stem, ext = os.path.splitext(name)
        target = os.path.join(dest, name)
        n = 1
        while os.path.exists(target):
            n += 1
            target = os.path.join(dest, '%s (%d)%s' % (stem, n, ext))
        try:
            shutil.move(s, target)
            moved.append(os.path.basename(target))
        except OSError as exc:
            failed.append(name)
            log.warning('移动文件失败：%s -> %s（%s）', name, target, exc)
        continue
    if failed and len(failed) == len(files):
        log.warning('这些文件没能移动到「%s」（没有权限或被占用），都还在下载目录里，可以先在「设置 → 默认保存位置」里换一个可写目录', dest)
    return moved

def load_state(path):
    fresh = {'seen': {}, 'last_live_room': '', 'live_active': False, 'first_run_done': False}
    if not os.path.isfile(path):
        return fresh
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        log.warning('状态文件损坏，已重建：%s', path)
        return fresh
    if isinstance(data, dict) and isinstance(data.get('seen'), dict):
        fresh.update(data)
        return fresh
    backup = path + '.client.bak'
    try:
        os.replace(path, backup)
        log.warning('状态文件属于图形客户端，已备份为 %s，命令行版重新开始记录', os.path.basename(backup))
    except OSError:
        pass
    return fresh

def save_state(path, state):
    state['seen'] = dict(list(state['seen'].items())[-800:])
    write_json(path, state)

def notify(cfg, title, content):
    log.info('%s | %s', title, content)
    url = (cfg.get('webhook_url') or '').strip()
    if not url:
        return
    try:
        body = json.dumps({'title': title, 'content': content, 'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}, ensure_ascii=False).encode('utf-8')
        req = Request(url, data=body, headers={'Content-Type': 'application/json'})
        urlopen(req, timeout=10).read()
        return
    except Exception as e:
        log.warning('推送通知失败：%s', e)

def post_kind(item):
    if item.get('images'):
        return '图集'
    return '视频'

def post_url(item):
    if item.get('share_url'):
        return item['share_url']
    if platform_of(item.get('platform')) == 'tiktok':
        return f'https://www.tiktok.com/@{item.get('author_id') or 'user'!s}/video/{item.get('aweme_id')!s}'
    return f'https://www.douyin.com/video/{item.get('aweme_id')}'

def brief_text(item, limit=40):
    return (item.get('desc') or '').replace('\n', ' ')[:limit]

def _fill_report(report, files, items, cfg):
    if report is None:
        return
    dest = (cfg.get('save_dir') or '').strip()
    base = os.path.abspath(dest) if dest else download_dir(cfg)
    abs_files = []
    for f in files or []:
        abs_files.append(f if os.path.isabs(f) else os.path.join(base, f))
    report['files'] = abs_files
    report['items'] = []
    for it in items or []:
        stamp = ''
        try:
            stamp = douk_dl.item_time(it)
        except Exception:
            stamp = ''
        kind = post_kind(it)
        mine = []
        if stamp:
            same = [f for f in abs_files if os.path.basename(f).startswith(stamp)]
            tagged = [f for f in same if '-%s-' % kind in os.path.basename(f)]
            mine = tagged or same
        report['items'].append({'aweme_id': str(it.get('aweme_id') or ''), 'kind': kind, 'desc': brief_text(it), 'url': post_url(it), 'stamp': stamp, 'files': mine})

def repair_seen(cfg, state, days=60, limit=200):
    seen = state.get('seen') or {}
    if not seen:
        return 0
    try:
        records = douk_dl.load_records(data_root(cfg)) or {}
    except Exception as exc:
        log.warning('读取下载记录失败，跳过补登记检查：%s', exc)
        return 0
    if not records:
        return 0
    platform = platform_of(cfg.get('platform') or DEFAULT_PLATFORM)
    now = int(time.time())
    floor = now - int(days) * 86400
    fixed = []
    for aid, info in list(seen.items()):
        if info.get('skip'):
            continue
        ts = int(info.get('create_time') or 0)
        if ts and ts < floor:
            continue
        if records.get(f'{platform!s}:{aid!s}'):
            continue
        fixed.append(aid)
        if len(fixed) >= limit:
            break
    for aid in fixed:
        seen.pop(aid, None)
    if fixed:
        log.info('发现 %d 个作品记着「已处理」但本地没有文件，已重新排入下载队列：%s', len(fixed), '、'.join(fixed[:5]) + ('…' if len(fixed) > 5 else ''))
    return len(fixed)

def fetch_new_items(dy, sec, seen, count=18, max_pages=3, last_ts=0, known=None):
    out = []
    cursor = '0'
    pages = 0
    last_ts = int(last_ts or 0)
    page_api = hasattr(dy, 'fetch_posts_page')
    soft = max(1, int(max_pages or 1))
    cap = max(soft, 8)
    while pages < cap:
        if page_api:
            items, has_more, nxt = dy.fetch_posts_page(sec, count=count, cursor=cursor)
        else:
            items, has_more, nxt = dy.fetch_posts(sec, count=count) or [], False, '0'
        pages += 1
        newest = 0
        oldest = 0
        fresh = 0
        for item in items or []:
            aid = str(item.get('aweme_id') or '')
            if not aid:
                continue
            ts = int(item.get('create_time') or 0)
            if ts:
                newest = max(newest, ts)
                oldest = ts if not oldest else min(oldest, ts)
            if aid in seen:
                continue
            if known and aid in known:
                continue
            out.append(item)
            fresh += 1
        if not fresh:
            break
        if last_ts and newest and newest <= last_ts:
            break
        if not has_more or not nxt or nxt == cursor:
            break
        if pages >= soft and not fresh:
            break
        cursor = nxt
    if pages > 1:
        log.info('翻页检查：共翻了 %d 页，取到 %d 条未见过的新作品', pages, len(out))
    return out

def find_missing(cfg, dy, days=3, max_pages=6, limit=60):
    sec = cfg['sec_user_id']
    platform = platform_of(getattr(dy, 'platform', 'douyin'))
    since = int(time.time()) - max(1, int(days)) * 86400
    try:
        items = fetch_new_items(dy, sec, {}, count=18, max_pages=max_pages, last_ts=since)
    except Exception as exc:
        log.warning('补查作品列表失败：%s', classify_error(exc, platform))
        return []
    try:
        records = douk_dl.load_records(data_root(cfg)) or {}
    except Exception:
        records = {}
    out = []
    for item in items:
        aid = str(item.get('aweme_id') or '')
        if not aid:
            continue
        if int(item.get('create_time') or 0) < since:
            continue
        if records.get(f'{platform!s}:{aid!s}'):
            continue
        out.append(item)
    out.sort(key=(lambda x: x.get('create_time') or 0))
    return out[:max(1, int(limit))]

def find_missing(cfg, dy, days=3, max_pages=6, limit=60):
    sec = cfg['sec_user_id']
    platform = platform_of(getattr(dy, 'platform', 'douyin'))
    since = int(time.time()) - max(1, int(days)) * 86400
    try:
        items = fetch_new_items(dy, sec, {}, count=18, max_pages=max_pages, last_ts=since)
    except Exception as exc:
        log.warning('补查作品列表失败：%s', classify_error(exc, platform))
        return []
    try:
        records = douk_dl.load_records(data_root(cfg)) or {}
    except Exception:
        records = {}
    out = []
    for item in items:
        aid = str(item.get('aweme_id') or '')
        if not aid:
            continue
        if int(item.get('create_time') or 0) < since:
            continue
        if records.get(f'{platform!s}:{aid!s}'):
            continue
        out.append(item)
    out.sort(key=(lambda x: x.get('create_time') or 0))
    return out[:max(1, int(limit))]

def iter_all_posts(dy, sec, count=18, max_pages=400, stats=None):
    seen_ids = set()
    cursor = '0'
    pages = 0
    cap = max(1, int(max_pages or 1))
    page_api = hasattr(dy, 'fetch_posts_page')
    while pages < cap:
        if page_api:
            items, has_more, nxt = dy.fetch_posts_page(sec, count=count, cursor=cursor)
        else:
            items, has_more, nxt = dy.fetch_posts(sec, count=count) or [], False, '0'
        pages += 1
        page = []
        for item in items or []:
            aid = str(item.get('aweme_id') or '')
            if not aid or aid in seen_ids:
                continue
            seen_ids.add(aid)
            page.append(item)
        if page:
            yield page
        if not has_more or not nxt or nxt == cursor:
            break
        cursor = nxt
    if stats is not None:
        stats['pages'] = pages
    log.debug('全量翻页：%d 页（逐页比对，不整表留存）', pages)

def fetch_all_posts(dy, sec, count=18, max_pages=400, stats=None):
    out = []
    for page in iter_all_posts(dy, sec, count=count, max_pages=max_pages, stats=stats):
        out.extend(page)
    return out

def find_missing_all(cfg, dy, limit=200, max_pages=400, stats=None, since_ts=0):
    sec = cfg['sec_user_id']
    platform = platform_of(getattr(dy, 'platform', 'douyin'))
    try:
        _root = data_root(cfg)
        records = douk_dl.load_records(_root) or {}
        if os.environ.get('DOUK_DEBUG_AUDIT'):
            log.info('核账调试：data_dir=%r root=%s 记录 %d 条 platform=%s', (cfg or {}).get('data_dir'), _root, len(records), platform)
    except Exception:
        records = {}
    total = 0
    newest = 0
    out = []
    since_ts = int(since_ts or 0)
    for page in iter_all_posts(dy, sec, count=20, max_pages=max_pages, stats=stats):
        page_newest = 0
        for item in page:
            ts = int(item.get('create_time') or 0)
            if ts:
                page_newest = max(page_newest, ts)
                newest = max(newest, ts)
            if since_ts and ts and ts < since_ts:
                continue
            total += 1
            aid = str(item.get('aweme_id') or '')
            if not aid:
                continue
            if records.get(f'{platform!s}:{aid!s}'):
                continue
            out.append(item)
        # 增量核账：作品按时间倒序返回，翻到「上次已核过的时间段」就不用再往下翻
        if since_ts and page_newest and page_newest < since_ts:
            if total:
                log.info('增量核账：本轮检查 %d 个作品，已覆盖到上次核账时间，余下作品不再重复扫描', total)
            break
    if out and not records:
        path = douk_dl.records_path(_root)
        if os.path.isfile(path) and os.path.getsize(path) > 4096:
            log.warning('下载记录读出来是空的（文件 %d 字节），本轮不补下载：%s', os.path.getsize(path), path)
            return [], total, 0
    out.sort(key=(lambda x: x.get('create_time') or 0))
    try:
        cfg['_audit_newest_ts'] = newest
    except TypeError:
        pass
    return out[:max(1, int(limit))], total, len(out)

def diagnose_posts(cfg, dy, max_pages=400):
    sec = cfg['sec_user_id']
    declared = 0
    try:
        prof = dy.fetch_profile(sec) or {}
        declared = int(prof.get('aweme_count') or 0)
    except Exception as exc:
        log.debug('诊断：取资料页失败 %s', exc)
    raw = []
    pages = 0
    has_more = True
    cursor = '0'
    cap = max(1, int(max_pages or 1))
    while pages < cap:
        items, more, nxt = dy.fetch_posts_page(sec, count=18, cursor=cursor)
        pages += 1
        raw.extend(items or [])
        has_more = bool(more)
        if not has_more or not nxt or nxt == cursor:
            break
        cursor = nxt
    uniq = {}
    for it in raw:
        aid = str(it.get('aweme_id') or '')
        if aid:
            uniq[aid] = it
    items = list(uniq.values())
    by_year, by_kind = {}, {}
    live_n = 0
    for it in items:
        ts = int(it.get('create_time') or 0)
        year = time.strftime('%Y', time.localtime(ts)) if ts else '?'
        by_year[year] = by_year.get(year, 0) + 1
        kind = post_kind(it)
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if int(it.get('media_type') or 0) == 42:
            live_n += 1
    times = sorted(((int(it.get('create_time') or 0), str(it.get('aweme_id'))) for it in items))
    return {'declared': declared, 'fetched': len(items), 'raw': len(raw), 'pages': pages, 'has_more': has_more, 'tops': sum((1 for it in items if it.get('is_top'))), 'oldest': times[0] if times else (0, ''), 'newest': times[-1] if times else (0, ''), 'by_year': by_year, 'by_kind': by_kind, 'live': live_n}

def prune_seen(state, keep=800):
    seen = state.get('seen') or {}
    if len(seen) <= keep:
        return 0
    def stamp(kv):
        return (kv[1] or {}).get('create_time') or 0

    drop = sorted(seen.items(), key=stamp)[:len(seen) - keep]
    for key, _ in drop:
        seen.pop(key, None)
    return len(drop)

def find_local_files(batch, cfg, dest=''):
    """新作品这批里，哪些其实早在账号文件夹/下载目录里（核账可能已经提前补下了）。"""
    dirs = [d for d in (dest, history_account_dir(cfg, {
        'nickname': cfg.get('nickname') or '',
        'unique_id': cfg.get('unique_id') or ''}),
        os.path.join(douk_volume_dir(cfg), 'Download')) if d]
    out = []
    for it, _kind in batch:
        kind = {'视频': 'video', '图集': 'image', '实况': 'live'}.get(post_kind(it), 'video')
        out.extend(douk_dl.match_name_files(it, kind, dirs))
    return out


def check_posts(cfg, dy, state, first_run, first_run_download=None, report=None):
    sec = cfg['sec_user_id']
    platform = platform_of(getattr(dy, 'platform', 'douyin'))
    repair_seen(dict(cfg, platform=platform), state)
    last_ts = int(state.get('last_poll_ts') or 0)
    if state.get('retry'):
        last_ts = 0
    # 下载记录里「确实在盘上」的作品也当作已见：客户端状态丢了/换过机器时，
    # 不会再把这些老作品当成新作品重下一遍。
    known = set()
    try:
        _root = data_root(cfg)
        _pfx = f'{platform!s}:'
        for _key, _info in (douk_dl.load_records(_root) or {}).items():
            if not str(_key).startswith(_pfx):
                continue
            _file = str((_info or {}).get('file') or '')
            if _file and os.path.isfile(_file if os.path.isabs(_file) else os.path.join(_root, _file)):
                known.add(str(_key)[len(_pfx):])
    except Exception as exc:  # noqa: BLE001
        log.debug('读取下载记录失败，本轮只按已见列表判断：%s', exc)
    items = fetch_new_items(dy, sec, state['seen'], count=18, max_pages=int(cfg.get('poll_pages') or 3), last_ts=last_ts, known=known)
    state['last_poll_ts'] = int(time.time())
    new_items = []
    for item in items:
        aid = str(item.get('aweme_id') or '')
        if not aid or aid in state['seen']:
            continue
        new_items.append(item)
    new_items.sort(key=(lambda x: x.get('create_time') or 0))
    def remember(item, kind, skip=False):
        state['seen'][str(item['aweme_id'])] = {'type': kind, 'create_time': item.get('create_time'), 'desc': (item.get('desc') or '')[:60], 'skip': bool(skip)}

    def brief(item):
        return (item.get('desc') or '').replace('\n', ' ')[:40]

    # 新账号首次检查只登记已有作品，不弹窗询问、也不自动下载历史作品；
    # 想下载历史作品请用界面上的「下载历史视频」。
    download_existing = False
    if first_run:
        if not download_existing:
            for item in new_items:
                kind = post_kind(item)
                remember(item, kind, skip=True)
                log.info('首次运行，已记录历史作品：%s [%s] %s', item['aweme_id'], kind, brief(item))
            return new_items
    pending = []
    for item in new_items:
        kind = post_kind(item)
        if kind == '视频' and not cfg['download_video']:
            remember(item, kind, skip=True)
            log.info('跳过视频（未启用视频下载）：%s %s', item['aweme_id'], brief(item))
            continue
        if kind == '图集' and not cfg['download_images']:
            remember(item, kind, skip=True)
            log.info('跳过图集（未启用图集下载）：%s %s', item['aweme_id'], brief(item))
            continue
        pending.append((item, kind))
    if not pending:
        return new_items
    limit = max(1, int(cfg['max_posts_per_poll']))
    batch, deferred = pending[:limit], pending[limit:]
    if deferred:
        log.warning('本轮新作品共 %d 个，按单次上限 %d 个处理前 %d 个，剩余 %d 个下一轮继续', len(pending), limit, len(batch), len(deferred))
    for item, kind in batch:
        notify(cfg, '发现新作品', f'[{kind!s}] {item['aweme_id']!s} {brief(item)!s}')
    dest = (cfg.get('save_dir') or '').strip()
    alt = []
    if not dest:
        alt.append(history_account_dir(cfg, {
            'nickname': cfg.get('nickname') or '',
            'unique_id': cfg.get('unique_id') or ''}))
    alt.append(os.path.join(douk_volume_dir(cfg), 'Download'))
    log.info('开始下载 %d 个新作品（内置下载器）...', len(batch))
    t0 = time.time()
    stats, files, text = douk_download(cfg, [post_url(item) for item, _ in batch],
                                       platform=platform, dest=dest, alt_dirs=alt)
    summary = '，'.join(('%s %d 个' % (k, v) for k, v in stats.items())) or '未解析到统计信息'
    log.info('下载结束：%s；新增文件 %d 个', summary, len(files))
    for f in files[:20]:
        log.info('  -> %s', f)
    if not files:
        skipped = sum(int(v or 0) for k, v in stats.items() if str(k).startswith('跳过'))
        if not skipped:
            notify(cfg, '下载失败', '这些作品没有生成文件，请查看 monitor.log 里的下载原因')
            tail = '\n'.join(text.strip().splitlines()[-25:])
            log.warning('下载器输出（末尾 25 行）：\n%s', tail)
        else:
            # 文件其实已经在账号文件夹里（多半是每轮核账提前补下了）：不重复下载，
            # 但要把它们放进「本次下载」面板，用户才知道这个新作品已经拿到了。
            shown = find_local_files(batch, cfg, dest)
            if shown:
                log.info('新作品 %d 个的文件已经在账号文件夹里（核账先补下了），只补记录、不重复下载',
                         len(batch))
                _fill_report(report, shown, [it for it, _k in batch], cfg)
                report['already'] = True
    if not (report or {}).get('already'):
        _fill_report(report, files, [item for item, _ in batch], cfg)
    records = {}
    try:
        records = douk_dl.load_records(data_root(cfg)) or {}
    except Exception as exc:
        log.warning('读取下载记录失败，本轮按「未下到文件即重试」处理：%s', exc)
    retry = state.setdefault('retry', {})
    ok_count = 0
    for item, kind in batch:
        aid = str(item.get('aweme_id') or '')
        key = f'{platform!s}:{aid!s}'
        done = bool(records.get(key))
        if done:
            ok_count += 1
            retry.pop(aid, None)
            remember(item, kind)
            continue
        info = retry.get(aid) or {'n': 0}
        info['n'] = int(info.get('n') or 0) + 1
        info['ts'] = int(time.time())
        info['desc'] = (item.get('desc') or '')[:40]
        retry[aid] = info
        if info['n'] >= 3:
            remember(item, kind, skip=True)
            retry.pop(aid, None)
            log.error('作品 %s 连续 %d 次没下载成功，先标记为已处理（想重下请清除下载记录）：%s', aid, info['n'], (item.get('desc') or '')[:40])
            continue
        log.warning('作品 %s 这轮没下到文件（第 %d 次），下一轮继续重试：%s', aid, info['n'], (item.get('desc') or '')[:40])
        continue
    if len(batch) > ok_count:
        log.info('本轮新作品 %d 个：下到 %d 个，%d 个留到下一轮重试', len(batch), ok_count, len(batch) - ok_count)
    if deferred:
        log.info('另有 %d 个新作品按单次上限顺延到下一轮（没有登记，不会丢）', len(deferred))
    dropped = prune_seen(state)
    if dropped:
        log.info('已清理 %d 条过旧的「已见作品」记录', dropped)
    return new_items

def check_live(cfg, dy, state, profile, report=None):
    import datetime
    sec = cfg['sec_user_id']
    platform = platform_of(getattr(dy, 'platform', 'douyin'))
    live_status = profile.get('live_status')
    room_id = str(profile.get('room_id') or '')
    nickname = profile.get('nickname') or ''
    if live_status != 1:
        if state.get('live_active'):
            log.info('直播已结束：%s', nickname)
            state['live_active'] = False
            state['last_live_room'] = ''
        return
    web_rid = None
    if platform == 'tiktok':
        web_rid = room_id
    else:
        short_id = str(profile.get('short_id') or '')
        unique_id = str(profile.get('unique_id') or '')
        if short_id and short_id != '0':
            web_rid = short_id
        elif unique_id:
            web_rid = unique_id
    if not web_rid:
        log.warning('账号正在直播但无法确定房间标识（%s），跳过', platform_label(platform))
        return
    already = bool(state.get('live_active')) and state.get('last_live_room') == room_id
    if not already:
        log.info('检测到开播：%s（room_id=%s, web_rid=%s），正在获取拉流地址 ...', nickname, room_id, web_rid)
    try:
        info = dy.fetch_live(web_rid)
    except Exception as e:
        log.error('获取直播信息失败：%s', e)
    if not info:
        log.warning('直播间未开播或已结束（web_rid=%s）', web_rid)
        return
    state['live_active'] = True
    state['last_live_room'] = info.get('room_id') or room_id
    flv = info['flv'] or {}
    hls = info['hls'] or {}
    want = (cfg.get('live_quality') or 'FULL_HD1').strip() or 'FULL_HD1'
    available = [q for q, _ in LIVE_QUALITY_LABELS if q in flv or q in hls]
    available += [q for q in list(flv) + list(hls) if q not in available]
    # 「原画」在部分直播间会以 ORIGIN 单独给出（码率更高），有就优先用源画质
    if want == 'FULL_HD1' and 'ORIGIN' in available:
        want = 'ORIGIN'
    if want not in flv and want not in hls:
        want = available[0] if available else want
    if not already:
        log.info('【%s】接口返回的画质档位：%s（选用 %s）',
                 nickname, '、'.join(str(x) for x in available) or '未知', want)
    flv_url = flv.get(want, '')
    hls_url = hls.get(want, '')
    if not hls_url:
        if want == 'FULL_HD1':
            hls_url = info.get('hls_default') or ''
    label = LIVE_QUALITY_NAMES.get(want, want)
    if not already:
        log.info('检测到开播：%s，画质 %s（%s），开始录制…', nickname, want, label)
        notify(cfg, '开始直播', f'{nickname!s} 开播：{info['title']!s}')
    else:
        log.debug('直播仍在继续：%s（%s）', nickname, label)
    if report is not None:
        report['live'] = {'nickname': nickname, 'title': info.get('title') or '', 'room': info.get('room_id') or room_id, 'quality': want, 'quality_label': label, 'txt': '', 'flv': flv_url, 'hls': hls_url, 'platform': platform, 'sec': sec}

def run(cfg, once=False):
    setup_logging(cfg['log_file'])
    log.info('======================================================================')
    log.info('抖音监控启动 | 数据目录: %s', data_root(cfg))
    dy = DouYin('', timeout=cfg['request_timeout'], data_dir=str(cfg.get('data_dir') or ''))
    state = load_state(cfg['state_file'])
    spec = cfg.get('sec_user_id') or cfg.get('target') or ''
    if not spec:
        raise RuntimeError('请先在 config.json 中填写 sec_user_id（目标账号的 sec_uid、主页链接或抖音号）')
    sec = dy.resolve_sec_uid(spec)
    cfg['sec_user_id'] = sec
    log.info('监控账号 sec_user_id = %s', sec)
    first_run = not state.get('first_run_done')
    if first_run:
        log.info('首次运行：只记录现有作品，不下载历史内容（如需下载历史作品，请点界面上的「下载历史视频」）')
    while True:
        t_start = time.time()
        try:
            profile = dy.fetch_profile(sec)
            log.info('账号：%s（粉丝 %s / 作品 %s）', profile.get('nickname'), profile.get('follower_count'), profile.get('aweme_count'))
            check_posts(cfg, dy, state, first_run)
            if cfg['live_check']:
                check_live(cfg, dy, state, profile)
            state['first_run_done'] = True
            save_state(cfg['state_file'], state)
            if first_run:
                first_run = False
        except Exception as e:
            log.error('本轮检查失败：%s', e)
            log.debug('%s', traceback.format_exc())
            if 'Cookie' in str(e) or '失效' in str(e):
                notify(cfg, 'Cookie 可能失效', '请点「设置 → 扫码登录」重新登录抖音')
        if once:
            return
        cost = time.time() - t_start
        wait = max(5, cfg['poll_interval'] - cost)
        log.info('本轮耗时 %.1f 秒，%d 秒后再次检查', cost, wait)
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            log.info('收到中断，退出')

def main():
    import argparse
    ap = argparse.ArgumentParser(description='抖音账号更新监控 + 内置下载器')
    ap.add_argument('--config', default=os.path.join(BASE_DIR, 'config.json'))
    ap.add_argument('--once', action='store_true', help='只检查一轮后退出')
    ap.add_argument('--resolve', metavar='抖音号', help='查询目标账号的 sec_user_id 后退出')
    args = ap.parse_args()
    cfg = load_config(args.config)
    setup_logging(cfg['log_file'])
    if args.resolve:
        dy = DouYin('', timeout=cfg['request_timeout'], data_dir=str(cfg.get('data_dir') or ''))
        sec = dy.resolve_sec_uid(args.resolve)
        p = dy.fetch_profile(sec)
        print('sec_user_id:', sec)
        print('昵称:', p.get('nickname'), '| 抖音号:', p.get('unique_id'), '| short_id:', p.get('short_id'))
        return
    try:
        run(cfg, once=args.once)
        return
    except KeyboardInterrupt:
        log.info('已停止')
    except Exception as e:
        log.error('启动失败：%s', e)
        log.debug('%s', traceback.format_exc())
        sys.exit(1)

if __name__ == '__main__':
    main()

def douk_db_path(cfg):
    return os.path.join(data_root(cfg), 'Volume', 'DouK-Downloader.db')

def _decode_console(raw):
    for enc in ('utf-8', 'gbk'):
        try:
            pass
        except UnicodeDecodeError:
            continue
        return ANSI_RE.sub('', raw.decode(enc))
    return ANSI_RE.sub('', raw.decode('utf-8', 'replace'))

def _finish_download(cfg, files, stats, text, items, report):
    dest = (cfg.get('save_dir') or '').strip()
    skipped = sum((v for k, v in stats.items() if str(k).startswith('跳过')))
    if not files and skipped:
        # 之前下过、这次直接跳过：不刷日志、不进「本次下载」面板
        log.debug('这次 %d 个作品之前下过，直接跳过（不重复下载）', skipped)
        _fill_report(report, files, items, cfg)
        return files
    summary = '，'.join(('%s %d 个' % (k, v) for k, v in stats.items()
                        if not str(k).startswith('跳过') and v)) or '没有新文件'
    log.info('下载结束：%s；新增文件 %d 个', summary, len(files))
    if dest and files:
        log.info('已保存到账号指定目录：%s（%d 个文件）', os.path.abspath(dest), len(files))
    for f in files[:20]:
        log.info('  -> %s', f)
    if not files:
        notify(cfg, '下载失败', '这次没有生成文件，原因见 monitor.log')
        tail = '\n'.join(text.strip().splitlines()[-25:])
        log.warning('下载器输出（末尾 25 行）：\n%s', tail)
    _fill_report(report, files, items, cfg)
    return files

def post_id_from(url, html=''):
    aid = douk_dl.aweme_id(url)
    if aid:
        return aid
    m = POST_ID_RE.search((url or '') + ' ' + (html or ''))
    if m:
        return next((g for g in m.groups() if g), '')
    return ''

def download_posts(cfg, items, dy=None, platform=DEFAULT_PLATFORM, dest='', timeout=None, progress=None,
                   alt_dirs=()):
    platform = platform_of(platform)
    root = data_root(cfg)
    records = douk_dl.load_records(root)
    if not dest:
        dest = os.path.join(root, 'Volume', 'Download')
    cookie = getattr(dy, 'cookie_str', '') if dy is not None else ''
    # 列表接口偶尔不带清晰度列表（video.bit_rate），这时 play_addr 给的是默认档（常见 540p）。
    # 下载前补一次详情，确保能选到最高画质；只有缺清晰度列表的条目才多这一次请求。
    if dy is not None:
        fixed = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            v = it.get('video') or {}
            if not v or v.get('bit_rate') or not it.get('aweme_id'):
                continue
            try:
                detail = dy.fetch_detail(str(it['aweme_id']))
            except Exception:  # noqa: BLE001
                continue
            dv = (detail or {}).get('video') or {}
            if dv.get('bit_rate'):
                it['video'] = dv
                fixed += 1
        if fixed:
            log.info('下载前补齐了 %d 个作品的清晰度列表（用于挑最高画质）', fixed)
    stats, files = douk_dl.download_items(cfg, items, platform, dest, cookie_str=cookie, timeout=timeout or 60, records=records, data_root=root, progress=progress, alt_dirs=alt_dirs)
    douk_dl.save_records(root, records)
    return stats, files, douk_dl.stats_text(stats)

def download_known_items(cfg, items, dy=None, platform=DEFAULT_PLATFORM, dest='', timeout=None,
                         alt_dirs=()):
    """作品列表里已经有完整媒体地址时直接下载，不再逐个请求详情接口（省一次风控风险）。"""
    platform = platform_of(platform)
    stats, files, text = download_posts(cfg, items, dy, platform, dest=dest, timeout=timeout,
                                        alt_dirs=alt_dirs)
    return stats, files, text


def data_root(cfg):
    import douk_rt
    return douk_rt.data_dir(str((cfg or {}).get('data_dir') or ''))

def history_dir(cfg):
    custom = str((cfg or {}).get('history_save_dir') or '').strip()
    if custom:
        return os.path.abspath(os.path.expanduser(custom))
    return os.path.join(douk_volume_dir(cfg), '历史下载')

def safe_dir_name(name, fallback='未命名'):
    s = re.sub('[\\\\/:*?"<>|\\r\\n\\t]+', '_', str(name or '')).strip(' .')
    return s[:80] or fallback

def history_account_dir(cfg, acc):
    acc = acc or {}
    nick = str(acc.get('nickname') or acc.get('target') or '未命名').strip()
    uid = str(acc.get('unique_id') or '').strip()
    name = f'UID{uid!s}_{nick!s}_发布作品' if uid else '%s_发布作品' % nick
    return os.path.join(history_dir(cfg), safe_dir_name(name))

def _nick_from_filename(name):
    m = re.search('-(?:视频|图集|实况)-([^-]+)', str(name or ''))
    if not m:
        return ''
    nick = re.sub('(_\\d+)?\\.[A-Za-z0-9]{1,5}$', '', m.group(1))
    return nick.strip()

def migrate_history_downloads(cfg, accounts=None):
    import shutil
    import douk_dl
    root = data_root(cfg)
    base = history_dir(cfg)
    res = {'moved': 0, 'skipped': 0, 'dupes': 0, 'records': 0, 'to': base}
    os.makedirs(base, exist_ok=True)
    nick_map = {}
    for a in accounts or []:
        nick = str((a or {}).get('nickname') or '').strip()
        if nick:
            nick_map[nick] = a
    def target_for(name):
        nick = _nick_from_filename(name)
        if nick and nick in nick_map:
            return history_account_dir(cfg, nick_map[nick])
        if nick:
            return os.path.join(base, safe_dir_name('%s_发布作品' % nick))
        return os.path.join(base, '未分类_发布作品')

    sources = []
    old_dl = os.path.join(root, 'Volume', 'Download')
    if os.path.isdir(old_dl):
        for cur, _dirs, names in os.walk(old_dl):
            for name in names:
                sources.append(os.path.join(cur, name))
    for cur, _dirs, names in os.walk(base):
        for name in names:
            sources.append(os.path.join(cur, name))
    # DouK 老版本按账号存到 Volume\<UID>_<昵称>_发布作品，和现在的 Volume\历史下载\<同名> 是两套；
    # 一并扫进来合并，否则用户看到两个同名文件夹内容不一致。
    vol = os.path.join(root, 'Volume')
    if os.path.isdir(vol):
        for name in sorted(os.listdir(vol)):
            one = os.path.join(vol, name)
            if (not os.path.isdir(one) or name in ('Download', '历史下载', '日常', '_旧文件备份')
                    or not name.endswith('_发布作品')):
                continue
            for cur, _dirs, names in os.walk(one):
                for fname in names:
                    sources.append(os.path.join(cur, fname))
    move_map = {}
    for old in sources:
        name = os.path.basename(old)
        target_dir = target_for(name)
        os.makedirs(target_dir, exist_ok=True)
        new = os.path.join(target_dir, name)
        if os.path.exists(new) and os.path.abspath(new) != os.path.abspath(old):
            try:
                if os.path.getsize(new) == os.path.getsize(old):
                    os.remove(old)          # 同名同大小：确定是同一份，直接去重
                    res['dupes'] += 1
                    continue
            except OSError:
                pass
            stem, ext = os.path.splitext(name)
            idx = 2
            while os.path.exists(new):
                new = os.path.join(target_dir, '%s (%d)%s' % (stem, idx, ext))
                idx += 1
        if os.path.abspath(new) == os.path.abspath(old):
            continue
        try:
            shutil.move(old, new)
            res['moved'] += 1
            move_map[os.path.abspath(old)] = new
        except OSError as exc:
            res['skipped'] += 1
            log.warning('整理历史作品失败 %s：%s', name, exc)
        continue
    records = douk_dl.load_records(root)
    changed = 0
    for _key, info in records.items():
        if not isinstance(info, dict):
            continue
        shown = str(info.get('file') or info.get('path') or '')
        if not shown:
            continue
        full = shown if os.path.isabs(shown) else os.path.join(root, shown)
        new = move_map.get(os.path.abspath(full))
        if new:
            info['file'] = os.path.relpath(new, root)
            changed += 1
    if changed:
        douk_dl.save_records(root, records)
    res['records'] = changed
    for root_dir in old_dl, base, os.path.join(root, 'Volume'):
        if os.path.isdir(root_dir):
            for cur, _dirs, _names in os.walk(root_dir, topdown=False):
                try:
                    if (cur != root_dir and not os.listdir(cur)
                            and os.path.basename(cur) not in ('Download', '历史下载', '日常', '_旧文件备份')):
                        os.rmdir(cur)
                except OSError:
                    continue
    if res['moved'] or res['skipped']:
        log.info('历史作品已整理到 %s：移动 %d 个，失败 %d 个，更新记录 %d 条', base, res['moved'], res['skipped'], res['records'])
    return res

def _client_for(cfg, platform=DEFAULT_PLATFORM, dy=None, timeout=None):
    if dy is not None:
        return dy
    return make_client(cfg, platform, timeout=timeout or cfg.get('request_timeout', 20))

def expand_post_url(url, timeout=15):
    if not re.search('v\\.douyin\\.com|(?:vm|vt)\\.tiktok\\.com', url or '', re.I):
        return url, ''
    from urllib.request import Request, urlopen
    req = Request(url, headers={'User-Agent': MOBILE_UA, 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8', 'Accept-Language': 'zh-CN,zh;q=0.9'})
    try:
        with urlopen(req, timeout=timeout) as resp:
            final = resp.geturl() or ''
            raw = resp.read(307200) or b''
    except Exception as exc:
        log.warning('展开短链失败：%s（%s）', url, exc)
        return ('', '')
    return final, raw.decode('utf-8', 'ignore')

# WARNING: Decompyle incomplete
