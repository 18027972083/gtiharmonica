"""采样参数卡里控件的实际渲染颜色，确认「灰底」到底是什么色。"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEventLoop, QTimer                 # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QSlider,  # noqa: E402
                               QSpinBox, QWidget)

from gtiharmonica.config import Config                        # noqa: E402
from gtiharmonica.gui.theme import QSS, dark_palette          # noqa: E402
from gtiharmonica.gui.main import MainWindow                  # noqa: E402


def pump(ms=800):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def sample(widget, label):
    if widget is None or not widget.isVisible():
        print('  %-22s 不可见' % label)
        return
    pm = widget.grab()
    img = pm.toImage()
    w, h = img.width(), img.height()
    if w < 4 or h < 4:
        print('  %-22s 尺寸过小 %dx%d' % (label, w, h))
        return
    # 取几个点：中心、左上角内侧、右下角内侧
    pts = [(w // 2, h // 2), (w // 2, 3), (3, h // 2), (w - 4, h // 2)]
    colors = [img.pixelColor(x, y).name() for x, y in pts]
    print('  %-22s %dx%d  中心=%s  上=%s  左=%s  右=%s'
          % (label, w, h, colors[0], colors[1], colors[2], colors[3]))


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setPalette(dark_palette())
    app.setStyleSheet(QSS)
    win = MainWindow(Config(), os.path.join(ROOT, 'songs'))
    win.resize(1360, 880)
    win.show()
    pump(900)

    print('--- 参数卡里的控件实际渲染色 ---')
    card = win.params
    combos = card.findChildren(QComboBox)
    spins = card.findChildren(QSpinBox)
    sliders = card.findChildren(QSlider)
    print('  QComboBox x%d / QSpinBox x%d / QSlider x%d'
          % (len(combos), len(spins), len(sliders)))
    for i, c in enumerate(combos[:4]):
        sample(c, 'combo[%d] %s' % (i, c.currentText()[:12]))
    for i, s in enumerate(spins[:2]):
        sample(s, 'spin[%d]' % i)
    for i, s in enumerate(sliders[:3]):
        sample(s, 'slider[%d]' % i)

    print('--- 按键行 ---')
    sample(getattr(win, 'keys', None), 'KeyStrip')
    print('--- 卡片底色 ---')
    sample(card, 'ParamCard')

    win.close()
    pump(150)
    return 0


if __name__ == '__main__':
    sys.exit(main())
