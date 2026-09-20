"""演奏悬浮窗 —— 切到游戏后仍然看得到演奏进度。

移植自 midikey-player 的 OverlayWindow 思路：
  * 倒计时阶段显示大号秒数
  * 演奏中显示滚动迷你卷帘（可见 8 秒，播放头固定在 1/4 处，
    右边是马上要弹的音）+ 进度条 + 时间
  * 置顶、无边框、不抢焦点（点击它不会把游戏切到后台）
  * 可拖动，位置记忆在 config
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from .theme import C

def _tint(hex_color: str, alpha: int) -> QColor:
    """主题色 + 透明度（悬浮窗是半透明覆盖层）。"""
    c = QColor(hex_color)
    c.setAlpha(alpha)
    return c


def BG():
    return _tint(C.WINDOW, 216)


def EDGE():
    return _tint(C.BORDER, 140)


def ACCENT():
    return QColor(C.ACCENT)


def GOLD():
    return QColor(C.ROLL_PLAYHEAD)


def TEXT():
    return QColor(C.TEXT)


def MUTED():
    return QColor(C.MUTED)

#: 迷你卷帘可见窗口（秒）：播放头前 2 秒、后 6 秒
WINDOW_BACK = 2.0
WINDOW_AHEAD = 6.0


class OverlayWindow(QWidget):
    """置顶演奏悬浮窗。用法：show_countdown → begin → set_position → finish。"""

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self._config = config
        self.setWindowTitle('大肥鲸洲琴工具包 悬浮窗')
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(320, 148)

        self._mode = 'hidden'          # hidden / countdown / playing
        self._countdown = 0
        self._position = 0.0
        self._duration = 0.0
        self._steps: List[Tuple[float, float, int]] = []   # (start, end, pitch)
        self._pitch_lo = 48
        self._pitch_hi = 84
        self._state_text = ''
        self._paused = False

        self._clock = QTimer(self)
        self._clock.setInterval(16)          # 60fps，与应用内试听一致
        self._clock.timeout.connect(self._advance_clock)
        self._anchor_pos = 0.0               # 对表锚点：锚定时刻的已知位置
        self._anchor_t = 0.0

        self._drag_offset: Optional[QPoint] = None
        self._restore_place()
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(2500)
        self._hide_timer.timeout.connect(self.hide)

    # -- 对外接口 --

    def show_countdown(self, seconds_left: int) -> None:
        self._mode = 'countdown'
        self._countdown = max(0, seconds_left)
        self._position = 0.0
        self._clock.stop()
        self.show()
        self.update()

    def begin(self, plan, duration: float) -> None:
        """进入演奏模式。plan 需要 steps（含 start/end/pitch/modifiers）。"""
        self._mode = 'playing'
        self._duration = max(duration, 0.001)
        self._position = 0.0
        self._paused = False
        self._state_text = ''
        steps = []
        lo, hi = 60, 72
        for s in plan.steps:
            p = s.pitch          # PlayStep.pitch 是实际发音的 MIDI 音高
            steps.append((s.start, s.end, p, len(s.modifiers)))
            lo, hi = min(lo, p), max(hi, p)
        self._steps = steps
        self._pitch_lo, self._pitch_hi = lo - 1, hi + 1
        self._hide_timer.stop()
        self._anchor_pos, self._anchor_t = 0.0, time.perf_counter()
        self._clock.start()
        self.show()
        self.raise_()
        self.update()

    def set_position(self, position: float) -> None:
        """worker 的权威位置（只在音符时刻到来）。

        平时位置由 _advance_clock 自走；偏差超过阈值才重新对表 ——
        正常演奏两者都是 perf_counter 时间轴，漂移极小，这样既平滑
        又不会在循环/起播跳变时跑飞。"""
        if self._mode != 'playing':
            return
        if self._paused:
            self._position = position
            self.update()
            return
        now = time.perf_counter()
        est = self._anchor_pos + (now - self._anchor_t)
        if abs(position - est) > 0.05:
            self._anchor_pos, self._anchor_t = position, now

    def set_state(self, text: str, paused: bool = False) -> None:
        self._state_text = text
        was = self._paused
        self._paused = paused
        if self._mode != 'playing':
            return
        if paused and not was:
            self._clock.stop()                 # 冻结在当前位置
        elif not paused and was:
            self._anchor_pos = self._position  # 从冻结处重新起表
            self._anchor_t = time.perf_counter()
            self._clock.start()
        self.update()

    def _advance_clock(self) -> None:
        if self._mode != 'playing' or self._paused:
            self._clock.stop()
            return
        self._position = min(
            self._anchor_pos + (time.perf_counter() - self._anchor_t),
            self._duration)
        self.update()

    def finish(self) -> None:
        """演奏结束：短暂展示最终状态后自动隐藏。"""
        if self._mode == 'playing':
            self._clock.stop()
            self._hide_timer.start()
            self.update()

    def stop_hidden(self) -> None:
        self._mode = 'hidden'
        self._clock.stop()
        self._hide_timer.stop()
        self.hide()

    # -- 拖动 --

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None \
                and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        self._remember_place()

    def _restore_place(self) -> None:
        try:
            x = int(self._config.value('overlay_x', -1))
            y = int(self._config.value('overlay_y', -1))
            if x >= 0 and y >= 0:
                self.move(x, y)
                return
        except Exception:
            pass
        screen = self.screen().geometry() if self.screen() else None
        if screen:
            self.move(screen.right() - self.width() - 24,
                      screen.top() + 96)

    def _remember_place(self) -> None:
        if self._config is None:
            return
        try:
            self._config.setValue('overlay_x', self.x())
            self._config.setValue('overlay_y', self.y())
        except Exception:
            pass

    # -- 绘制 --

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(BG())
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1,
                                       self.height() - 1), 12, 12)
        painter.setPen(QPen(EDGE(), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1,
                                       self.height() - 1), 12, 12)

        if self._mode == 'countdown':
            self._paint_countdown(painter)
        elif self._mode == 'playing':
            self._paint_roll(painter)
            self._paint_progress(painter)

    def _paint_countdown(self, painter: QPainter) -> None:
        painter.setPen(ACCENT())
        painter.setFont(QFont('Segoe UI', 44, QFont.Bold))
        painter.drawText(self.rect(), Qt.AlignCenter, str(self._countdown))
        painter.setPen(MUTED())
        painter.setFont(QFont('Microsoft YaHei UI', 9))
        painter.drawText(QRectF(0, self.height() - 30, self.width(), 22),
                         Qt.AlignHCenter, '请切到游戏 · F8 暂停 / F9 停止')

    def _paint_roll(self, painter: QPainter) -> None:
        """迷你卷帘 —— 视觉与应用内 PianoRoll 同一套语言：
        音级网格 / 指法复杂度三档配色 / 预读金边 / 光晕播放头。"""
        area = QRectF(12, 10, self.width() - 24, 62)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_tint(C.ROLL_BG, 200))
        painter.drawRoundedRect(area, 8, 8)

        t0 = self._position - WINDOW_BACK
        span = WINDOW_BACK + WINDOW_AHEAD
        head_x = area.x() + area.width() * (WINDOW_BACK / span)
        lo, hi = self._pitch_lo, self._pitch_hi
        rows = max(hi - lo + 1, 1)
        rh = area.height() / rows

        painter.save()
        painter.setClipRect(area)

        # 音级网格：行距放得下就每行一条淡线（C 音行加粗），放不下退到八度线
        painter.setRenderHint(QPainter.Antialiasing, False)
        for p in range(lo, hi + 1):
            if rh < 4.0 and p % 12 != 0:
                continue
            y = area.top() + (hi - p + 1) * rh
            painter.setPen(QPen(
                QColor(C.ROLL_GRID_STRONG if p % 12 == 0 else C.ROLL_GRID), 1))
            painter.drawLine(QPointF(area.left(), y),
                             QPointF(area.right(), y))
        painter.setRenderHint(QPainter.Antialiasing, True)

        # 音符：按修饰键数量三档配色，与应用内一致
        note_h = max(3.5, min(rh * 0.6, area.height() / max(6.0, rows / 3.0)))
        colors = (QColor(C.ROLL_NOTE), QColor(C.ROLL_NOTE_DIM),
                  QColor(C.ROLL_NOTE_MUTED))
        active = None
        for start, end, pitch, nmods in self._steps:
            if end < t0 or start > t0 + span:
                continue
            x1 = area.x() + area.width() * max(0.0, (start - t0) / span)
            x2 = area.x() + area.width() * min(1.0, (end - t0) / span)
            y = area.top() + (hi - pitch) * rh + (rh - note_h) / 2.0
            rect = QRectF(x1, y, max(3.0, x2 - x1), note_h)
            painter.setBrush(colors[min(nmods, 2)])
            painter.drawRoundedRect(rect, 2, 2)
            if start <= self._position <= end:
                active = rect

        # 预读金边：1.2 秒内要弹的音描一圈发光边（应用内同款）
        glow = QColor(C.ROLL_PLAYHEAD)
        glow.setAlpha(150)
        painter.setPen(QPen(glow, 1.5))
        painter.setBrush(Qt.NoBrush)
        for start, end, pitch, _ in self._steps:
            if not (self._position < start <= self._position + 1.2):
                continue
            if end < t0 or start > t0 + span:
                continue
            x1 = area.x() + area.width() * max(0.0, (start - t0) / span)
            x2 = area.x() + area.width() * min(1.0, (end - t0) / span)
            y = area.top() + (hi - pitch) * rh + (rh - note_h) / 2.0
            painter.drawRoundedRect(
                QRectF(x1, y, max(3.0, x2 - x1), note_h), 2, 2)

        # 当前音：金色实心（应用内把 active note 画成播放头色）
        if active is not None:
            painter.setPen(Qt.NoPen)
            painter.setBrush(GOLD())
            painter.drawRoundedRect(active, 2, 2)

        # 播放头：光晕粗线 + 主线 + 顶部小三角，应用内同款三件套
        halo = QColor(C.ROLL_PLAYHEAD)
        halo.setAlpha(36)
        painter.setPen(QPen(halo, 5.0))
        painter.drawLine(QPointF(head_x, area.top()),
                         QPointF(head_x, area.bottom()))
        painter.setPen(QPen(GOLD(), 1.6))
        painter.drawLine(QPointF(head_x, area.top()),
                         QPointF(head_x, area.bottom()))
        painter.setPen(Qt.NoPen)
        painter.setBrush(GOLD())
        painter.drawPolygon(QPolygonF([
            QPointF(head_x - 3.5, area.top()),
            QPointF(head_x + 3.5, area.top()),
            QPointF(head_x, area.top() + 5.5)]))
        painter.restore()

    def _paint_progress(self, painter: QPainter) -> None:
        bar = QRectF(12, self.height() - 34, self.width() - 24, 5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(C.SLIDER_GROOVE))
        painter.drawRoundedRect(bar, 2.5, 2.5)
        frac = min(1.0, self._position / self._duration)
        painter.setBrush(ACCENT() if not self._paused else GOLD())
        painter.drawRoundedRect(
            QRectF(bar.x(), bar.y(), bar.width() * frac, bar.height()),
            2.5, 2.5)

        painter.setPen(TEXT())
        painter.setFont(QFont('Segoe UI', 9, QFont.Bold))
        painter.drawText(
            QRectF(12, self.height() - 26, self.width() - 24, 20),
            Qt.AlignLeft, '%02d:%02d / %02d:%02d' % (
                int(self._position) // 60, int(self._position) % 60,
                int(self._duration) // 60, int(self._duration) % 60))
        state = self._state_text or ('暂停中' if self._paused else '演奏中')
        painter.setPen(MUTED())
        painter.drawText(
            QRectF(12, self.height() - 26, self.width() - 24, 20),
            Qt.AlignRight, state)
