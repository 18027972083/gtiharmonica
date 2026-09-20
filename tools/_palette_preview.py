# -*- coding: utf-8 -*-
"""配色方案预览：派生 4 套「高雅黑 / 典雅白 + 对比色」候选，
用真实 QSS 渲染成对比图，供用户挑选后再正式写入 theme.py。

只是预览：这里的派生规则刻意简单（去饱和底色 + 强调色族整体替换），
选定方案后会在 theme.py 里逐 token 手工调校，不直接沿用本脚本结果。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QSlider, QVBoxLayout, QWidget)

from gtiharmonica.gui import theme
from gtiharmonica.gui.theme import DARK, LIGHT, C, build_qss

# ------------------------------------------------------------------
# 颜色工具
# ------------------------------------------------------------------

def mix(a, b, t):
    """t=0 -> a, t=1 -> b。"""
    ca, cb = QColor(a), QColor(b)
    return '#%02x%02x%02x' % (
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t))


def shade(c, f):
    """f>0 向白提亮，f<0 向黑压暗。"""
    return mix(c, '#ffffff' if f > 0 else '#000000', abs(f))


def desat(c, k=0.32):
    """把底色压向中性灰 —— 「黑/白」的优雅感来自低饱和。"""
    col = QColor(c)
    h, s, l, _ = col.getHslF()
    return QColor.fromHslF(h, s * k, l, 1.0).name()


def lum(c):
    col = QColor(c)
    return 0.2126 * col.red() + 0.7152 * col.green() + 0.0722 * col.blue()


def on_color(c):
    """该颜色上面该配深字还是白字。"""
    return '#141414' if lum(c) > 150 else '#ffffff'


# 底色 / 文字族：做「高雅黑 / 典雅白」时去饱和的 token
NEUTRAL_KEYS = [
    'WINDOW', 'SIDEBAR', 'CARD', 'INPUT', 'POPUP',
    'BORDER', 'BORDER_SOFT', 'FIELD_BORDER',
    'BTN', 'BTN_BORDER', 'BTN_HOVER', 'BTN_HOVER_BORDER', 'BTN_PRESSED',
    'HOVER', 'CONTROL_HOVER', 'CONTROL_HOVER_2',
    'SEG_CHECKED_BG', 'BTN_DISABLED_BG', 'BTN_DISABLED_BORDER',
    'PRIMARY_DISABLED_BG', 'PANEL_GREY',
    'SLIDER_GROOVE', 'PROGRESS_BG', 'SCROLL', 'SCROLL_HOVER',
    'TOOLTIP_BG', 'TOOLTIP_BORDER', 'SPLITTER',
    'ROLL_BG', 'ROLL_GRID', 'ROLL_GRID_STRONG', 'ROLL_LABEL',
    'EDIT_BAR', 'EDIT_BAR_NUM', 'EDIT_BEAT', 'EDIT_TIE',
    'TEXT', 'MUTED', 'DISABLED', 'KEY_TEXT', 'TOOLTIP_FG',
    'KEY_BG', 'KEY_BORDER',
]

SCHEMES = {
    'A 香槟金 · 黑金': {
        'dark': '#e8c57e', 'light': '#9a7514',
    },
    'B 绯红 · 黑红': {
        'dark': '#ff5d6c', 'light': '#d93a4b',
    },
    'C 电光青 · 黑青': {
        'dark': '#2fd6f2', 'light': '#0b93b8',
    },
    'D 紫罗兰 · 黑紫': {
        'dark': '#b39bfa', 'light': '#6d4fe0',
    },
}


def derive(base: dict, accent: str, mode: str) -> dict:
    """在现有主题表上派生：底色去饱和 + 强调色族整体换血。"""
    d = dict(base)
    for k in NEUTRAL_KEYS:
        d[k] = desat(d[k])
    acc = QColor(accent)
    card = d['CARD']
    if mode == 'dark':
        text_on = on_color(accent)
        brand = accent if lum(accent) > 110 else shade(accent, 0.25)
        d.update({
            'BRAND': brand, 'ACCENT': brand,
            'ACCENT_HOVER': shade(brand, 0.15),
            'ACCENT_DEEP': mix(accent, d['WINDOW'], 0.78),
            'TRACK_FILL': shade(accent, -0.08),
            'KNOB': shade(accent, 0.3),
            'SELECT_BG': mix(accent, card, 0.82),
            'SELECT_FG': shade(brand, 0.45),
            'STATUS': shade(brand, 0.12),
            'SEG_CHECKED_FG': brand,
            'PRIMARY_TEXT': text_on,
            'PRIMARY_SOLID': accent,
            'PRIMARY_SOLID_H': shade(accent, 0.12),
            'PRIMARY_PRESSED': shade(accent, -0.18),
            'ROLL_NOTE': shade(accent, 0.52),
            'ROLL_NOTE_DIM': accent,
            'ROLL_NOTE_MUTED': mix(accent, card, 0.72),
            'KEY_ACTIVE': accent,
            'KEY_ACTIVE_DEEP': text_on,
            'COMBO_SEL': mix(accent, d['POPUP'], 0.72),
            'MENU_SEL': mix(accent, d['POPUP'], 0.80),
            'SLIDER_HANDLE': shade(accent, 0.35),
            'SLIDER_HANDLE_BORDER': shade(accent, -0.55),
            'SLIDER_PRESSED': shade(accent, 0.6),
            'KEY_HINT_ON': mix(accent, d['WINDOW'], 0.6),
        })
    else:
        d.update({
            'BRAND': accent, 'ACCENT': accent,
            'ACCENT_HOVER': shade(accent, -0.10),
            'ACCENT_DEEP': mix(accent, '#ffffff', 0.88),
            'TRACK_FILL': shade(accent, 0.05),
            'KNOB': shade(accent, -0.15),
            'SELECT_BG': mix(accent, '#ffffff', 0.86),
            'SELECT_FG': shade(accent, -0.15),
            'STATUS': shade(accent, -0.12),
            'SEG_CHECKED_FG': shade(accent, -0.10),
            'PRIMARY_TEXT': on_color(accent),
            'PRIMARY_SOLID': accent,
            'PRIMARY_SOLID_H': shade(accent, -0.10),
            'PRIMARY_PRESSED': shade(accent, -0.22),
            'ROLL_NOTE': accent,
            'ROLL_NOTE_DIM': mix(accent, '#ffffff', 0.55),
            'ROLL_NOTE_MUTED': mix(accent, '#ffffff', 0.78),
            'KEY_ACTIVE': accent,
            'KEY_ACTIVE_DEEP': on_color(accent),
            'COMBO_SEL': mix(accent, '#ffffff', 0.86),
            'MENU_SEL': mix(accent, '#ffffff', 0.90),
            'SLIDER_HANDLE': '#ffffff',
            'SLIDER_HANDLE_BORDER': accent,
            'SLIDER_PRESSED': accent,
            'KEY_HINT_ON': mix(accent, '#ffffff', 0.9),
        })
    return d


# ------------------------------------------------------------------
# 预览面板：真实控件 + 简化卷帘/键帽
# ------------------------------------------------------------------

class NoteStrip(QWidget):
    """迷你卷帘：按当前 C 调色板画几颗音符 + 播放头。"""

    def __init__(self, notes, parent=None):
        super().__init__(parent)
        self._notes = notes          # (x0,x1,row, kind) kind: 0亮1中2弱

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C.ROLL_BG))
        p.drawRoundedRect(self.rect(), 8, 8)
        rows = 5
        rh = (self.height() - 12) / rows
        for i in range(rows + 1):
            p.setPen(QPen(QColor(C.ROLL_GRID), 1))
            y = 6 + i * rh
            p.drawLine(8, y, self.width() - 8, y)
        colors = [QColor(C.ROLL_NOTE), QColor(C.ROLL_NOTE_DIM),
                  QColor(C.ROLL_NOTE_MUTED)]
        w = self.width()
        h = max(rh * 0.55, 5)
        for x0, x1, row, kind in self._notes:
            y = 6 + row * rh + (rh - h) / 2
            p.setPen(Qt.NoPen)
            p.setBrush(colors[kind])
            p.drawRoundedRect(QRectF(8 + x0 * (w - 16), y,
                                     max((x1 - x0) * (w - 16), 5), h), 2.5, 2.5)
        # 播放头
        px = 8 + 0.46 * (w - 16)
        p.setPen(QPen(QColor(C.ROLL_PLAYHEAD), 1.6))
        p.drawLine(int(px), 4, int(px), self.height() - 4)


class KeyCaps(QWidget):
    """迷你键帽行。"""

    KEYS = [('1', 'Z'), ('2', 'X'), ('3', 'C'), ('4', 'V')]

    def __init__(self, parent=None):
        super().__init__(parent)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        n = len(self.KEYS)
        gap = 8
        kw = (self.width() - gap * (n - 1)) / n
        for i, (deg, key) in enumerate(self.KEYS):
            rect = QRectF(i * (kw + gap), 0, kw, self.height() - 4)
            p.setPen(QPen(QColor(C.KEY_BORDER), 1))
            p.setBrush(QColor(C.KEY_BG))
            p.drawRoundedRect(rect, 9, 9)
            p.setFont(QFont('Microsoft YaHei UI', 7))
            p.setPen(QColor(C.MUTED))
            p.drawText(QRectF(rect.left(), rect.top() + 3, rect.width(), 10),
                       Qt.AlignHCenter, deg)
            p.setFont(QFont('Consolas', 10, QFont.Bold))
            p.setPen(QColor(C.KEY_TEXT))
            p.drawText(QRectF(rect.left(), rect.top() + 12, rect.width(),
                              rect.height() - 15),
                       Qt.AlignHCenter, key)


def make_panel(mode: str, scheme_name: str) -> QWidget:
    """一块真实控件面板（当前 C 已 load 对应模式）。
    绝对坐标布局（不经布局管理器）：预览图的控件位置必须确定，
    懒激活的布局管理器会让 grab 抓到半成品几何。方案名与强调色
    直接画进面板，杜绝任何标签与内容的错位。"""
    panel = QFrame()
    panel.setObjectName('card')
    panel.setFixedSize(1060, 292)

    side = QFrame()
    side.setObjectName('sidebar')
    side.setParent(panel)
    side.setGeometry(0, 0, 210, 292)

    brand = QLabel('DeltaHarp', side)
    brand.setObjectName('brand')
    brand.setGeometry(16, 16, 180, 36)

    for i, (txt, selected) in enumerate((('AIZO（修复版）', True),
                                         ('SPECIALZ', False))):
        item = QLabel(txt, side)
        item.setAlignment(Qt.AlignVCenter)
        item.setGeometry(16, 62 + i * 46, 178, 38)
        item.setStyleSheet(
            'background: %s; color: %s; border-radius: 8px; padding: 7px 9px;'
            % (C.SELECT_BG if selected else 'transparent',
               C.SELECT_FG if selected else C.TEXT))

    eyebrow = QLabel('DELTA HARP STUDIO', panel)
    eyebrow.setObjectName('eyebrow')
    eyebrow.setGeometry(230, 16, 300, 22)

    tag = QLabel('%s  ·  强调色 %s' % (scheme_name, C.ACCENT), panel)
    tag.setStyleSheet('color: %s; font-size: 12px;' % C.MUTED)
    tag.setGeometry(230, 44, 400, 18)

    ghost = QPushButton('策略分析', panel)
    ghost.setObjectName('ghost')
    ghost.setGeometry(820, 12, 96, 36)

    save = QPushButton('保存编排曲谱', panel)
    save.setGeometry(920, 12, 126, 36)

    strip = NoteStrip([(0.00, 0.06, 3, 0), (0.08, 0.13, 2, 0),
                       (0.15, 0.22, 3, 1), (0.24, 0.30, 1, 0),
                       (0.33, 0.38, 2, 2), (0.40, 0.52, 3, 0),
                       (0.55, 0.60, 0, 1), (0.63, 0.70, 2, 0),
                       (0.73, 0.80, 3, 1), (0.84, 0.95, 1, 0)], panel)
    strip.setGeometry(230, 76, 816, 68)

    slider = QSlider(Qt.Horizontal, panel)
    slider.setValue(46)
    slider.setGeometry(230, 168, 500, 28)

    play = QPushButton('开始演奏   F8', panel)
    play.setObjectName('primary')
    play.setGeometry(756, 154, 290, 54)

    keys = KeyCaps(panel)
    keys.setGeometry(230, 234, 816, 44)
    return panel


def main():
    # 单进程只渲染一个方案（SCHEME_IDX 环境变量选择），杜绝同进程内
    # 连续切换调色板带来的任何脏状态；拼接由 compose() 纯 PIL 完成。
    idx = int(os.environ.get('SCHEME_IDX', '-1'))
    if idx < 0:
        compose()
        return

    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    name, spec = list(SCHEMES.items())[idx]
    derived = {'dark': derive(DARK, spec['dark'], 'dark'),
               'light': derive(LIGHT, spec['light'], 'light')}
    imgs = []
    for mode in ('dark', 'light'):
        theme.TABLES = {'dark': derived['dark'], 'light': derived['light']}
        theme.apply_theme(app, mode)
        w = make_panel(mode, name)
        w.show()
        app.processEvents()
        qimg = w.grab().toImage()
        w.close()
        app.processEvents()
        buf = bytes(qimg.constBits())
        from PIL import Image as _I
        # Qt QImage 小端序是 BGRA；按 RGBA 直读会红蓝颠倒，
        # 金色变蓝、青色变黄 —— 必须声明 raw 格式为 BGRA
        imgs.append(_I.frombytes('RGBA', (qimg.width(), qimg.height()),
                                 buf, 'raw', 'BGRA').convert('RGB'))

    base = os.path.join(ROOT, 'build', 'logo', '_scheme_%d' % idx)
    imgs[0].save(base + '.png')
    imgs[1].save(base + 'l.png')
    print('saved', base + '.png /', base + 'l.png')


def compose():
    from PIL import Image
    GAP = 18
    n = len(SCHEMES)
    first = Image.open(os.path.join(ROOT, 'build', 'logo', '_scheme_0.png'))
    W, ph = first.size
    H = 12 + n * (ph + 12) + 2
    img = Image.new('RGB', (GAP + (W + GAP) * 2, H), '#101012')
    for i in range(n):
        dk = Image.open(os.path.join(ROOT, 'build', 'logo', '_scheme_%d.png' % i))
        lt = Image.open(os.path.join(ROOT, 'build', 'logo', '_scheme_%dl.png' % i))
        y = 12 + i * (ph + 12)
        img.paste(dk, (GAP, y))
        img.paste(lt, (GAP + W + GAP, y))
    out = os.path.join(ROOT, 'build', 'logo', 'color_schemes.png')
    img.save(out)
    print('saved', out)


if __name__ == '__main__':
    main()
