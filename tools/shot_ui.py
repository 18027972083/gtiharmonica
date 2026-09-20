"""抓取当前主界面与转录对话框的整体截图，用于比对 UI 风格。

用法：python tools/shot_ui.py
产出：build/shots/ui_main.png / ui_sidebar.png / ui_dialog.png
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEventLoop, QTimer      # noqa: E402
from PySide6.QtWidgets import QApplication          # noqa: E402

from gtiharmonica.config import Config              # noqa: E402
from gtiharmonica.gui.theme import QSS, make_icon   # noqa: E402

SHOTS = os.path.join(ROOT, 'build', 'shots')
os.makedirs(SHOTS, exist_ok=True)


def pump(ms=200):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(QSS)

    from gtiharmonica.gui.dialogs import TranscribeDialog
    from gtiharmonica.gui.main import MainWindow

    lib = os.path.join(ROOT, 'songs')
    win = MainWindow(Config(), lib)
    win.setWindowIcon(make_icon(64))
    win.resize(1360, 880)
    win.show()
    pump(700)

    for name, widget in (('ui_main.png', win),
                         ('ui_sidebar.png', win.library)):
        path = os.path.join(SHOTS, name)
        widget.grab().save(path)
        print('shot -> %s' % path)

    dlg = TranscribeDialog(lib, None)
    dlg.show()
    pump(400)
    path = os.path.join(SHOTS, 'ui_dialog.png')
    dlg.grab().save(path)
    print('shot -> %s' % path)

    # 顺便打印实际生效的调色板，确认 QSS 真的应用上了
    from PySide6.QtGui import QPalette
    pal = win.palette()
    print('window 色: %s' % pal.color(QPalette.Window).name())
    print('text   色: %s' % pal.color(QPalette.WindowText).name())
    print('styleSheet 长度: %d' % len(app.styleSheet()))

    dlg.close()
    win.close()
    pump(200)
    return 0


if __name__ == '__main__':
    sys.exit(main())
