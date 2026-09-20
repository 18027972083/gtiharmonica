"""诊断：曲库条目的八分音符图标为什么没画出来。

读第一个条目的真实几何，算出图标应该落在哪，再去截图里采样那个位置，
用确定性的像素证据判断是「没画」还是「画在别处」。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEventLoop, QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402

from gtiharmonica.config import Config                 # noqa: E402
from gtiharmonica.gui.theme import build_qss                 # noqa: E402
from gtiharmonica.gui.main import MainWindow           # noqa: E402


def pump(ms=700):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_qss())
    win = MainWindow(Config(), os.path.join(ROOT, 'songs'))
    win.resize(1360, 880)
    win.show()
    pump(900)

    lst = win.library.list
    print('条目数 =', lst.count())
    if lst.count() == 0:
        print('曲库为空，无法诊断')
        return 1

    r = lst.visualItemRect(lst.item(0))
    print('visualItemRect = x=%d y=%d w=%d h=%d' % (r.x(), r.y(), r.width(), r.height()))
    print('viewport 尺寸 = %dx%d' % (lst.viewport().width(), lst.viewport().height()))

    rect = r.adjusted(2, 2, -2, -2)
    left = rect.left() + 11
    top = rect.top() + (rect.height() - 24) // 2
    print('计算出的图标位置 (viewport 坐标) = left=%d top=%d' % (left, top))

    off = lst.viewport().mapTo(win.library, r.topLeft())
    print('viewport→sidebar 偏移 = %d,%d' % (off.x(), off.y()))
    print('图标在 sidebar 内的位置 = %d,%d' % (off.x() + left, off.y() + top))

    pm = win.library.grab()
    img = pm.toImage()
    print('sidebar 截图尺寸 = %dx%d' % (img.width(), img.height()))

    # 全图找青绿色像素（图标颜色 #83dfc0），排除品牌字区域（top < 120）
    hits = []
    for y in range(120, img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.green() > 130 and c.blue() > 120 and c.red() < 180:
                hits.append((x, y, c.name()))
    print('品牌区以外找到的青绿/亮青像素 =', len(hits))
    for h in hits[:8]:
        print('    x=%d y=%d %s' % h)

    # 在预期位置附近细采
    cx, cy = off.x() + left, off.y() + top
    near = [h for h in hits if abs(h[0] - cx) < 40 and abs(h[1] - cy) < 40]
    print('预期图标位置 (%d,%d) 附近 40px 内的像素 = %d' % (cx, cy, len(near)))

    win.close()
    pump(150)
    return 0


if __name__ == '__main__':
    sys.exit(main())
