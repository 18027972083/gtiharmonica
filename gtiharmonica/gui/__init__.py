"""图形界面层（PySide6）。

故意不在本文件导入子模块，避免 PySide6 缺失时连 `import gtiharmonica` 都失败。
命令行入口可以在没有 Qt 的环境下继续工作。
"""
__all__ = ['MainWindow', 'run_app']


def __getattr__(name):
    """按需导入，保持包顶层轻量。"""
    if name in ('MainWindow', 'run_app'):
        from . import main as _main
        return getattr(_main, name)
    raise AttributeError(name)
