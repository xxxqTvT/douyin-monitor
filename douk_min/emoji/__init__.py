"""极简 emoji 替身 —— 只提供上游模块用来清理文件名的那几个接口。

来源说明：
    本项目自行编写的依赖替身（正则删除表情），不含 TikTokDownloader / DouK 的实现。
    存在原因：TikTokDownloader 的 cleaner 依赖 emoji 包，我们不带 4 MB 表情表。
"""

import re

__version__ = "2.14.0"

EMOJI_DATA = {}

# 覆盖常见表情区块：符号、Emoji、杂项符号、交通、补充符号等
_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF"
    "\U0000FE00-\U0000FE0F"
    "\U0001F000-\U0001F0FF"
    "\U00002000-\U0000206F"
    "]+"
)


def replace_emoji(text, replace="", *args, **kwargs):
    """把表情替换掉。returns_tuple=True 时返回 (文本, 命中的表情列表)。"""
    found = _PATTERN.findall(text or "")
    new = _PATTERN.sub(replace, text or "")
    if kwargs.get("returns_tuple"):
        return new, found
    return new


def emojize(text, *args, **kwargs):
    return text


def demojize(text, *args, **kwargs):
    return replace_emoji(text, "", *args, **kwargs)[0] if kwargs.get("returns_tuple") else replace_emoji(text, "")


def is_emoji(char):
    return bool(_PATTERN.match(char or ""))


def get_emoji_regexp():
    return _PATTERN


def analyze(text, *args, **kwargs):
    return [(m.group(0), 0, m.end()) for m in _PATTERN.finditer(text or "")]


def __getattr__(name):
    """其它未知接口一律当作恒等函数，避免因为少一个名字就崩。"""
    def _noop(*args, **kwargs):
        if args and isinstance(args[0], str):
            return args[0]
        return None
    return _noop