"""乐谱编辑器：小节线、拍号、可拖拽的音符、碎片合并。

编辑的不是「按键序列」而是「乐谱本身」—— 每个音符有音高、起点、时值，
小节线由 BPM 与拍号算出。用户改完之后由 `arrange_from_notes()` 重算指法，
所以「编辑」和「演奏」始终是同一份数据。

交互一览（刻意贴着音频编辑器的习惯，用户不用重新学）：

    单击            选中（Shift 加选/减选 —— 多选音阶就靠它）
    拖音符主体      移动时间与音高
    拖音符两端      改时值（拖左端时终点钉死）
    空白拖动        框选
    双击空白        插入音符
    双击音符        在点击处切成两半
    滚轮 / Shift+滚轮  左右滚动
    Ctrl+滚轮       缩放
    中键拖动        平移视图
    方向键          上下调音高、左右调时间（Shift 一次八度 / 一格）
    Delete          删除选中（可多选后批量删）
    「整段删除」按钮  时间轴上点两次 → 删掉这段时间里的所有音符（留白保留）
    「剪掉时间」按钮  时间轴上点两次 → 删音符且后面的音符前移补位
    Ctrl+Z / Ctrl+Shift+Z  撤销 / 重做
    Ctrl+S           保存编辑结果
    Ctrl+A          全选
    M               合并选中（碎片并成长音）

绘制沿用 `PianoRoll` 的坐标换算与配色常量，两个视图必须长得一样 ——
用户在预览里看到的旋律轮廓，切到编辑视图不能变个样。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout)

from .theme import C
from .widgets import PianoRoll, mono_font, ui_font
from ..edit import (MIN_NOTE_SEC, SCALE_LABELS, EditDoc, fragment_report)
from ..instrument import note_name


class _UndoKeyFilter(QObject):
    """输入框层面的撤销/重做按键改道。

    QSpinBox/QLineEdit 对 Ctrl+Z / Ctrl+Y 发出 ShortcutOverride 并接受
    （它们有框内文字撤销），QShortcut 因此永远收不到这两个键。这个
    过滤器装在具体输入框上，在事件进入控件之前把撤销/重做按键截下来
    改道到乐谱编辑器。"""

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            key = event.key()
            ctrl = bool(event.modifiers() & Qt.ControlModifier)
            if ctrl and key in (Qt.Key_Z, Qt.Key_Y):
                if key == Qt.Key_Y or (event.modifiers() & Qt.ShiftModifier):
                    self._panel.editor.redo()
                else:
                    self._panel.editor.undo()
                return True
        return False


#: 选中态用主题绿。整个界面里「主色 = 可操作」一直是一致的，
#: 换别的颜色会和「演奏」按钮抢注意力。
#: 整段删除的预览色用暖红：这是唯一会「成片杀音符」的操作，
#: 视觉上必须和绿色的选择/框选拉开距离，避免手滑时看起来像普通选择。

#: 多长的音算「长音」。剪掉时间时，跨越剪口又超过这个时长的音符要单独
#: 点名警告 —— 它们被删掉最不容易被察觉（余音本来就长），删完整句都空。
LONG_NOTE_SEC = 1.0


class ScoreEditor(PianoRoll):
    """可编辑卷帘。"""

    #: 文档被改动（需要重算编排并刷新预览）
    changed = Signal()
    #: 选中的音符数变了
    selectionChanged = Signal(int)
    #: 给状态栏用的一句话
    statusMessage = Signal(str)
    #: Ctrl+S 保存（工具栏按钮与快捷键共用）
    saveRequested = Signal()

    #: 音符两端多宽的范围算「抓到了边缘」
    EDGE_GRAB_PX = 7.0
    #: 缩放范围。下限要给到很小 —— 「适应窗口」要把三个多小时的曲子塞进
    #: 一屏，取 0.35 的话最多只能显示 28 秒。
    ZOOM_MIN = 0.008
    ZOOM_MAX = 12.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self._doc: Optional[EditDoc] = None
        self._sel: set = set()          # 存 note.uid：编辑会重排列表，索引和对象身份都不稳
        self._drag = None
        self._band = None
        self._snap = 0.25               # 吸附网格，单位是四分音符；0 = 关闭
        self._zoom = 1.0
        self._rev = 0                   # 静态层缓存的失效计数
        self._drag_hint = ''
        #: 时间轴点选模式。_range_pick=True 时 _pick_action 决定含义：
        #: 'delete' = 整段删除（只删音符，留白保留）；'cut' = 剪掉时间
        #: （删音符且把后面的音符前移补位，时间轴缩短）。
        self._range_pick = False
        self._pick_action = 'delete'
        self._range: Optional[tuple] = None

    # ------------------------------------------------------------------
    # 文档
    # ------------------------------------------------------------------

    def doc(self) -> Optional[EditDoc]:
        return self._doc

    def set_doc(self, doc: Optional[EditDoc], keep_view: bool = False) -> None:
        self._doc = doc
        self._sel.clear()
        self._drag = None
        self._band = None
        self._drag_hint = ''
        self._range_pick = False
        self._range = None
        if not keep_view:
            self._zoom = 1.0
            self._view_left = 0.0
        self.reframe()
        self.selectionChanged.emit(0)
        self.update()

    def refresh(self, reframe: bool = False) -> None:
        """文档被外部改动后重新取景。"""
        if reframe:
            self.reframe()
        self._rev += 1
        self._invalidate_static()
        self.update()

    def reframe(self) -> None:
        """按内容调整音高范围与总时长。"""
        if self._doc is None or not len(self._doc):
            self._lo, self._hi = 48, 84
            self._duration = 0.0
        else:
            _, t1, lo, hi = self._doc.bounds()
            pad = max(2, (hi - lo) // 10)
            self._lo = max(0, lo - pad)
            self._hi = min(127, hi + pad)
            self._duration = max(t1, 0.001)
        self._clamp_view()
        self._invalidate_static()

    def _clamp_view(self) -> None:
        span = self._span()
        limit = max(self._duration - span, 0.0)
        self._view_left = max(0.0, min(self._view_left, limit))

    # -- 缩放 --

    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, value: float, anchor: Optional[float] = None) -> None:
        """缩放。anchor 是保持不动的那个时间点（默认可见窗中点）。"""
        if self._doc is None:
            return
        old_span = self._span()
        anchor = (self._view_left + old_span / 2.0) if anchor is None else anchor
        frac = 0.5 if old_span <= 0 else (anchor - self._view_left) / old_span
        self._zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, float(value)))
        new_span = self._span()
        self._view_left = anchor - frac * new_span
        self._clamp_view()
        self._invalidate_static()
        self.update()

    def zoom_fit(self) -> None:
        """缩到刚好放下全曲。"""
        if self._doc is None or self._duration <= 0:
            return
        self._zoom = max(self.ZOOM_MIN,
                         min(self.ZOOM_MAX, self.VIEW_SPAN / self._duration))
        self._view_left = 0.0
        self._clamp_view()
        self._invalidate_static()
        self.update()

    def _span(self) -> float:
        if self._duration <= 0:
            return 1.0
        return min(self.VIEW_SPAN / self._zoom, self._duration)

    # -- 吸附 --

    def snap_grid(self) -> float:
        return self._snap

    def set_snap_grid(self, grid: float) -> None:
        self._snap = max(0.0, float(grid))
        self._invalidate_static()
        self.update()

    def _snap_seconds(self) -> float:
        """当前网格对应的秒数（0 表示关闭吸附）。"""
        if not self._snap or self._doc is None:
            return 0.0
        return self._doc.context().beat_sec * self._snap

    def _apply_snap(self, t: float) -> float:
        if not self._snap or self._doc is None:
            return t
        return self._doc.context().snap(t, self._snap)

    # ------------------------------------------------------------------
    # 选区
    # ------------------------------------------------------------------

    def selected_notes(self) -> List:
        if self._doc is None:
            return []
        return [n for n in self._doc.notes if n.uid in self._sel]

    def selected_indices(self) -> List[int]:
        if self._doc is None:
            return []
        return [i for i, n in enumerate(self._doc.notes) if n.uid in self._sel]

    def selection_count(self) -> int:
        return len(self._sel)

    def select_all(self) -> None:
        if self._doc is None:
            return
        self._sel = {n.uid for n in self._doc.notes}
        self._emit_selection()
        self.update()

    def clear_selection(self) -> None:
        self._sel.clear()
        self._emit_selection()
        self.update()

    def select_indices(self, indices) -> None:
        if self._doc is None:
            return
        self._sel = {self._doc.notes[i].uid for i in indices
                     if 0 <= i < len(self._doc.notes)}
        self._emit_selection()
        self.update()

    def _emit_selection(self) -> None:
        self.selectionChanged.emit(len(self._sel))

    def _prune_selection(self) -> None:
        if self._doc is None:
            self._sel.clear()
            return
        live = {n.uid for n in self._doc.notes}
        self._sel &= live

    # ------------------------------------------------------------------
    # 坐标
    # ------------------------------------------------------------------

    def _row_height(self) -> float:
        """一行音的像素高度。

        父类用的是 height/(hi-lo)，而行数其实是 hi-lo+1，两者不自洽：
        lo 那一行会被算到画布外面去。编辑器必须能精确反解「鼠标在哪一行」，
        所以这里重写成自洽的版本，并让 _pitch_to_y 用同一个行高。
        """
        r = self._plot_rect()
        return r.height() / max(self._hi - self._lo + 1, 1)

    def _pitch_to_y(self, pitch: int) -> float:
        """音高行的顶部像素。"""
        return self._plot_rect().top() + (self._hi - pitch) * self._row_height()

    def _y_to_pitch(self, y: float) -> int:
        """_pitch_to_y 的严格反函数（点击/拖动定位靠它准）。"""
        row_h = self._row_height()
        if row_h <= 0:
            return self._lo
        row = math.floor((y - self._plot_rect().top()) / row_h)
        return int(max(0, min(127, self._hi - row)))

    def _edge_at(self, note, x: float) -> Optional[str]:
        """x 落在音符的哪个边缘（用于改时值）。"""
        x0 = self._time_to_x(note.start)
        x1 = self._time_to_x(note.end)
        if (x1 - x0) < self.EDGE_GRAB_PX * 2.2:
            return None                  # 太窄，只能整体拖动
        if abs(x - x0) <= self.EDGE_GRAB_PX:
            return 'left'
        if abs(x - x1) <= self.EDGE_GRAB_PX:
            return 'right'
        return None

    def _hit(self, pos) -> int:
        """鼠标下的音符下标，-1 表示没有。"""
        if self._doc is None:
            return -1
        t = self._x_to_time(pos.x())
        pitch = self._y_to_pitch(pos.y())
        row_h = self._row_height()
        tol = self._span() * (4.0 / max(self._plot_rect().width(), 1.0))
        best, best_d = -1, 1e9
        for i, n in enumerate(self._doc.notes):
            if n.pitch != pitch:
                continue
            # 命中判定要和视觉一致：音符条本身只占行高的一半
            if not (n.start - tol <= t <= n.end + tol):
                continue
            d = abs(t - (n.start + n.end) / 2.0)
            if d < best_d:
                best, best_d = i, d
        return best

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------

    def _paint_empty(self, p: QPainter) -> None:
        p.setPen(QColor(C.MUTED))
        p.setFont(ui_font(10))
        p.drawText(self.rect(), Qt.AlignCenter,
                   '在左侧选一首曲子，然后在这里编辑它的乐谱')

    def _layer_key(self):
        """静态层缓存键。

        注意不能叫 _static_key —— 那是父类 PianoRoll 用来存缓存键的**属性**，
        子类定义一个同名方法会被父类 __init__ 里的赋值直接盖掉。
        """
        doc = self._doc
        return (self.width(), self.height(), self._lo, self._hi,
                len(doc.notes) if doc else 0,
                round(self._view_left, 6), round(self._span(), 6),
                round(doc.bpm, 3) if doc else 0,
                doc.time_sig_num if doc else 0,
                doc.time_sig_den if doc else 0,
                round(doc.phase, 6) if doc else 0,
                round(self._snap, 4), self._rev, C.mode,
                # 选中集合进缓存键：音符（含选中态）画在静态层里，
                # 不把选区算进去的话，选中/取消选中后画面不会重建 ——
                # 高亮要么迟到要么根本不出现。
                hash(frozenset(self._sel)))

    def _static_layer(self):
        key = self._layer_key()
        if self._static is not None and self._static_key == key:
            return self._static

        pm = self._pixmap_for_size()
        pm.fill(QColor(C.ROLL_BG))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        plot = self._plot_rect()
        row_h = self._row_height()
        self._paint_rows(p, plot, row_h)
        self._paint_grid(p, plot)            # 拍网格
        self._paint_bar_lines(p, plot)       # 小节线（在拍线之上）
        self._paint_notes(p, plot, row_h)
        self._paint_axis(p, plot)
        self._paint_gutter(p, plot, row_h)
        p.end()

        self._static = pm
        self._static_key = key
        return pm

    def _pixmap_for_size(self):
        from PySide6.QtGui import QPixmap
        return QPixmap(self.size())

    def _paint_grid(self, p: QPainter, plot: QRectF) -> None:
        """拍网格：比小节线淡一档，只画到当前缩放看得见的程度。"""
        if self._doc is None:
            return
        step = self._snap_seconds()
        if step <= 0:
            beat = self._doc.context().beat_sec
            step = beat if beat * self._pixels_per_second() >= 26 else beat * 4
        if step <= 0 or step * self._pixels_per_second() < 6:
            return

        p.save()
        p.setClipRect(plot)
        p.setPen(QPen(QColor(C.EDIT_BEAT), 1))
        right_t = self._view_left + self._span()
        t = math.floor(self._view_left / step) * step
        while t <= right_t + 1e-6:
            if t > -1e-6:
                x = self._time_to_x(t)
                p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            t += step
        p.restore()

    def _paint_bar_lines(self, p: QPainter, plot: QRectF) -> None:
        """小节线 + 小节号。

        小节号画在顶部 —— 没有它，用户没法说「第 5 小节那个音不对」，
        编辑器的可沟通性会差很多。
        """
        if self._doc is None:
            return
        ctx = self._doc.context()
        bar = ctx.bar_sec
        if bar <= 0:
            return
        p.save()
        p.setClipRect(plot)
        right_t = self._view_left + self._span()
        first = int(math.floor((self._view_left - ctx.phase) / bar))
        last = int(math.ceil((right_t - ctx.phase) / bar)) + 1
        p.setFont(mono_font(8))
        for i in range(first, last + 1):
            t = ctx.phase + i * bar
            if t < -1e-9:
                continue
            x = self._time_to_x(t)
            if x < plot.left() - 20 or x > plot.right() + 20:
                continue
            strong = (i % 4 == 0)
            p.setPen(QPen(QColor(C.EDIT_BAR), 1.6 if strong else 1.0))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            if t >= self._view_left - 1e-6:
                p.setPen(QColor(C.EDIT_BAR_NUM) if strong else QColor(C.MUTED))
                p.drawText(QPointF(x + 3, plot.top() + 11),
                           '%d' % (i + 1))
        p.restore()

    def _paint_notes(self, p: QPainter, plot: QRectF, row_h: float) -> None:
        if self._doc is None:
            return
        p.save()
        p.setClipRect(plot)
        h = max(row_h * 0.52, self.MIN_NOTE_H)
        radius = 2.0
        left_t = self._view_left
        right_t = self._view_left + self._span()

        for n in self._doc.notes:
            if n.end < left_t or n.start > right_t:
                continue
            x0 = self._time_to_x(n.start)
            x1 = self._time_to_x(n.end)
            w = max(x1 - x0, 3.0)
            y = self._pitch_to_y(n.pitch) + (row_h - h) / 2.0
            rect = QRectF(x0, y, w, h)
            sel = n.uid in self._sel

            if sel:
                # 选中态要一眼可辨：外圈金色光晕 + 亮色描边 + 饱和填充。
                # 只换填充色不够 —— 音符本身也是金色系，深浅两套主题里
                # 都几乎没有区分度。
                halo = QColor(C.ACCENT)
                halo.setAlpha(85)
                p.setPen(QPen(halo, 3.2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5),
                                  radius + 1.5, radius + 1.5)
                p.setPen(QPen(QColor(C.TEXT), 1.7))
                p.setBrush(QBrush(QColor(C.ACCENT)))
                p.drawRoundedRect(rect, radius, radius)
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor(C.ROLL_NOTE)))
                p.drawRoundedRect(rect, radius, radius)

            if n.tie:
                # 延音线的后半段：左端一小截暗色，表示「接着上一个音」
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor(C.EDIT_TIE)))
                p.drawRoundedRect(QRectF(x0, y, min(3.0, w), h), 1.0, 1.0)
            if sel and w >= 8.0:
                # 选中态给两端的把手一点暗示，用户才知道可以拖边缘
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor(C.ACCENT_DEEP)))
                p.drawRect(QRectF(x0 + 1.0, y + h * 0.28, 1.6, h * 0.44))
                p.drawRect(QRectF(x1 - 2.6, y + h * 0.28, 1.6, h * 0.44))
        p.restore()

    def _pixels_per_second(self) -> float:
        span = self._span()
        return self._plot_rect().width() / span if span > 0 else 1.0

    def paintEvent(self, event):
        p = QPainter(self)
        plot = self._plot_rect()

        if self._doc is None or not len(self._doc):
            p.fillRect(self.rect(), QColor(C.ROLL_BG))
            self._paint_empty(p)
            return

        p.drawPixmap(0, 0, self._static_layer())
        self._paint_playhead(p, plot)
        self._paint_range(p, plot)
        self._paint_band(p, plot)
        self._paint_hover(p, plot)
        self._paint_drag_hint(p, plot)

    def _paint_band(self, p: QPainter, plot: QRectF) -> None:
        if self._band is None:
            return
        x0, y0, x1, y1 = self._band
        rect = QRectF(min(x0, x1), min(y0, y1),
                      abs(x1 - x0), abs(y1 - y0))
        p.save()
        p.setClipRect(plot)
        fill = QColor(QColor(C.ACCENT))
        fill.setAlpha(46)
        p.setPen(QPen(QColor(C.ACCENT), 1, Qt.DashLine))
        p.setBrush(QBrush(fill))
        p.drawRect(rect)
        p.restore()

    def _paint_drag_hint(self, p: QPainter, plot: QRectF) -> None:
        """拖动时在光标旁显示目标时间 / 音高 —— 拖到哪要心里有数。"""
        if not self._drag_hint:
            return
        p.save()
        p.setFont(mono_font(8))
        w = p.fontMetrics().horizontalAdvance(self._drag_hint) + 12
        x = min(max(self._hover_x + 10, plot.left()), plot.right() - w)
        y = plot.top() + 4
        box = QRectF(x, y, w, 17)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(C.ACCENT_DEEP)))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QColor(C.ACCENT))
        p.drawText(box, Qt.AlignCenter, self._drag_hint)
        p.restore()

    # ------------------------------------------------------------------
    # 鼠标
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if self._doc is None or not len(self._doc):
            return super().mousePressEvent(event)
        self.setFocus(Qt.MouseFocusReason)
        pos = event.position()

        # 整段删除 / 剪掉时间模式下：点画布**任意位置**都算选时间点。
        # 只认底部 24px 的时间轴条太窄，用户点不准（需求：放宽选点区域）。
        if event.button() == Qt.LeftButton and self._range_pick:
            self._pick_time_point(self._x_to_time(pos.x()))
            self.update()
            return

        # 底部时间轴条（非点选模式）：跳转播放位置。
        # 编辑操作都发生在绘图区里，点刻度上不该顺手画出一个框选框。
        if event.button() == Qt.LeftButton \
                and pos.y() > self._plot_rect().bottom():
            self.seekRequested.emit(self._x_to_time(pos.x()))
            self.update()
            return

        # 整段删除模式下右键 = 取消（和 Esc 等效，编辑软件的惯例）
        if event.button() == Qt.RightButton and self._range_pick:
            self.cancel_range_pick()
            return

        if event.button() == Qt.MiddleButton:
            self._drag = {'mode': 'pan', 'x0': pos.x(),
                          'left0': self._view_left, 'moved': False}
            self.setCursor(Qt.ClosedHandCursor)
            return

        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)

        index = self._hit(pos)
        shift = bool(event.modifiers() & Qt.ShiftModifier)

        if index >= 0:
            note = self._doc.notes[index]
            nid = note.uid
            if shift:
                self._sel.symmetric_difference_update({nid})
            elif nid not in self._sel:
                self._sel = {nid}
            self._emit_selection()

            edge = self._edge_at(note, pos.x())
            mode = ('resize-left' if edge == 'left' else
                    'resize-right' if edge == 'right' else 'move')
            self._begin_drag(mode, index, pos)
        else:
            if not shift:
                self._sel.clear()
                self._emit_selection()
            # Shift 状态在按下时记下来：松开鼠标的那一刻修饰键可能已经
            # 抬起来了（用户先松 Shift 再松鼠标），那时再读就是错的。
            self._band_shift = shift
            self._band = (pos.x(), pos.y(), pos.x(), pos.y())
        self.update()

    def _begin_drag(self, mode: str, index: int, pos) -> None:
        notes = self.selected_notes()
        if not notes:
            return
        anchor = self._doc.notes[index]
        self._drag = {
            'mode': mode,
            'anchor': self._doc.notes[index].uid,
            't0': self._x_to_time(pos.x()),
            'p0': self._y_to_pitch(pos.y()),
            'orig': {n.uid: (n.start, n.duration, n.pitch) for n in notes},
            'moved': False,
        }
        if mode == 'move':
            self._drag['anchor'] = anchor.uid
        else:
            # 改时值只作用于被按住的那个音：边缘拖动是精确操作，
            # 顺手把整组都改掉不符合直觉。
            self._drag['orig'] = {anchor.uid: (anchor.start, anchor.duration,
                                               anchor.pitch)}
            self._sel = {anchor.uid}
            self._emit_selection()
        self._doc.begin_drag()

    def mouseMoveEvent(self, event):
        pos = event.position()
        self._hover_x = pos.x()

        # 框选必须放在 _drag 判断之前：框选期间 _drag 是 None，先判 _drag
        # 的话这一支永远不会执行，框选框就画不出来、也选不中东西。
        if self._band is not None:
            x0, y0, _, _ = self._band
            self._band = (x0, y0, pos.x(), pos.y())
            self.update()
            return

        if self._drag is None:
            self._update_cursor(pos)
            self.update()
            return super().mouseMoveEvent(event)

        mode = self._drag['mode']
        if mode == 'pan':
            dx = pos.x() - self._drag['x0']
            pps = self._pixels_per_second()
            self._view_left = self._drag['left0'] - dx / max(pps, 1e-6)
            self._clamp_view()
            self._invalidate_static()
            self.update()
            return

        self._apply_drag(pos)

    def _apply_drag(self, pos) -> None:
        """按「当前鼠标位置相对按下点」的总位移重算目标值。

        每次都从按下时的原值重算，而不是在上一帧的结果上累加 ——
        累加会把吸附误差一点点攒起来，拖远之后音符会自己飘走。
        """
        drag = self._drag
        doc = self._doc
        by_id = {n.uid: n for n in doc.notes}

        dt = self._x_to_time(pos.x()) - drag['t0']
        dp = self._y_to_pitch(pos.y()) - drag['p0']
        mode = drag['mode']
        orig = drag['orig']
        anchor_id = drag['anchor']
        anchor = orig.get(anchor_id)
        if anchor is None:
            return
        a_start, a_dur, a_pitch = anchor

        if mode == 'move':
            # 拖了就吸附。按下时的位置不在网格上会有一个小突跳，
            # 这是所有宿主软件的一致行为，保持一致比自作聪明好。
            target = max(0.0, self._apply_snap(a_start + dt))
            dtime = target - a_start
            hint = '%s  %+.3fs' % (note_name(max(0, min(127, a_pitch + dp))), dtime)
        else:
            edge_t = (a_start + a_dur + dt) if mode == 'resize-right' \
                else (a_start + dt)
            snapped = self._apply_snap(edge_t)
            if mode == 'resize-right':
                dtime = max(snapped - (a_start + a_dur),
                            MIN_NOTE_SEC - a_dur)
                hint = '时值 %.3fs' % max(a_dur + dtime, MIN_NOTE_SEC)
            else:
                limit = a_start + a_dur - MIN_NOTE_SEC
                dtime = min(snapped - a_start, limit - a_start)
                hint = '起点 %.3fs' % max(a_start + dtime, 0.0)

        for nid, (s0, d0, p0) in orig.items():
            n = by_id.get(nid)
            if n is None:
                continue
            if mode == 'move':
                n.start = max(0.0, round(s0 + dtime, 6))
                n.pitch = max(0, min(127, p0 + dp))
            elif mode == 'resize-right':
                n.duration = max(MIN_NOTE_SEC, round(d0 + dtime, 6))
            else:
                new_start = max(0.0, round(s0 + dtime, 6))
                n.start = new_start
                n.duration = max(MIN_NOTE_SEC,
                                 round(s0 + d0 - new_start, 6))

        drag['moved'] = True
        self._drag_hint = hint
        self._rev += 1
        self._invalidate_static()
        self.update()

    def _update_cursor(self, pos) -> None:
        if self._doc is None or not len(self._doc):
            return
        index = self._hit(pos)
        if index < 0:
            self.setCursor(Qt.ArrowCursor)
            return
        edge = self._edge_at(self._doc.notes[index], pos.x())
        self.setCursor(Qt.SizeHorCursor if edge else Qt.OpenHandCursor)

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            mode = self._drag['mode']
            moved = self._drag['moved']
            self._drag = None
            self.unsetCursor()
            self._drag_hint = ''

            if mode == 'pan':
                if not moved:
                    self._clamp_view()
                self.update()
                return

            changed = self._doc.commit_drag() if moved else False
            if not changed:
                self._doc.cancel_drag()
            else:
                self.reframe()
                self.changed.emit()
            self._rev += 1
            self._invalidate_static()
            self.update()
            return

        if self._band is not None:
            x0, y0, x1, y1 = self._band
            self._band = None
            if abs(x1 - x0) > 3 or abs(y1 - y0) > 3:
                self._select_band(x0, y0, x1, y1)
            self.update()
            return

        super().mouseReleaseEvent(event)

    def _select_band(self, x0: float, y0: float, x1: float, y1: float) -> None:
        t0, t1 = self._x_to_time(x0), self._x_to_time(x1)
        p_a, p_b = self._y_to_pitch(y0), self._y_to_pitch(y1)
        idx = self._doc.notes_in_rect(t0, t1, p_a, p_b)
        shift = bool(self._band_shift)
        picked = {self._doc.notes[i].uid for i in idx}
        self._sel = (self._sel | picked) if shift else picked
        self._emit_selection()

    #: 框选时是否按住 Shift（在按下时记录，因为 release 时修饰键可能已松）
    _band_shift = False

    def mouseDoubleClickEvent(self, event):
        if self._doc is None or not len(self._doc):
            return
        pos = event.position()
        index = self._hit(pos)

        if index >= 0:
            note = self._doc.notes[index]
            at = self._apply_snap(self._x_to_time(pos.x()))
            made = self._doc.split(index, at)
            if len(made) > 1:
                self._after_edit('已切开')
            return

        pitch = self._y_to_pitch(pos.y())
        start = max(0.0, self._apply_snap(self._x_to_time(pos.x())))
        dur = self._snap_seconds() or 0.25
        idx = self._doc.insert(pitch, start, dur)
        if idx >= 0:
            self._sel = {self._doc.notes[idx].uid}
            self._emit_selection()
            self._after_edit('已插入 %s' % note_name(pitch))

    # ------------------------------------------------------------------
    # 滚轮 / 键盘
    # ------------------------------------------------------------------

    def wheelEvent(self, event):
        if self._doc is None:
            return super().wheelEvent(event)
        delta = event.angleDelta().y() or event.angleDelta().x()
        mods = event.modifiers()

        if mods & Qt.ControlModifier:
            self.set_zoom(self._zoom * (1.25 if delta > 0 else 0.8),
                          anchor=self._x_to_time(event.position().x()))
            return
        # 普通滚轮左右滚动：编辑器里竖直方向没有可滚的东西，
        # 让它横向走比什么都不做顺手得多。
        pps = max(self._pixels_per_second(), 1e-6)
        self._view_left -= (delta / 120.0) * (60.0 / pps) * 1.5
        self._clamp_view()
        self._invalidate_static()
        self.update()

    def keyPressEvent(self, event):
        if self._doc is None:
            return super().keyPressEvent(event)
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.ControlModifier)
        shift = bool(mods & Qt.ShiftModifier)
        step = self._snap_seconds() or 0.125

        if ctrl and key == Qt.Key_Z:
            (self._doc.redo() if shift else self._doc.undo())
            self._after_history('已重做' if shift else '已撤销')
            return
        if ctrl and key == Qt.Key_Y:
            self._doc.redo()
            self._after_history('已重做')
            return
        if ctrl and key == Qt.Key_A:
            self.select_all()
            return
        if ctrl and key == Qt.Key_S:
            self.saveRequested.emit()
            return

        idx = self.selected_indices()
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            if idx:
                n = self._doc.delete(idx)
                self._after_edit('已删除 %d 个音符' % n)
            return
        if key == Qt.Key_Escape:
            if self._range_pick:
                self.cancel_range_pick()
                return
            self.clear_selection()
            return
        if key in (Qt.Key_Left, Qt.Key_Right) and idx:
            d = -step if key == Qt.Key_Left else step
            self._doc.move(idx, d)
            self._after_edit('')
            return
        if key in (Qt.Key_Up, Qt.Key_Down) and idx:
            d = 12 if shift else 1
            d = -d if key == Qt.Key_Down else d
            self._doc.transpose(idx, d)
            self._after_edit('')
            return
        if key == Qt.Key_M and not ctrl:
            if len(idx) >= 2:
                self.merge_selection()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # 编辑命令（工具栏与快捷键共用）
    # ------------------------------------------------------------------

    def _after_edit(self, message: str) -> None:
        self._prune_selection()
        self.reframe()
        self.changed.emit()
        if message:
            self.statusMessage.emit(message)

    def _after_history(self, action: str) -> None:
        self._prune_selection()
        self.reframe()
        self.changed.emit()
        self.statusMessage.emit(action)

    def merge_selection(self, by_pitch: bool = True) -> int:
        """把选中的碎片合并成长音。

        没有选中任何音符时自动转为「合并整首的碎片」—— 用户想的就是
        「把我这首曲子的碎片收拾一下」，让他先框选反而是多一道手续。
        返回合并后剩下的音符数。
        """
        idx = self.selected_indices()
        if not idx:
            return self.merge_all_fragments()
        if len(idx) < 2:
            self.statusMessage.emit('至少选中两个音符才能合并')
            return 0
        before = len(idx)
        kept = self._doc.merge(idx, by_pitch=by_pitch)
        self._sel = {self._doc.notes[i].uid for i in kept
                     if 0 <= i < len(self._doc.notes)}
        self._emit_selection()
        self._after_edit('已把 %d 个音符合并成 %d 个' % (before, len(kept)))
        return len(kept)

    def merge_all_fragments(self) -> int:
        """全曲碎片合并 —— 长音被切碎时的一键补救。"""
        if self._doc is None or not len(self._doc):
            return 0
        rep = fragment_report(self._doc)
        if not rep['groups']:
            self.statusMessage.emit('没有发现可以合并的碎片')
            return 0
        self._doc.merge(range(len(self._doc.notes)), by_pitch=True)
        self.clear_selection()
        self._after_edit('合并了 %d 组碎片，减少 %d 个音符'
                         % (rep['groups'], rep['extra']))
        return rep['extra']

    def quantize_selection(self, grid: Optional[float] = None) -> None:
        idx = self.selected_indices() or list(range(len(self._doc.notes)))
        self._doc.quantize(idx, grid if grid is not None else (self._snap or 0.25))
        self._after_edit('已量化到网格')

    def snap_selection_to_scale(self) -> None:
        idx = self.selected_indices() or list(range(len(self._doc.notes)))
        if self._doc.scale == 'chromatic':
            self.statusMessage.emit('当前是半音阶，没有可吸附的调内音')
            return
        self._doc.snap_to_scale(idx)
        self._after_edit('已吸附到 %s' % SCALE_LABELS.get(self._doc.scale,
                                                          self._doc.scale))

    def fix_overlaps(self) -> None:
        n = self._doc.fix_overlaps()
        if n:
            self._after_edit('清理了 %d 处重叠' % n)
        else:
            self.statusMessage.emit('没有发现重叠')

    def delete_selection(self) -> None:
        idx = self.selected_indices()
        if idx:
            self._after_edit('已删除 %d 个音符' % self._doc.delete(idx))

    # ------------------------------------------------------------------
    # 整段删除：时间轴上点两次，删除这段时间里的所有音符
    # ------------------------------------------------------------------

    def begin_range_pick(self, action: str = 'delete') -> None:
        """进入（或退出/切换）时间轴点选模式。

        action='delete' 是整段删除（只删音符），'cut' 是剪掉时间
        （删音符且把后面的音符前移补位）。再次点击同一按钮取消；
        直接点另一个按钮则切换模式。
        """
        if self._doc is None or not len(self._doc):
            self.statusMessage.emit('先选一首曲子，再使用时间轴点选')
            return
        if action not in ('delete', 'cut'):
            action = 'delete'
        if self._range_pick and self._pick_action == action:
            self.cancel_range_pick()
            return
        self._range_pick = True
        self._pick_action = action
        self._range = None
        self.setFocus(Qt.MouseFocusReason)
        if action == 'cut':
            self.statusMessage.emit(
                '剪掉时间：在画布上点第 1 个位置（Esc 取消）\n'
                '剪口里的音符（含跨在剪口上的长音）会一起删掉 —— '
                '选第二点前先看清，动手时还会再确认一次')
        else:
            self.statusMessage.emit(
                '整段删除：在画布上点第 1 个位置（任何地方都可以，Esc 取消）\n'
                '看不见整段就先用缩放收小视图')
        self.update()

    def cancel_range_pick(self) -> None:
        self._range_pick = False
        self._range = None
        self.statusMessage.emit('已取消时间轴点选')
        self.update()

    def _pick_time_point(self, t: float) -> None:
        """点选模式下记录一个时间点：第一次记起点，第二次执行动作。"""
        if self._range is None:
            self._range = (t,)
            if self._pick_action == 'cut':
                self.statusMessage.emit(
                    '起点 %.2fs —— 再点一个位置，剪掉并前移补位（Esc 取消）' % t)
            else:
                self.statusMessage.emit(
                    '起点 %.2fs —— 再点一个位置作为终点（Esc 取消）' % t)
        else:
            a, b = sorted((self._range[0], t))
            self._range_pick = False
            self._range = None
            if self._pick_action == 'cut':
                self._cut_time_range(a, b)
            else:
                self._delete_in_range(a, b)

    def _delete_in_range(self, a: float, b: float) -> None:
        """删除与 [a, b] 有重叠的所有音符（Ctrl+Z 可撤销）。"""
        idx = [i for i, n in enumerate(self._doc.notes)
               if n.start < b and n.end > a]
        if not idx:
            self.statusMessage.emit('%.2fs ~ %.2fs 之间没有音符' % (a, b))
            self.update()
            return
        n = self._doc.delete(idx)
        self._after_edit('整段删除了 %d 个音符（%.2fs ~ %.2fs）—— Ctrl+Z 可撤销'
                         % (n, a, b))

    def _cut_time_range(self, a: float, b: float, confirm: bool = True) -> None:
        """剪掉 [a, b] 这段时间：段内音符删除 + 后面的音符前移补位。

        剪之前要确认，因为「剪掉一段时间」听起来像只动时间轴，实际会连带
        删掉这段时间里的音符 —— 尤其是**跨越剪口的长音**：用户选的两个点
        只要有一个落在长音里，整个长音就没了（还会把后面的音符整体前移，
        听感上等于整段错位）。这正是用户报过的「尾音很长…删空白后还是
        出问题」的来源，所以这里必须把代价说清楚再动手。
        """
        a, b = min(a, b), max(a, b)
        if abs(b - a) < 1e-9:
            self.statusMessage.emit('%.2fs ~ %.2fs 之间没有内容' % (a, b))
            self.update()
            return
        drop, moved = self._cut_preview(a, b)
        if confirm and drop and not self._confirm_cut(a, b, drop, moved):
            self.statusMessage.emit('已取消剪掉时间')
            self.update()
            return
        n = self._doc.cut_time(a, b)
        note = '剪掉了 %.2f 秒（删除 %d 个音符），后面的音符已前移' % (b - a, n)
        long_ones = [x for x in drop
                     if x.duration >= LONG_NOTE_SEC
                     and (x.start < a - 1e-9 or x.end > b + 1e-9)]
        if long_ones:
            note += '；连 %.2f 秒的长音一起删了' % max(x.duration
                                                    for x in long_ones)
        self._after_edit(note + ' —— Ctrl+Z 可撤销')

    def _cut_preview(self, a: float, b: float) -> Tuple[List, List]:
        """剪 [a, b] 会删掉哪些音符、移动哪些音符。

        判定与 EditDoc.cut_time 保持一致（与 [a, b] 相交即删、起点在 b
        之后前移），所以确认框里数出来的就是真正会发生的事。
        """
        doc = self._doc
        if doc is None:
            return [], []
        drop, moved = [], []
        for n in doc.notes:
            if n.start < b and n.end > a:
                drop.append(n)
            elif n.start >= b - 1e-9:
                moved.append(n)
        return drop, moved

    def _confirm_cut(self, a: float, b: float, drop: List,
                     moved: List) -> bool:
        """把「剪掉时间」的代价摆出来，由用户决定继续还是取消。"""
        from PySide6.QtWidgets import QMessageBox
        spanning = [n for n in drop if n.start < a - 1e-9 or n.end > b + 1e-9]
        longest = max(drop, key=lambda n: n.duration)
        lines = ['剪掉 %.2f 秒（%.2fs ~ %.2fs）。' % (b - a, a, b),
                 '这段时间里有 %d 个音符会被一起删掉%s，'
                 '后面的 %d 个音符前移补位。'
                 % (len(drop),
                    '（其中 %d 个跨越剪口）' % len(spanning) if spanning else '',
                    len(moved))]
        if longest.duration >= LONG_NOTE_SEC:
            lines.append('最长的那个有 %.2f 秒（%s）—— 长音的余音很长，'
                         '删掉后这一句听起来会空一截。'
                         % (longest.duration, note_name(longest.pitch)))
        lines.append('想只删空白、一个音符都不碰：先取消，把两个点都点在'
                     '完全没有音符的地方。')
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle('剪掉时间')
        box.setText(lines[0])
        box.setInformativeText('\n'.join(lines[1:]))
        btn_go = box.addButton('继续剪掉', QMessageBox.DestructiveRole)
        box.addButton('取消', QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is btn_go

    def _paint_range(self, p: QPainter, plot: QRectF) -> None:
        """时间轴点选的范围预览：已定起点后，从起点画到鼠标（或终点）。

        颜色按动作区分：整段删除是暖红（成片杀音符），剪掉时间是琥珀
        （删音符 + 时间合拢）。两种破坏性操作都不能长得像绿色选择态。
        """
        if not self._range_pick or self._range is None:
            return
        if self._pick_action == 'cut':
            fill = QColor(C.WARN_FG)
            edge = QColor(C.EDIT_RANGE_EDGE)
            text_col = QColor(C.ROLL_BG)
        else:
            fill = QColor(QColor(C.EDIT_RANGE_FILL))
            edge = QColor(QColor(C.EDIT_RANGE_EDGE))
            text_col = QColor(C.DANGER)
        fill.setAlpha(56)
        a = self._range[0]
        if len(self._range) >= 2:
            b = self._range[1]
        else:
            b = self._x_to_time(self._hover_x) if self._hover_x >= 0 else a
        x0, x1 = sorted((self._time_to_x(a), self._time_to_x(b)))
        rect = QRectF(x0, plot.top(), max(x1 - x0, 2.0), plot.height())
        p.save()
        p.setClipRect(plot)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(fill))
        p.drawRect(rect)
        p.setPen(QPen(edge, 1, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect)
        p.setFont(mono_font(8))
        p.setPen(text_col)
        text = ('%.2fs ~ %.2fs' % (min(a, b), max(a, b)))
        # 画在选段区内部的底部：顶部是小节号的地盘，悬停时间标签也在顶部，
        # 挤在一起谁都看不清；底部紧挨时间轴，正好是用户视线所在。
        p.drawText(QPointF(min(x1 + 4, plot.right() - 96),
                           plot.bottom() - 6), text)
        p.restore()

    def undo(self) -> None:
        if self._doc.undo():
            self._after_history('已撤销')
        else:
            self.statusMessage.emit('没有可撤销的操作')

    def redo(self) -> None:
        if self._doc.redo():
            self._after_history('已重做')
        else:
            self.statusMessage.emit('没有可重做的操作')

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(820, 300)


class EditorToolbar(QFrame):
    """编辑器工具行：编辑动作 + 吸附与缩放 + 乐谱上下文。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('editBar')
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(38)

        from PySide6.QtWidgets import QComboBox

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        # 图标工具条：按钮多，间距收小一档，1080 最小窗口下也放得下
        row.setSpacing(5)

        def button(text: str, tip: str, name: str = '') -> QPushButton:
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            if name:
                b.setObjectName(name)
            row.addWidget(b)
            return b

        # 图标按钮：一行 14 个文字按钮在常见窗口宽度下必然截断
        # （「合并长音」被压成「并长音」），所以编辑动作全部图标化，
        # 悬停出完整提示，状态栏同步显示名称。图标在切主题后要重生成
        # （pixmap 缓存了颜色），见 refresh_icons()。
        from PySide6.QtWidgets import QToolButton

        self._icons = []        # (按钮, 图标工厂) —— 供 refresh_icons 重建

        def ibutton(icon_fn, name: str, tip: str) -> QToolButton:
            b = QToolButton()
            b.setObjectName('iconBtn')
            b.setIcon(icon_fn())
            b.setToolTip(tip)
            b.setStatusTip(name)      # 悬停时状态栏显示动作名
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
            self._icons.append((b, icon_fn))
            return b

        from .theme import (icon_cut, icon_delete, icon_erase_range,
                            icon_insert_gap, icon_insert_scale, icon_merge,
                            icon_overlap, icon_quantize, icon_redo,
                            icon_snap_scale, icon_save, icon_undo)

        # 合并放在最前面：这是用户最常用的一个动作 —— 长音被识别成一串
        # 短音时，框选 + 合并就完事了。金色图标标示它是主操作。
        self.btn_merge = ibutton(
            lambda: icon_merge(color=C.ACCENT),
            '合并长音',
            '合并长音 —— 把碎片音符合并成一个长音（快捷键 M）\n'
            '转录把长音切碎时，这是最直接的补救\n'
            '没选中时：自动合并整首的碎片')
        self.btn_quant = ibutton(
            icon_quantize, '量化',
            '量化 —— 把起点与时值吸附到当前网格\n'
            '没选中时：作用于全曲')
        self.btn_scale = ibutton(
            icon_snap_scale, '调内吸附',
            '调内吸附 —— 把音高吸附到当前调式的音级上\n'
            '转录结果常见的 ±1 半音漂移\n'
            '没选中时：作用于全曲')
        self.btn_overlap = ibutton(
            icon_overlap, '清理重叠',
            '清理重叠 —— 同一音高上时间重叠的音符\n'
            '（游戏一次只能按一个键）\n'
            '没选中时：作用于全曲')
        self.btn_delete = ibutton(
            icon_delete, '删除', '删除 —— 删除选中的音符（Delete 键）')

        row.addSpacing(6)
        row.addWidget(self._label('吸附'))
        self.cb_snap = QComboBox()
        self.cb_snap.setToolTip('拖动音符时吸附到节拍网格')
        for text, value in (('关闭', 0.0), ('1/4 拍', 0.25), ('1/8 拍', 0.125),
                            ('1/16 拍', 0.0625), ('1/32 拍', 0.03125)):
            self.cb_snap.addItem(text, value)
        self.cb_snap.setCurrentIndex(1)
        self.cb_snap.setFixedWidth(88)
        row.addWidget(self.cb_snap)

        row.addSpacing(6)
        # −/适应/+ 三连自解释，不再加「缩放」文字标签（最小宽度下省位置）
        self.btn_zoom_out = button('−', '缩小', 'ghost')
        self.btn_zoom_out.setFixedWidth(30)
        self.btn_zoom_fit = button('适应', '缩到刚好放下全曲', 'ghost')
        self.btn_zoom_fit.setFixedWidth(46)
        self.btn_zoom_in = button('+', '放大', 'ghost')
        self.btn_zoom_in.setFixedWidth(30)

        row.addStretch(1)
        self.btn_undo = ibutton(icon_undo, '撤销', '撤销上一步（Ctrl+Z）')
        self.btn_redo = ibutton(icon_redo, '重做', '重做（Ctrl+Shift+Z）')

        # 区间操作放撤销旁边：删完手就悬在 Ctrl+Z / 撤销键附近。
        row.addSpacing(6)
        self.btn_range = ibutton(
            icon_erase_range, '整段删除',
            '整段删除 —— 删除一段时间内的所有音符（时间轴不变，空白保留）：\n'
            '点这里 → 在画布上点第 1 个位置 → 再点第 2 个（任意位置均可）\n'
            '想连空白一起移除、让后面的音符补位 → 用「剪掉时间」\n'
            '右键 / Esc 取消 · Ctrl+Z 可撤销')
        self.btn_cut = ibutton(
            icon_cut, '剪掉时间',
            '剪掉时间 —— 剪掉一段时间，后面的音符前移补位（时间轴缩短）：\n'
            '点这里 → 在画布上点第 1 个位置 → 再点第 2 个（任意位置均可）\n'
            '和「整段删除」的区别：那个只删音符、留一片空白；这个把空白合拢\n'
            '剪口里的音符会一起删掉，跨在剪口上的长音也是 —— 动手前会先确认\n'
            '右键 / Esc 取消 · Ctrl+Z 可撤销')

        # 插入类操作都挂在播放头上：看着卷帘走到想插入的位置，点按钮即可
        self.btn_gap = ibutton(
            icon_insert_gap, '插入空白',
            '插入空白 —— 在播放头位置插入一段空白，后面的音符整体后移：\n'
            '想多空一拍再进唱、或把某段整体往后挪时用\nCtrl+Z 可撤销')
        self.btn_scale_ins = ibutton(
            icon_insert_scale, '插入音阶',
            '插入音阶 —— 在播放头位置插入一段调内音阶（上行 / 下行 / 上下行）：\n'
            '起始音自动贴近全曲中位音高，每个音的时长可调\nCtrl+Z 可撤销')

        # 保存是编辑视图唯一的落地出口，图标保持金色主色。
        row.addSpacing(6)
        self.btn_save = ibutton(
            lambda: icon_save(color=C.ACCENT), '保存曲谱',
            '保存曲谱 —— 把编辑结果保存进曲库（Ctrl+S）\n'
            '保存的就是你改过的乐谱：音高、时值、小节与拍号一并保存')

    def refresh_icons(self) -> None:
        """切主题后重建图标：pixmap 缓存了旧主题的颜色。"""
        for btn, fn in self._icons:
            btn.setIcon(fn())
        self.update()

    @staticmethod
    def _label(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName('keyHint')
        return lab

    def sync_history(self, can_undo: bool, can_redo: bool) -> None:
        self.btn_undo.setEnabled(can_undo)
        self.btn_redo.setEnabled(can_redo)


#: 十二个音名，用于调号下拉
KEY_NAMES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')

#: 拍号候选。和转录层的推断候选不同：这里允许用户选 2/4 和 6/8 等，
#: 因为人知道自己想要什么，机器猜不出来不代表人定不了。
TIME_SIGS = ((4, 4), (3, 4), (2, 4), (6, 8), (2, 2), (3, 8), (5, 4), (12, 8))


class EditorInfoBar(QFrame):
    """编辑器信息行：左边一句状态，右边乐谱上下文。

    拍号与 BPM 必须能改 —— 转录推断出的拍号本来就有猜错的时候，
    而拍号一改小节线立刻重排，这是编辑器最有用的能力之一。
    """

    bpmChanged = Signal(float)
    timeSigChanged = Signal(int, int)
    keyChanged = Signal(int)
    scaleChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QComboBox, QSpinBox

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(7)

        self.lbl_stats = QLabel('')
        self.lbl_stats.setObjectName('mono')
        row.addWidget(self.lbl_stats)

        self.lbl_frag = QLabel('')
        self.lbl_frag.setObjectName('keyHint')
        row.addWidget(self.lbl_frag)
        row.addStretch(1)

        row.addWidget(EditorToolbar._label('BPM'))
        self.spin_bpm = QSpinBox()
        self.spin_bpm.setRange(30, 300)
        self.spin_bpm.setValue(120)
        self.spin_bpm.setFixedWidth(64)
        # 去掉上下箭头：QSS 没样式化它们，留着会露出原生绘制的灰块。
        self.spin_bpm.setButtonSymbols(QSpinBox.NoButtons)
        self.spin_bpm.setToolTip('速度。改这里会让小节线按新速度重排。')
        self.spin_bpm.setAlignment(Qt.AlignCenter)
        row.addWidget(self.spin_bpm)

        row.addWidget(EditorToolbar._label('拍号'))
        self.cb_ts = QComboBox()
        for num, den in TIME_SIGS:
            self.cb_ts.addItem('%d/%d' % (num, den), (num, den))
        self.cb_ts.setFixedWidth(72)
        self.cb_ts.setToolTip('拍号决定一个小节有多长、小节线画在哪。')
        row.addWidget(self.cb_ts)

        row.addWidget(EditorToolbar._label('调'))
        self.cb_key = QComboBox()
        for i, name in enumerate(KEY_NAMES):
            self.cb_key.addItem(name, i)
        self.cb_key.setFixedWidth(62)
        self.cb_key.setToolTip('调号。「调内吸附」按它把音高吸到音阶上。')
        row.addWidget(self.cb_key)

        self.cb_scale = QComboBox()
        for value, label in SCALE_LABELS.items():
            self.cb_scale.addItem(label, value)
        self.cb_scale.setFixedWidth(112)
        self.cb_scale.setToolTip('调式。选「半音（不吸附）」则不做调内吸附。')
        row.addWidget(self.cb_scale)

        self.spin_bpm.valueChanged.connect(
            lambda v: self.bpmChanged.emit(float(v)))
        self.cb_ts.currentIndexChanged.connect(self._emit_ts)
        self.cb_key.currentIndexChanged.connect(
            lambda _i: self.keyChanged.emit(int(self.cb_key.currentData())))
        self.cb_scale.currentIndexChanged.connect(
            lambda _i: self.scaleChanged.emit(str(self.cb_scale.currentData())))

    def _emit_ts(self, _index: int) -> None:
        data = self.cb_ts.currentData()
        if data:
            self.timeSigChanged.emit(int(data[0]), int(data[1]))

    def set_status(self, text: str, fragment: str = '') -> None:
        self.lbl_stats.setText(text)
        self.lbl_frag.setText(fragment)

    def load_from(self, doc: EditDoc) -> None:
        """把文档的上下文填进控件。改控件时不触发信号，避免回环。"""
        for widget in (self.spin_bpm, self.cb_ts, self.cb_key, self.cb_scale):
            widget.blockSignals(True)
        self.spin_bpm.setValue(int(round(doc.bpm)))
        for i in range(self.cb_ts.count()):
            if self.cb_ts.itemData(i) == (doc.time_sig_num, doc.time_sig_den):
                self.cb_ts.setCurrentIndex(i)
                break
        self.cb_key.setCurrentIndex(max(0, min(11, doc.key)))
        idx = self.cb_scale.findData(doc.scale)
        self.cb_scale.setCurrentIndex(idx if idx >= 0 else 0)
        for widget in (self.spin_bpm, self.cb_ts, self.cb_key, self.cb_scale):
            widget.blockSignals(False)


class EditorPanel(QFrame):
    """编辑器整体：工具行 + 卷帘 + 信息行。

    对外只暴露三件事：喂一份文档进来、问它现在的文档、把播放位置喂进去。
    其余信号转发给主窗口，主窗口只管重算编排和刷新预览。
    """

    changed = Signal()
    seekRequested = Signal(float)
    statusMessage = Signal(str)
    #: 编辑器请求保存（工具栏按钮 / 编辑器内 Ctrl+S）
    saveRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.toolbar = EditorToolbar()
        self.editor = ScoreEditor()
        self.infobar = EditorInfoBar()
        layout.addWidget(self.toolbar)
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.infobar)

        # 撤销/重做快捷键必须挂在面板层（WidgetWithChildren）：工具栏上有
        # BPM / 拍号 / 吸附这些输入框，QSpinBox/QLineEdit 会把 Ctrl+Z 吃掉
        # 当成「撤销框里的文字」—— 用户点过一次输入框，Ctrl+Z 就永远到
        # 不了乐谱。QShortcut 处理常规焦点；但 QSpinBox 会用 ShortcutOverride
        # 抢占 Ctrl+Z（它有内置框内撤销），所以对这些输入框再装一层事件
        # 过滤器兜底，直接把撤销/重做按键改道到乐谱。
        from PySide6.QtGui import QKeySequence, QShortcut
        for seq, fn in (('Ctrl+Z', lambda: self.editor.undo()),
                        ('Ctrl+Shift+Z', lambda: self.editor.redo()),
                        ('Ctrl+Y', lambda: self.editor.redo())):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

        self._key_filter = _UndoKeyFilter(self)
        for w in (self.infobar.spin_bpm, self.toolbar.cb_snap):
            w.installEventFilter(self._key_filter)

        self._wire()

    def _wire(self) -> None:
        tb, ed, ib = self.toolbar, self.editor, self.infobar

        ed.changed.connect(self.changed)
        ed.changed.connect(self.refresh_labels)   # 编辑后立刻同步撤销/重做使能
        ed.seekRequested.connect(self.seekRequested)
        ed.statusMessage.connect(self.statusMessage)
        ed.selectionChanged.connect(lambda _n: self.refresh_labels())

        tb.btn_merge.clicked.connect(lambda: ed.merge_selection())
        tb.btn_quant.clicked.connect(lambda: ed.quantize_selection())
        tb.btn_scale.clicked.connect(lambda: ed.snap_selection_to_scale())
        tb.btn_overlap.clicked.connect(lambda: ed.fix_overlaps())
        tb.btn_delete.clicked.connect(lambda: ed.delete_selection())
        tb.btn_undo.clicked.connect(lambda: ed.undo())
        tb.btn_redo.clicked.connect(lambda: ed.redo())
        tb.btn_range.clicked.connect(lambda: ed.begin_range_pick('delete'))
        tb.btn_cut.clicked.connect(lambda: ed.begin_range_pick('cut'))
        tb.btn_gap.clicked.connect(self.insert_gap_at_playhead)
        tb.btn_scale_ins.clicked.connect(self.insert_scale_dialog)
        tb.btn_save.clicked.connect(self.saveRequested)
        ed.saveRequested.connect(self.saveRequested)
        tb.cb_snap.currentIndexChanged.connect(
            lambda _i: ed.set_snap_grid(float(tb.cb_snap.currentData() or 0.0)))
        tb.btn_zoom_in.clicked.connect(lambda: ed.set_zoom(ed.zoom() * 1.3))
        tb.btn_zoom_out.clicked.connect(lambda: ed.set_zoom(ed.zoom() / 1.3))
        tb.btn_zoom_fit.clicked.connect(ed.zoom_fit)

        ib.bpmChanged.connect(self._on_bpm)
        ib.timeSigChanged.connect(self._on_time_sig)
        ib.keyChanged.connect(self._on_key)
        ib.scaleChanged.connect(self._on_scale)

        ed.set_snap_grid(float(tb.cb_snap.currentData() or 0.0))

    # -- 文档 --

    def set_doc(self, doc: Optional[EditDoc], keep_view: bool = False) -> None:
        self.editor.set_doc(doc, keep_view=keep_view)
        self.setEnabled(doc is not None and len(doc) > 0)
        if doc is not None:
            self.infobar.load_from(doc)
        self.refresh_labels()

    def doc(self) -> Optional[EditDoc]:
        return self.editor.doc()

    # -- 转发给内部卷帘 ---------------------------------------------------
    # 面板是编辑器对外的门面，调用方不该为了选几个音符而写
    # window.editor.editor.select_indices(...) 这种穿透两层的代码。

    def select_indices(self, indices) -> None:
        self.editor.select_indices(indices)

    def select_all(self) -> None:
        self.editor.select_all()

    def selection_count(self) -> int:
        return self.editor.selection_count()

    def merge_selection(self, by_pitch: bool = True) -> int:
        return self.editor.merge_selection(by_pitch=by_pitch)

    def merge_all_fragments(self) -> int:
        return self.editor.merge_all_fragments()

    def undo(self) -> None:
        self.editor.undo()

    def redo(self) -> None:
        self.editor.redo()

    def refresh(self, reframe: bool = False) -> None:
        self.editor.refresh(reframe=reframe)
        self.refresh_labels()

    def set_position(self, seconds: float) -> None:
        self.editor.set_position(seconds)

    # -- 插入类操作（都挂在播放头上） ------------------------------------

    def _playhead(self) -> float:
        return float(getattr(self.editor, '_position', 0.0) or 0.0)

    def insert_gap_at_playhead(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        doc = self.editor.doc()
        if doc is None or not len(doc):
            self.statusMessage.emit('先选一首曲子，再插入空白')
            return
        at = self._playhead()
        gap, ok = QInputDialog.getDouble(
            self, '插入空白',
            '在 %.2fs（播放头）处插入空白时长（秒）：' % at,
            1.0, 0.1, 60.0, 2)
        if not ok:
            return
        moved = doc.insert_time(at, gap)
        if not moved:
            self.statusMessage.emit('%.2fs 之后没有音符，无需插入' % at)
            self.editor.update()
            return
        self.editor._after_edit(
            '在 %.2fs 插入了 %.2fs 空白，%d 个音符后移 —— Ctrl+Z 可撤销'
            % (at, gap, moved))

    def insert_scale_dialog(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        doc = self.editor.doc()
        if doc is None:
            self.statusMessage.emit('先选一首曲子，再插入音阶')
            return
        at = self._playhead()
        directions = ('上行 + 下行', '上行', '下行')
        d, ok = QInputDialog.getItem(
            self, '插入音阶',
            '在 %.2fs（播放头）插入一段调内音阶：' % at,
            directions, 0, False)
        if not ok:
            return
        beats, ok = QInputDialog.getDouble(
            self, '插入音阶', '每个音的时长（拍，1 = 四分音符）：',
            0.5, 0.125, 4.0, 3)
        if not ok:
            return
        direction = {'上行 + 下行': 'updown', '上行': 'up', '下行': 'down'}[d]
        n = doc.insert_scale(at, direction, beats)
        self.editor._after_edit(
            '在 %.2fs 插入了 %d 个音阶音（%s）—— Ctrl+Z 可撤销' % (at, n, d))

    def refresh_labels(self) -> None:
        doc = self.editor.doc()
        if doc is None or not len(doc):
            self.infobar.set_status('', '')
            self.toolbar.sync_history(False, False)
            return

        info = doc.summarize()
        parts = ['%d 小节' % info.get('bars', 0),
                 '%d 音符' % len(doc),
                 _fmt_clock(doc.duration)]
        sel = self.editor.selection_count()
        if sel:
            parts.append('选中 %d' % sel)

        rep = fragment_report(doc)
        hint = ('可合并 %d 组碎片（省 %d 个音符）' % (rep['groups'], rep['extra'])
                if rep['groups'] else '')
        self.infobar.set_status('  ·  '.join(parts), hint)

        self.toolbar.sync_history(doc.can_undo, doc.can_redo)

    # -- 上下文改动 --

    def _apply_context(self, **kw) -> None:
        doc = self.editor.doc()
        if doc is None:
            return
        doc.set_context(**kw)
        self.editor.refresh(reframe=True)
        self._after_context()

    def _after_context(self) -> None:
        doc = self.editor.doc()
        if doc is not None:
            self.infobar.load_from(doc)
        self.refresh_labels()
        self.changed.emit()

    def _on_bpm(self, bpm: float) -> None:
        self._apply_context(bpm=bpm)

    def _on_time_sig(self, num: int, den: int) -> None:
        from ..notation import TimeSignature
        self._apply_context(time_sig=TimeSignature(num, den))

    def _on_key(self, key: int) -> None:
        self._apply_context(key=key)

    def _on_scale(self, scale: str) -> None:
        self._apply_context(scale=scale)


def _fmt_clock(seconds: float) -> str:
    total = max(int(round(seconds)), 0)
    return '%02d:%02d' % (total // 60, total % 60)
