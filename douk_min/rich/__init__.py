"""极简 rich 替身（只为满足上游模块的 import）。

来源说明：
    本项目自行编写的空实现桩，不含 rich / TikTokDownloader / DouK 的实现。
"""

import sys
import types


class _Anything:
    """万能占位对象：可调用、可当基类、可取属性。"""

    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return _Anything()

    def __getattr__(self, name):
        return _Anything()

    def __iter__(self):
        return iter(())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __bool__(self):
        return False


class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return type(name, (_Anything,), {})


class _Finder:
    def find_module(self, fullname, path=None):  # 兼容旧接口
        return None

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "rich" or fullname.startswith("rich."):
            import importlib.machinery
            return importlib.machinery.ModuleSpec(fullname, _Loader(), is_package=True)
        return None


class _Loader:
    def create_module(self, spec):
        mod = _StubModule(spec.name)
        mod.__path__ = []
        return mod

    def exec_module(self, module):
        pass


def _install():
    if not any(isinstance(f, _Finder) for f in sys.meta_path):
        sys.meta_path.insert(0, _Finder())


_install()