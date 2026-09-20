"""自定义控件：钢琴卷帘、按键映射行、指标条。

配色与样式对齐参考软件的「旋律预览」区：
  * 卷帘底色比卡片更深一层，形成层次
  * 音符是纯色圆角横条，宽度即音值
  * 左侧音名栏、底部时间刻度
  * 黄色播放头
  * 按键行显示「音级 + 键位」，如  1 Z  2 X …  高1 ,
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QPainter,
                           QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import QSizePolicy, QWidget

from .theme import C
from ..instrument import note_name


# 按键行
#: 键帽式设计：每个键是一块圆角「键帽」，音级小字在上、键位大字在下，
#: 分层靠底色与描边的轻重，不靠立体感。按下的键用主题强调色整块点亮，
#: 外圈一圈低透明度光环 —— 是现代 UI 的 focus ring 语言，不是描边浮雕。





_FONT_CACHE = {}


def mono_font(size: int = 9, bold: bool = False) -> QFont:
    key = (size, bold)
    font = _FONT_CACHE.get(key)
    if font is None:
        font = QFont('Consolas', size)
        font.setBold(bold)
        font.setStyleHint(QFont.Monospace)
        _FONT_CACHE[key] = font
    return font


def ui_font(size: int = 9, bold: bool = False) -> QFont:
    key = ('ui', size, bold)
    font = _FONT_CACHE.get(key)
    if font is None:
        font = QFont('Microsoft YaHei UI', size)
        font.setBold(bold)
        _FONT_CACHE[key] = font
    return font


class PianoRoll(QWidget):
    """旋律预览：横轴时间，纵轴音高。

    颜色区分指法复杂度 —— 绿松石（无需修饰键）越靠黄绿（修饰键越多），
    一眼能看出哪些段落手最忙。
    """

    seekRequested = Signal(float)

    LEFT_GUTTER = 50
    BOTTOM_AXIS = 24
    MIN_NOTE_H = 5.0
    LABEL_MIN_GAP = 26.0          # 音名标签的最小像素间隔
    #: 可见时间窗（秒）。把整曲压进一屏的话，两分多钟的曲子会有几百个
    #: 音符挤成一片，旋律轮廓完全看不出来 —— 改成固定窗 + 随播放滚动。
    #: 比这个窗还短的曲子直接铺满整屏，不滚动（短曲子那样最好看）。
    VIEW_SPAN = 10.0
    #: 播放头越过窗口这个比例后，窗口才往后推
    FOLLOW_AT = 0.72

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._steps: List = []
        self._duration = 0.0
        self._position = 0.0
        self._active = -1
        self._lo = 48
        self._hi = 84
        self._hover_x = -1.0
        self._title = ''
        self._subtitle = ''
        self._view_left = 0.0
        self._static = None          # 静态层缓存（背景/参考线/音符）
        self._static_key = None

    # -- 数据 --

    def set_plan(self, plan=None, subtitle: str = '') -> None:
        if plan is None or not len(plan.steps):
            self._steps = []
            self._duration = 0.0
            self._lo, self._hi = 48, 84
            self._title = ''
        else:
            self._steps = list(plan.steps)
            self._duration = max(plan.duration, 0.001)
            pitches = [s.pitch for s in self._steps]
            lo, hi = min(pitches), max(pitches)
            pad = max(2, (hi - lo) // 10)
            self._lo = max(0, lo - pad)
            self._hi = min(127, hi + pad)
            self._title = plan.title
        self._subtitle = subtitle
        self._active = -1
        self._position = 0.0
        self._view_left = 0.0
        self._invalidate_static()
        self.update()

    def set_position(self, seconds: float, index: int = -1) -> None:
        self._position = seconds
        self._active = index
        self._follow(seconds)
        self.update()

    def _follow(self, t: float) -> None:
        """让播放头留在可见窗里：越过右侧触发点就把窗口往后推。

        短于一个窗宽的曲子始终显示全曲，不滚动。
        """
        span = self._span()
        if self._duration <= span:
            self._view_left = 0.0
            return
        if t < self._view_left or t > self._view_left + span * self.FOLLOW_AT:
            self._view_left = max(0.0, min(t - span * 0.25,
                                           self._duration - span))

    def _span(self) -> float:
        """当前可见的时间跨度。"""
        if self._duration <= 0:
            return 1.0
        return min(self.VIEW_SPAN, self._duration)

    def step_count(self) -> int:
        return len(self._steps)

    # -- 坐标换算 --

    def _plot_rect(self) -> QRectF:
        return QRectF(self.LEFT_GUTTER, 0,
                      max(self.width() - self.LEFT_GUTTER - 1, 1),
                      max(self.height() - self.BOTTOM_AXIS - 1, 1))

    def _time_to_x(self, t: float) -> float:
        r = self._plot_rect()
        return r.left() + (t - self._view_left) / self._span() * r.width()

    def _x_to_time(self, x: float) -> float:
        r = self._plot_rect()
        if r.width() <= 0:
            return self._view_left
        frac = (x - r.left()) / r.width()
        return self._view_left + max(0.0, min(frac, 1.0)) * self._span()

    def _pitch_to_y(self, pitch: int) -> float:
        r = self._plot_rect()
        span = max(self._hi - self._lo, 1)
        return r.top() + (self._hi - pitch) / span * r.height()

    def _row_height(self) -> float:
        r = self._plot_rect()
        return r.height() / max(self._hi - self._lo + 1, 1)

    # -- 交互 --

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._steps:
            self.seekRequested.emit(self._x_to_time(event.position().x()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._hover_x = event.position().x()
        self.update()

    def leaveEvent(self, event):
        self._hover_x = -1.0
        self.update()

    # -- 绘制 --

    def paintEvent(self, event):
        p = QPainter(self)
        plot = self._plot_rect()

        # 静态层（背景 / 水平参考线 / 全部音符）缓存成 QPixmap。
        # 播放头每帧都在动，如果每帧都重画几百个音符，60fps 下光重绘
        # 就要吃掉 10% 以上的 CPU，也跑不满帧。缓存后每帧只补画动态部分。
        if not self._steps:
            p.fillRect(self.rect(), QColor(C.ROLL_BG))
            self._paint_empty(p)
            return

        p.drawPixmap(0, 0, self._static_layer())

        self._paint_active_note(p, plot)
        self._paint_upcoming(p, plot)
        self._paint_playhead(p, plot)
        self._paint_hover(p, plot)

    def _invalidate_static(self) -> None:
        self._static = None
        self._static_key = None

    def _static_layer(self):
        """构建（或复用）静态层。缓存键覆盖所有会影响它的状态。"""
        row_h = self._row_height()
        # 主题必须进缓存键：静态层把背景色画进 pixmap，切深浅色后
        # 不换键就会继续用旧主题的图层（表现为卷帘留着一块深色底）。
        key = (self.width(), self.height(), self._lo, self._hi,
               len(self._steps), round(self._view_left, 6), C.mode)
        if self._static is not None and self._static_key == key:
            return self._static

        pm = QPixmap(self.size())
        pm.fill(QColor(C.ROLL_BG))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        plot = self._plot_rect()
        self._paint_rows(p, plot, row_h)
        self._paint_notes(p, plot, row_h)
        self._paint_axis(p, plot)
        self._paint_gutter(p, plot, row_h)
        p.end()

        self._static = pm
        self._static_key = key
        return pm

    def _paint_active_note(self, p: QPainter, plot: QRectF):
        """当前演奏中的音符单独用高亮色补画（它每帧都在变，不能进缓存）。"""
        if self._active < 0 or self._active >= len(self._steps):
            return
        step = self._steps[self._active]
        span = self._span()
        if step.end < self._view_left or step.start > self._view_left + span:
            return
        p.save()
        p.setClipRect(plot)
        row_h = self._row_height()
        h = max(row_h * 0.52, self.MIN_NOTE_H)
        x0 = self._time_to_x(step.start)
        x1 = self._time_to_x(step.end)
        y = self._pitch_to_y(step.pitch) + (row_h - h) / 2.0
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(C.ROLL_PLAYHEAD)))
        p.drawRoundedRect(QRectF(x0, y, max(x1 - x0, 3.0), h), 2.0, 2.0)
        p.restore()

    def _paint_empty(self, p: QPainter):
        p.setPen(QColor(C.MUTED))
        p.setFont(ui_font(10))
        p.drawText(self.rect(), Qt.AlignCenter, '选择一首曲子以查看编排结果')

    def _paint_rows(self, p: QPainter, plot: QRectF, row_h: float):
        """每行一条水平线；黑键行略亮，C 音加粗。"""
        p.save()
        p.setClipRect(plot)
        for pitch in range(self._lo, self._hi + 1):
            y = self._pitch_to_y(pitch) + row_h
            if pitch % 12 == 0:
                p.setPen(QPen(QColor(C.ROLL_GRID_STRONG), 1))
            else:
                p.setPen(QPen(QColor(C.ROLL_GRID), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        p.restore()

    def _paint_grid(self, p: QPainter, plot: QRectF):
        p.save()
        p.setClipRect(plot)
        step = self._time_step()
        k = 1
        t = step
        while t < self._duration:
            x = self._time_to_x(t)
            # 每 4 条刻度一条更亮的「小节线」，眼睛定位靠这些锚点
            p.setPen(QPen(QColor(C.ROLL_GRID_STRONG) if k % 4 == 0
                          else QColor(C.ROLL_GRID), 1))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            t += step
            k += 1
        p.restore()

    def _time_step(self) -> float:
        span = self._span()
        for step in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300):
            if span / step <= 10:
                return step
        return 600.0

    def _paint_notes(self, p: QPainter, plot: QRectF, row_h: float):
        p.save()
        p.setClipRect(plot)
        p.setPen(Qt.NoPen)
        h = max(row_h * 0.52, self.MIN_NOTE_H)
        radius = 2.0

        # 只画落在可见窗里的音符：长曲子有几百个音，全画既浪费
        # 也会因为坐标落在窗口外而白白生成大量不可见图元。
        left_t = self._view_left
        right_t = self._view_left + self._span()

        for i, step in enumerate(self._steps):
            if step.end < left_t or step.start > right_t:
                continue
            x0 = self._time_to_x(step.start)
            x1 = self._time_to_x(step.end)
            w = max(x1 - x0, 3.0)
            y = self._pitch_to_y(step.pitch) + (row_h - h) / 2.0
            rect = QRectF(x0, y, w, h)

            # 当前音符的高亮交给动态层补画。如果在这里按 active 换色，
            # 每换一个音都会让静态层缓存作废，缓存就白做了。
            n = len(step.modifiers)
            color = (QColor(C.ROLL_NOTE) if n == 0 else
                     QColor(C.ROLL_NOTE_DIM) if n == 1 else QColor(C.ROLL_NOTE_MUTED))
            p.setBrush(QBrush(color))
            p.drawRoundedRect(rect, radius, radius)
        p.restore()

    def _paint_upcoming(self, p: QPainter, plot: QRectF):
        """预读高亮：播放头前方 1.2 秒内即将弹响的音符描一圈发光边，
        眼睛不用找「下一个音在哪」。只动动态层，静态缓存不受影响。"""
        if self._position <= 0 or not self._steps:
            return
        horizon = self._position + 1.2
        p.save()
        p.setClipRect(plot)
        p.setRenderHint(QPainter.Antialiasing, True)
        row_h = self._row_height()
        h = max(row_h * 0.52, self.MIN_NOTE_H)
        glow = QColor(C.ROLL_PLAYHEAD)
        glow.setAlpha(150)
        for step in self._steps:
            if step.end <= self._position:
                continue
            if step.start > horizon:
                break
            x0 = self._time_to_x(max(step.start, self._position))
            x1 = self._time_to_x(step.end)
            w = max(x1 - x0, 3.0)
            y = self._pitch_to_y(step.pitch) + (row_h - h) / 2.0
            p.setPen(QPen(glow, 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(x0, y, w, h), 3.0, 3.0)
        p.restore()

    def _paint_playhead(self, p: QPainter, plot: QRectF):
        if self._position <= 0:
            return
        x = self._time_to_x(self._position)
        p.save()
        p.setClipRect(plot)
        # 一条 1.6px 实线加一圈低透明度光晕：单根细线在密集音符上
        # 经常找不到，光晕让它从背景里「浮」出来又不刺眼。
        glow = QColor(QColor(C.ROLL_PLAYHEAD))
        glow.setAlpha(34)
        p.setPen(QPen(glow, 5.0))
        p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        p.setPen(QPen(QColor(C.ROLL_PLAYHEAD), 1.6))
        p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        # 顶部小三角：视线扫过来时先看到它，像视频剪辑软件的时间指针
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(C.ROLL_PLAYHEAD)))
        p.drawPolygon(QPolygonF([QPointF(x - 3.5, plot.top()),
                                 QPointF(x + 3.5, plot.top()),
                                 QPointF(x, plot.top() + 5.5)]))
        p.restore()

    def _paint_axis(self, p: QPainter, plot: QRectF):
        """底部时间刻度，只画可见窗内的。

        刻度对齐到 step 的整数倍，滚动时数字才不会左右乱跳 ——
        否则窗口一动，每个刻度都会跟着重排。这里也不叠总时长，
        它和末位刻度会撞在一起（显示成「120s/2:06」）。
        """
        y = plot.bottom()
        p.setFont(mono_font(8))
        p.setPen(QColor(C.MUTED))
        step = self._time_step()
        right_t = self._view_left + self._span()
        t = math.floor(self._view_left / step) * step
        while t <= right_t + 1e-6:
            if t >= self._view_left - 1e-6 and t >= -1e-6:
                x = self._time_to_x(t)
                label = ('%gs' % round(t, 2)) if step < 1 else ('%ds' % round(t))
                p.drawText(QPointF(x, y + 14), label)
            t += step

    def _paint_gutter(self, p: QPainter, plot: QRectF, row_h: float):
        """音高轴标签。

        目标 6 个：只标 3 个看不出音区分布，把整条音域的音名都标满又会变成
        一堵字墙。行距不够时按可容纳数量回落，并跳过重复音名避免叠字。
        """
        span = self._hi - self._lo + 1
        if span <= 0 or plot.height() <= 0:
            return
        # 24px 一行：常见高度下都够放满 6 个，矮窗口也不会掉到只剩两三个
        count = int(max(2, min(6, plot.height() // 24)))
        count = min(count, max(span // 2, 2))
        p.setFont(mono_font(8))
        used = set()
        for i in range(count):
            pitch = self._lo + int(round(i * (span - 1) / max(count - 1, 1)))
            if pitch % 12 in (1, 3, 6, 8, 10):      # 落到黑键时移到下方白键
                pitch -= 1
            if pitch in used:                        # 相邻标签可能并到同一个音
                continue
            used.add(pitch)
            y = self._pitch_to_y(pitch) + row_h / 2.0
            p.setPen(QColor(C.ROLL_LABEL))
            p.drawText(QRectF(0, y - 7, self.LEFT_GUTTER - 10, 14),
                       Qt.AlignRight | Qt.AlignVCenter, note_name(pitch))

    def _paint_hover(self, p: QPainter, plot: QRectF):
        if self._hover_x < 0:
            return
        t = self._x_to_time(self._hover_x)
        p.setPen(QPen(QColor(C.ROLL_CURSOR), 1, Qt.DashLine))
        p.drawLine(QPointF(self._hover_x, plot.top()),
                   QPointF(self._hover_x, plot.bottom()))
        # 编辑器整段删除选终点时，悬停线就是「终点预览」，时间读数已经
        # 包含在选段标签里，这里不再重复一个（否则两个标签会叠在一起）。
        if getattr(self, '_range_pick', False):
            return
        p.setFont(mono_font(8))
        p.setPen(QColor(C.ROLL_CURSOR))
        p.drawText(QPointF(min(self._hover_x + 4, plot.right() - 46), 14),
                   '%.3f s' % t)

    def sizeHint(self):
        return QSize(720, 220)


class KeyStrip(QWidget):
    """按键映射行：显示 8 个音阶键的「音级 + 键位」，演奏时高亮。

    形如   1 Z   2 X   3 C   4 V   5 B   6 N   7 M   高1 ,
    下方是按住式修饰键（鼠标左/中/右）。
    """

    DEGREE = ('1', '2', '3', '4', '5', '6', '7', '高1')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(54)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._keys: List[str] = list('zxcvbnm,')
        self._mods: List[str] = ['mouse_left', 'mouse_middle', 'mouse_right']
        self._active_key: Optional[str] = None
        self._active_mods: tuple = ()

    def configure(self, instrument) -> None:
        self._keys = list(instrument.keys)
        self._mods = [instrument.lower, instrument.semitone, instrument.upper]
        self.update()

    def set_active(self, key: Optional[str], modifiers: Sequence[str] = ()) -> None:
        if key == self._active_key and tuple(modifiers) == self._active_mods:
            return
        self._active_key = key
        self._active_mods = tuple(modifiers)
        self.update()

    _MOD_LABEL = {'mouse_left': '左键', 'mouse_middle': '中键', 'mouse_right': '右键'}

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        n = max(len(self._keys), 1)
        gap = 7
        key_w = (self.width() - gap * (n - 1)) / n
        for i, key in enumerate(self._keys):
            x = i * (key_w + gap)
            rect = QRectF(x, 0, key_w, self.height() - 4)
            pressed = (key == self._active_key)
            degree = self.DEGREE[i] if i < len(self.DEGREE) else ''
            self._draw_key(p, rect, key.upper(), degree, pressed)

    def _draw_key(self, p: QPainter, rect: QRectF, key: str, degree: str,
                  pressed: bool):
        p.setPen(QPen(QColor(C.KEY_ACTIVE if pressed else C.KEY_BORDER), 1))
        p.setBrush(QBrush(QColor(C.KEY_ACTIVE if pressed else C.KEY_BG)))
        p.drawRoundedRect(rect, 10, 10)
        if pressed:
            glow = QColor(C.KEY_ACTIVE)
            glow.setAlpha(64)
            p.setPen(QPen(glow, 3))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(-2.0, -2.0, 2.0, 2.0), 12, 12)

        fg = QColor(C.KEY_ACTIVE_DEEP) if pressed else QColor(C.KEY_TEXT)
        dim = QColor(C.KEY_ACTIVE_DEEP) if pressed else QColor(C.MUTED)
        p.setFont(ui_font(8))
        p.setPen(dim)
        p.drawText(QRectF(rect.left(), rect.top() + 5, rect.width(), 11),
                   Qt.AlignHCenter | Qt.AlignVCenter, degree)
        p.setFont(mono_font(12, True))
        p.setPen(fg)
        p.drawText(QRectF(rect.left(), rect.top() + 14, rect.width(),
                          rect.height() - 19),
                   Qt.AlignHCenter | Qt.AlignVCenter, key)

    def sizeHint(self):
        return QSize(560, 54)


class ModifierStrip(QWidget):
    """按住式修饰键状态条（鼠标左/中/右）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)
        self._mods = ['mouse_left', 'mouse_middle', 'mouse_right']
        self._active: tuple = ()

    def configure(self, instrument) -> None:
        self._mods = [instrument.lower, instrument.semitone, instrument.upper]
        self.update()

    def set_active(self, modifiers: Sequence[str] = ()) -> None:
        if tuple(modifiers) == self._active:
            return
        self._active = tuple(modifiers)
        self.update()

    _LABEL = {'mouse_left': '按住左键 · 降八度',
              'mouse_middle': '按住中键 · 升半音',
              'mouse_right': '按住右键 · 升八度'}

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setFont(ui_font(8))
        x = 0.0
        h = self.height()
        for mod in self._mods:
            text = self._LABEL.get(mod, mod)
            fm = QFontMetrics(p.font())
            w = fm.horizontalAdvance(text) + 16
            rect = QRectF(x, 1, w, h - 2)
            on = mod in self._active
            if on:
                tint = QColor(C.KEY_ACTIVE)
                tint.setAlpha(46)
                p.setPen(QPen(QColor(C.KEY_ACTIVE), 1))
                p.setBrush(QBrush(tint))
            else:
                # 闲时只留一圈极淡的轮廓：三个修饰键常驻在画面里，
                # 画成实心块会喧宾夺主
                p.setPen(QPen(QColor(C.KEY_BORDER), 1))
                p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
            p.setPen(QColor(C.KEY_TEXT if on else C.MUTED))
            p.drawText(rect, Qt.AlignCenter, text)
            x += w + 6


class MetricBar(QWidget):
    """水平占比条，用于展示指标对比。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(16)
        self._value = 0.0
        self._max = 1.0
        self._color = QColor(C.TRACK_FILL)
        self._label = ''

    def set_value(self, value: float, maximum: float,
                  color: Optional[QColor] = None, label: str = '') -> None:
        self._value = value
        self._max = max(maximum, 1e-9)
        self._label = label
        if color:
            self._color = color
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0, 4, self.width(), self.height() - 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(C.SLIDER_GROOVE)))
        p.drawRoundedRect(rect, 3, 3)
        frac = max(0.0, min(self._value / self._max, 1.0))
        if frac > 0:
            p.setBrush(QBrush(self._color))
            p.drawRoundedRect(QRectF(rect.left(), rect.top(),
                                     max(rect.width() * frac, 4), rect.height()),
                              3, 3)
