"""检查顶部工具栏的布局：按钮位置、是否重叠、是否超出窗口。

OCR 对区域裁剪的坐标不可靠，所以这里直接读 Qt 控件的真实几何，
用确定性的方式回答「加了按钮之后工具栏有没有挤坏」。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEventLoop, QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from gtiharmonica.config import Config                  # noqa: E402
from gtiharmonica.gui.theme import build_qss                  # noqa: E402
from gtiharmonica.gui.main import MainWindow            # noqa: E402


def pump(ms=600):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def report(win, width):
    win.resize(width, 880)
    pump(500)
    # 用**实际**宽度判断溢出，不是请求值：窗口有 setMinimumSize(1080)，
    # 请求 900 时 Qt 会把它撑回 1080，拿 900 去比必然误报。
    actual = win.width()
    # 工具栏是具名控件（平铺的 ghost 按钮 + 两个图标按钮），按对象属性找
    btns = []
    for name in ('btn_analysis', 'btn_calibrate', 'btn_import',
                 'btn_jianpu',
                 'btn_save_arrangement', 'btn_theme', 'btn_gear'):
        b = getattr(win, name, None)
        if b is not None and b.isVisible():
            btns.append(b)
    items = []
    for b in btns:
        p = b.mapTo(win, b.rect().topLeft())
        items.append((p.x(), p.y(), b.width(), b.height(), b.text()))
    items.sort()

    print('--- 窗口宽 %d（实际 %d）---' % (width, actual))
    row_y = min(i[1] for i in items) if items else 0
    # 同一行里的控件高度不同（QPushButton 27px / QToolButton 21px），
    # 垂直居中会让 top 差几个像素，所以用容差而不是精确相等。
    row = [i for i in items if abs(i[1] - row_y) <= 8]
    for x, y, w, h, t in row:
        print('   %-8s x=%4d w=%3d h=%2d' % (t, x, w, h))

    problems = []
    for a, b in zip(row, row[1:]):
        if a[0] + a[2] > b[0]:                       # 水平重叠
            problems.append('重叠: %s / %s' % (a[4], b[4]))
    if row:
        right = max(i[0] + i[2] for i in row)
        if right > actual - 8:
            problems.append('超出窗口右边界: %d > %d' % (right, actual - 8))
        print('   整行右边缘 x=%d（窗口 %d，余量 %d）'
              % (right, actual, actual - right))
    print('   -> %s' % ('；'.join(problems) if problems else '无重叠、无溢出'))
    return not problems


def report_header(win):
    """卡片标题行：分段控件与提示文字必须留在卡片里。

    分段控件是「编排预览 / 乐谱编辑」的入口，挤到卡片外面就点不到了。
    """
    card = win.roll.parentWidget()
    while card is not None and card.objectName() != 'card':
        card = card.parentWidget()
    if card is None:
        print('\n--- 卡片标题行 ---\n   -> 找不到卡片容器')
        return False

    rect = card.rect()
    switch = win.mode_switch
    tip = win.tip_label
    problems = []
    print('\n--- 卡片标题行（卡片宽 %d）---' % rect.width())

    sp = switch.mapTo(card, switch.rect().topLeft())
    tp = tip.mapTo(card, tip.rect().topLeft())
    print('   分段控件 x=%d w=%d' % (sp.x(), switch.width()))
    print('   提示文字 x=%d w=%d' % (tp.x(), tip.width()))

    if sp.x() < 0 or sp.x() + switch.width() > rect.width():
        problems.append('分段控件跑出卡片')
    if tp.x() < 0 or tp.x() + tip.width() > rect.width():
        problems.append('提示文字跑出卡片')
    if sp.x() + switch.width() > tp.x():
        problems.append('分段控件与提示文字重叠')
    # 分段控件自己内部也不能挤：两个按钮都得完整
    for name in ('btn_mode_preview', 'btn_mode_edit'):
        b = getattr(win, name)
        if b.width() < 60:
            problems.append('%s 被挤到 %dpx' % (name, b.width()))

    print('   -> %s' % ('；'.join(problems) if problems else '标题行正常'))
    return not problems


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_qss())
    win = MainWindow(Config(), os.path.join(ROOT, 'songs'))
    win.show()
    pump(700)

    ok = True
    # 1080 是 setMinimumSize 的下限，比它更小的请求没有意义
    for width in (1500, 1360, 1200, 1100, 1080):
        ok = report(win, width) and ok
    ok = report_header(win) and ok

    win.close()
    pump(150)
    print()
    print('结论：%s' % ('工具栏与标题行布局正常' if ok else '存在布局问题'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
