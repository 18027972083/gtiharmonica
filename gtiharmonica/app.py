"""应用程序入口。

职责：
  * 决定配置目录与曲库目录的位置（打包后也要能正确落盘）
  * 安装全局异常处理，保证崩溃时按键一定被释放
  * 应用主题、创建主窗口
"""
from __future__ import annotations

import os
import shutil
import sys
import traceback
from typing import Optional

from .library_state import is_deleted

# 数据目录名刻意保持 GTIHarmonica 不跟显示名（大肥鲸洲琴工具包）一起改：
# %APPDATA%\GTIHarmonica 里是用户曲库、配置和日志，改名等于让用户
# 「丢」掉整个曲库。
APP_NAME = 'GTIHarmonica'


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包后的环境里。"""
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def app_root() -> str:
    """程序所在目录（打包后是 exe 所在目录，开发时是项目根目录）。"""
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def user_data_dir() -> str:
    """可写的用户数据目录（配置、日志、用户曲库）。"""
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, '.write_test')
        with open(probe, 'w') as fh:
            fh.write('x')
        os.remove(probe)
        return True
    except OSError:
        return False


#: 曲库认这些后缀（迁移、统计、校验都用同一份定义）
LIBRARY_EXTS = ('.json', '.mid', '.midi')


def _adopt_portable_library(src: str, dst: str) -> int:
    """把旧版「程序旁边的 songs」曲库合并进用户数据目录。

    1.1 及更早版本的曲库放在 exe 旁边，而那个位置正好落在构建产物的
    dist/ 里面，重新构建会被整个删掉（用户自己转的谱就这样丢过一次）。
    这里做一次幂等合并：只补目标目录里没有的曲子，绝不覆盖、绝不删除
    源文件。合并结果返回补进去的文件数。

    用户删过的曲目不补 —— 源曲库里那份还在，不看删除名单的话每次启动
    都会把删掉的曲子拷回来（用户报过的「删除后重启又出现」）。
    """
    if not os.path.isdir(src):
        return 0
    try:
        if os.path.samefile(src, dst):
            return 0
    except OSError:
        pass
    try:
        names = sorted(os.listdir(src))
    except OSError:
        return 0

    moved = 0
    skipped = 0
    for name in names:
        if not name.lower().endswith(LIBRARY_EXTS):
            continue
        if is_deleted(dst, name):
            skipped += 1
            continue
        target = os.path.join(dst, name)
        if os.path.exists(target):
            continue
        try:
            shutil.copy2(os.path.join(src, name), target)
            moved += 1
        except OSError:
            pass
    if skipped:
        try:
            with open(log_path(), 'a', encoding='utf8') as fh:
                fh.write('[library] 跳过 %d 首已从曲库删除的曲目（不补齐）\n'
                         % skipped)
        except OSError:
            pass
    return moved


def resolve_library_dir() -> str:
    """曲库目录。

    打包后使用 %APPDATA%\\GTIHarmonica\\songs —— 放在用户数据目录里，
    重装程序、解压覆盖、重新构建都不会碰到它。

    这里刻意不再默认使用程序旁边的 songs：那个位置在构建产物的
    dist/ 内部，PyInstaller 每次重建会先整个删掉该目录，用户导入的
    曲目因此丢过。旧版曲库会在首次启动时自动合并过来（不删原件）。

    想继续便携（U 盘 / 免安装）的话，在 exe 旁边放一个空的
    portable.txt，曲库就跟着程序走。

    开发环境（未打包）仍用项目根目录的 songs，方便直接改曲谱。
    """
    portable = os.path.join(app_root(), 'songs')
    user = os.path.join(user_data_dir(), 'songs')

    if not is_frozen():
        os.makedirs(portable, exist_ok=True)
        return portable

    # 显式便携模式：只有用户主动放了 portable.txt 才跟着程序走
    if os.path.exists(os.path.join(app_root(), 'portable.txt')) \
            and _writable(portable):
        return portable

    os.makedirs(user, exist_ok=True)
    if _writable(user):
        try:
            moved = _adopt_portable_library(portable, user)
            if moved:
                with open(log_path(), 'a', encoding='utf8') as fh:
                    fh.write('[library] 已从程序目录曲库合并 %d 首到 %s\n'
                             % (moved, user))
        except Exception:
            pass  # 迁移失败绝不能挡住启动
        return user

    # 用户数据目录不可写（极罕见）：退回程序旁边的 songs
    if _writable(portable):
        return portable
    return user


def config_path() -> str:
    return os.path.join(user_data_dir(), 'config.json')


def log_path() -> str:
    return os.path.join(user_data_dir(), 'gtiharmonica.log')


# ---------------------------------------------------------------------------
# 异常处理
# ---------------------------------------------------------------------------

def install_excepthook() -> None:
    """把未捕获异常写进日志，并尽力释放可能按住的按键。"""
    def hook(exc_type, exc_value, exc_tb):
        text = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            with open(log_path(), 'a', encoding='utf8') as fh:
                import datetime
                fh.write('\n===== %s =====\n%s' % (datetime.datetime.now(), text))
        except Exception:
            pass
        # 兜底：确保没有按键卡在按下状态
        try:
            from .backend import SendInputBackend
            api = SendInputBackend.__new__(SendInputBackend)
            from .backend import load_user32
            from .instrument import MOUSE_FLAGS
            api.api = load_user32()
            for key in list(MOUSE_FLAGS) + list('zxcvbnm,./;[]-='):
                try:
                    api._emit(key, False)
                except Exception:
                    pass
        except Exception:
            pass

        print(text, file=sys.stderr)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None, '程序异常',
                    '发生了未预期的错误，已写入日志：\n%s\n\n%s'
                    % (log_path(), text.strip().splitlines()[-1] if text else ''))
        except Exception:
            pass

    sys.excepthook = hook


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def bootstrap(argv: Optional[list] = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    install_excepthook()

    # 打包后 multiprocessing 需要，避免意外启动子进程时递归
    try:
        import multiprocessing
        multiprocessing.freeze_support()
    except Exception:
        pass

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
    except ImportError:
        sys.stderr.write(
            '缺少 PySide6。请先安装：\n    pip install PySide6-Essentials\n')
        return 3

    from .gui.theme import apply_theme, make_icon
    from .gui.main import MainWindow
    from .config import Config

    # 任务栏身份：刻意**不**设显式 AUMID。
    # 设了 AUMID 而快捷方式里没有写入匹配值时，Win11 任务栏不再回退到
    # 窗口/可执行文件图标，直接显示系统默认占位图（实测过的坑）。
    # 不设 AUMID 时任务栏按窗口图标取图 —— 正是我们要的效果。

    app = QApplication(argv)
    app.setApplicationName('大肥鲸洲琴工具包')
    app.setOrganizationName(APP_NAME)
    # Windows 上 Qt 默认走 windowsvista 原生风格，它会忽略 QSS 对下拉框、
    # 滑块这类控件的背景设置（只认系统绘制），结果就是深色界面里冒出
    # 几个灰底下拉框。Fusion 完整支持样式表，观感才统一。
    app.setStyle('Fusion')
    from PySide6.QtCore import QSettings
    _settings = QSettings('GTIHarmonica', 'app')
    apply_theme(app, _settings.value('theme_mode', 'light'))
    app.setWindowIcon(make_icon(64))

    # 卡密激活：本机未激活时先弹激活窗（模态），取消就退出。
    # 已激活的机器不走这里，打开即用。
    from .activation import is_activated
    if not is_activated():
        from .gui.dialogs import ActivationDialog
        if not ActivationDialog().exec():
            return 0

    cfg_path = config_path()
    library = resolve_library_dir()

    # 首次运行：放入示例曲目，保证打开就有东西可试
    try:
        from .demo import ensure_demo_songs
        ensure_demo_songs(library)
    except Exception:
        pass

    config = Config.load(cfg_path) if os.path.exists(cfg_path) else Config()
    window = MainWindow(config, library)
    window.set_config_path(cfg_path)
    window.setWindowIcon(make_icon(64))

    # 双击打开的曲谱文件 / 提权重启时携带的曲目：入库并直接载入
    for arg in argv[1:]:
        if os.path.isfile(arg) and arg.lower().endswith(('.mid', '.midi', '.json')):
            added = window.library.import_files([arg])
            window.library.refresh()
            target = added[0] if added else arg
            window.library.select_path(target)
            window.on_library_selected(target)
            break

    window.show()
    return app.exec()


#: CLI 子命令名。打包版是 GUI 子系统的 exe，双击只会启动界面；
#: 但带着这些参数运行时应当走命令行，方便批量转录等操作。
_CLI_COMMANDS = frozenset({
    'play', 'preview', 'analyze', 'tracks', 'export',
    'import-legacy', 'selftest', 'calibrate', 'windows', 'init',
    'transcribe',
})


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] in _CLI_COMMANDS:
        # 命令行同样要激活：未激活时没有图形界面弹窗，直接给文字提示。
        from .activation import is_activated
        if not is_activated():
            sys.stderr.write(
                '本机尚未激活。请先打开图形界面（双击程序），'
                '在激活窗口输入卡密后再使用命令行。\n')
            return 4
        from .cli import main as cli_main
        return cli_main()
    return bootstrap()


if __name__ == '__main__':
    sys.exit(main())
