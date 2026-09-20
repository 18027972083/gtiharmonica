"""侧边栏（曲库）与参数卡片。

布局参照参考软件：
  * 侧边栏：品牌 → 搜索 → 导入 → 曲库列表（每项两行：曲名 / 时长·音符数）
  * 参数卡片：一行下拉与开关 + 一排三滑块 + 底部说明
"""
from __future__ import annotations

import math
import os
import shutil
from typing import List, Optional

from PySide6.QtCore import (QEvent, QRect, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox,
                               QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPushButton,
                               QSizePolicy, QSlider, QSpinBox, QStyle,
                               QStyledItemDelegate, QVBoxLayout, QWidget)

from .. import library_state as lstate
from ..fingering import STRATEGIES
from .theme import C, make_icon
from ..instrument import note_name
from ..score import Score

AUDIO_EXT = ('.mid', '.midi', '.json')


def _fmt_time(seconds: float) -> str:
    total = max(int(round(seconds)), 0)
    return '%02d:%02d' % (total // 60, total % 60)


# ---------------------------------------------------------------------------
# 曲库条目绘制
# ---------------------------------------------------------------------------

#: 曲库条目左侧的八分音符图标参数。
#: 照原版 gtiartist/library_widgets.py 的 music_icon 逐条重建：
#: 20x24 画布、两根符干 + 一条连接线 + 两个实心符头。
#: 颜色画时从 C.BRAND 取（跟随主题），不用模块常量缓存。
_ICON_W, _ICON_H = 20, 24
_ICON_GAP = 30


def music_icon() -> QPixmap:
    """画一个八分音符图标（原版是现画的，没有图片资源）。"""
    pixmap = QPixmap(_ICON_W, _ICON_H)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(C.BRAND), 1.5))
    painter.drawLine(8, 18, 8, 6)       # 左符干
    painter.drawLine(8, 6, 17, 3)       # 连接线
    painter.drawLine(17, 3, 17, 15)     # 右符干
    painter.setBrush(QColor(C.BRAND))
    painter.drawEllipse(2, 16, 6, 4)    # 左符头
    painter.drawEllipse(11, 13, 6, 4)   # 右符头
    painter.end()
    return pixmap


class _ScoreItemDelegate(QStyledItemDelegate):
    """曲库条目：八分音符图标 + 曲名 + 「时长 · 音符数」+ 右侧收藏星标。"""

    #: 星标热区边长（正方形，竖直居中贴右侧）
    STAR_HIT = 30

    def sizeHint(self, option, index) -> QSize:
        # 原版 SongRow 的最小高度是 68
        return QSize(200, 64)

    @staticmethod
    def star_rect(rect: QRect) -> QRect:
        """星标热区。绘制与命中测试共用，两处算出来必须一致。"""
        inner = rect.adjusted(2, 2, -2, -2)
        size = _ScoreItemDelegate.STAR_HIT
        return QRect(inner.right() - size,
                     inner.center().y() - size // 2 + 1, size, size)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)

        rect = option.rect.adjusted(2, 2, -2, -2)
        if selected:
            painter.setBrush(QColor(C.SELECT_BG))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 9, 9)
        elif hovered:
            painter.setBrush(QColor(C.HOVER))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 9, 9)

        title = index.data(Qt.DisplayRole) or ''
        meta = index.data(Qt.UserRole + 1) or ''
        favorited = bool(index.data(Qt.UserRole + 2))

        left = rect.left() + 11
        painter.drawPixmap(left, rect.top() + (rect.height() - _ICON_H) // 2,
                           music_icon())
        left += _ICON_GAP
        right = rect.right() - 10 - self.STAR_HIT
        width = max(10, right - left)

        painter.setPen(QColor(C.TEXT) if not selected else QColor(C.SELECT_FG))
        font = QFont('Microsoft YaHei UI', 10)
        font.setBold(True)
        painter.setFont(font)
        fm = QFontMetrics(font)
        painter.drawText(QRect(left, rect.top() + 12, width, 18),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         fm.elidedText(title, Qt.ElideRight, width))

        painter.setPen(QColor(C.MUTED))
        font2 = QFont('Microsoft YaHei UI', 8)
        painter.setFont(font2)
        painter.drawText(QRect(left, rect.top() + 32, width, 16),
                         Qt.AlignLeft | Qt.AlignVCenter, meta)

        self._paint_star(painter, option.rect, favorited, hovered)
        painter.restore()

    def _paint_star(self, painter: QPainter, item_rect: QRect,
                    favorited: bool, hovered: bool) -> None:
        """收藏星标：收藏了是实心强调色，没收藏是描边（悬停时提亮）。"""
        center = self.star_rect(item_rect).center()
        path = _star_path(center.x() + 0.5, center.y() + 0.5)
        if favorited:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(C.ACCENT))
            painter.drawPath(path)
        else:
            pen = QPen(QColor(C.TEXT if hovered else C.MUTED))
            pen.setWidthF(1.3)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)


def _star_path(cx: float, cy: float, r: float = 8.0) -> QPainterPath:
    """五角星路径（一个尖朝上），r 是外接圆半径。"""
    inner = r * 0.42
    path = QPainterPath()
    for i in range(10):
        radius = r if i % 2 == 0 else inner
        angle = -math.pi / 2 + i * math.pi / 5
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    return path


# ---------------------------------------------------------------------------
# 侧边栏
# ---------------------------------------------------------------------------

class Sidebar(QFrame):
    """左侧：品牌、搜索、导入、曲库列表、快捷键提示。"""

    selected = Signal(str)
    activated = Signal(str)
    imported = Signal(list)
    removed = Signal(str)
    showKeymap = Signal()

    def __init__(self, directory: str, version: str = '', parent=None):
        super().__init__(parent)
        self.setObjectName('sidebar')
        self.directory = directory
        os.makedirs(directory, exist_ok=True)
        self._all: List[str] = []
        self._meta_cache = {}
        self._locked = False        # 演奏/试听中锁选曲（防误触）
        self._favs: List[str] = lstate.read_favorites(directory)
        self._fav_only = False      # 只看收藏
        self._shown = 0
        self._build(version)

    def _build(self, version: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 22, 18, 16)
        root.setSpacing(12)

        # 品牌位：图标 + 名称 + 标语。图标就是应用图标（theme.make_icon），
        # 不引入第二套视觉。
        head = QHBoxLayout()
        head.setSpacing(10)
        brand_icon = QLabel()
        brand_icon.setPixmap(make_icon(40).pixmap(40, 40))
        head.addWidget(brand_icon, 0, Qt.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        brand = QLabel('大肥鲸洲琴工具包')
        brand.setObjectName('brand')
        text_col.addWidget(brand)
        sub = QLabel('三角洲行动 · 自动口琴')
        sub.setObjectName('muted')
        text_col.addWidget(sub)
        head.addLayout(text_col)
        head.addStretch(1)
        root.addLayout(head)

        root.addSpacing(6)

        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索曲库…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        root.addWidget(self.search)

        # 导入入口已挪到主窗口右上角（顶栏「导入 MIDI / 曲谱」），
        # MIDI 入库时会弹出「直转 / 旋律化」二选一 —— 见 main.import_paths

        # 曲库标题行：右边挂「全部 / 收藏」筛选（收藏视图只列星标过的曲目）
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        section = QLabel('我的曲库')
        section.setObjectName('section')
        head.addWidget(section)
        head.addStretch(1)

        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        self.btn_all = QPushButton('全部')
        self.btn_fav = QPushButton('收藏')
        for btn, fav in ((self.btn_all, False), (self.btn_fav, True)):
            btn.setObjectName('chip')
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip('收藏的曲目单列一份'
                           if fav else '显示全部曲目')
            btn.clicked.connect(lambda _=False, f=fav: self.set_fav_only(f))
            self._filter_group.addButton(btn)
            head.addWidget(btn)
        self.btn_all.setChecked(True)
        root.addLayout(head)

        self.list = QListWidget()
        self.list.setMinimumHeight(96)   # 小窗口时先压缩列表，底部块不裁
        self.list.setItemDelegate(_ScoreItemDelegate(self.list))
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setMouseTracking(True)
        self.list.setUniformItemSizes(True)
        self.list.itemSelectionChanged.connect(self._on_selection)
        self.list.itemDoubleClicked.connect(self._on_double)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context)
        # 星标点击要在列表自己的处理之前拦下来（见 eventFilter）
        self.list.viewport().setMouseTracking(True)
        self.list.viewport().installEventFilter(self)
        root.addWidget(self.list, 1)

        self.hint = QLabel('')
        self.hint.setObjectName('keyHint')
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        sep = QFrame()
        sep.setObjectName('separator')
        root.addWidget(sep)

        self.btn_keymap = QPushButton('键位 / 音高 / 快捷键')
        # 默认描边样式：与上方「导入 MIDI / 曲谱」同一档视觉
        self.btn_keymap.clicked.connect(self.showKeymap.emit)
        root.addWidget(self.btn_keymap)

        hint = QLabel('F8  开始 / 暂停 / 继续\nF9  停止并释放键鼠')
        hint.setObjectName('keyHint')
        root.addWidget(hint)

        if version:
            ver = QLabel('v%s' % version)
            ver.setObjectName('keyHint')
            root.addWidget(ver)

    # -- 曲库数据 --

    def refresh(self) -> None:
        files = []
        try:
            for name in sorted(os.listdir(self.directory)):
                if name.lower().endswith(AUDIO_EXT):
                    files.append(os.path.join(self.directory, name))
        except OSError:
            pass
        self._all = files
        # 文件真的回到曲库了（用户又拖回来 / 重新导入）说明删除名单里的
        # 那条记录该撤了，否则它永远不再受「程序目录曲库补齐」保护。
        deleted = {n.lower() for n in lstate.read_deleted(self.directory)}
        if deleted:
            for path in files:
                name = os.path.basename(path)
                if name.lower() in deleted:
                    lstate.forget_deleted(self.directory, name)
        self._favs = lstate.read_favorites(self.directory)
        self._apply_filter()

    def _is_fav(self, name: str) -> bool:
        low = name.lower()
        return any(n.lower() == low for n in self._favs)

    def set_fav_only(self, only: bool) -> None:
        only = bool(only)
        if only == self._fav_only:
            return
        self._fav_only = only
        self.btn_fav.setChecked(only)
        self.btn_all.setChecked(not only)
        self._apply_filter()

    def _apply_filter(self) -> None:
        needle = self.search.text().strip().lower()
        current = self.current_path()
        self.list.clear()
        shown = 0
        for path in self._all:
            name = os.path.basename(path)
            if needle and needle not in name.lower():
                continue
            fav = self._is_fav(name)
            if self._fav_only and not fav:
                continue
            item = QListWidgetItem(os.path.splitext(name)[0])
            item.setData(Qt.UserRole, path)
            item.setData(Qt.UserRole + 1, self._meta(path))
            item.setData(Qt.UserRole + 2, fav)
            item.setToolTip('%s\n右键可收藏 / 取消收藏' % path)
            self.list.addItem(item)
            shown += 1
            if current and path == current:
                self.list.setCurrentItem(item)
        self._shown = shown
        self._update_hint()

    def _update_hint(self) -> None:
        if not self._all:
            self.hint.setText('曲库是空的，点上面的按钮导入，或把文件拖进窗口。')
        elif self._shown == 0 and self._fav_only \
                and not self.search.text().strip():
            self.hint.setText('还没有收藏的曲目：把鼠标移到曲目上，'
                              '点右边的星标就能收藏。')
        elif self._shown == 0:
            self.hint.setText('没有匹配「%s」的曲目。' % self.search.text().strip())
        elif self._fav_only:
            self.hint.setText('收藏 %d 首 · 共 %d 首' % (self._shown, len(self._all)))
        else:
            self.hint.setText('共 %d 首' % self._shown)

    def _flash_hint(self, text: str) -> None:
        self.hint.setText(text)
        QTimer.singleShot(1600, self._update_hint)

    def _meta(self, path: str) -> str:
        """读取缓存里的时长/音符数；没有就异步解析。"""
        cached = self._meta_cache.get(path)
        if cached:
            return cached
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return ''
        key = (path, mtime)
        cached = self._meta_cache.get(key)
        if cached:
            return cached
        QTimer.singleShot(10, lambda p=path: self._parse_meta(p))
        return '…'

    def _parse_meta(self, path: str) -> None:
        try:
            from ..score import load_score
            score = load_score(path)
            text = '%s · %d 音符' % (_fmt_time(score.duration), len(score.notes))
        except Exception as exc:
            text = '无法读取：%s' % str(exc)[:26]
        self._meta_cache[path] = text
        try:
            for i in range(self.list.count()):
                item = self.list.item(i)
                if item.data(Qt.UserRole) == path:
                    item.setData(Qt.UserRole + 1, text)
        except RuntimeError:
            return      # 控件已销毁（窗口关闭后回调才到）：缓存写完就够

    def current_path(self) -> Optional[str]:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def select_path(self, path: str) -> bool:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.UserRole) == path:
                self.list.setCurrentRow(i)
                # 必须显式滚动过去：列表是 ScrollPerPixel 模式，
                # setCurrentRow 不会自动滚动到可见区域。加上曲库是按名称
                # 排序的，新导入的曲目经常落在很靠后的位置（比如第 36/45 位），
                # 滚动条却停在顶部，用户就会以为「根本没加进来」。
                self.list.scrollToItem(item, QAbstractItemView.PositionAtCenter)
                return True
        return False

    # -- 事件 --

    def _on_selection(self) -> None:
        if self._locked:
            return
        path = self.current_path()
        if path:
            self.selected.emit(path)

    def _on_double(self, item: QListWidgetItem) -> None:
        if self._locked:
            return
        self.activated.emit(item.data(Qt.UserRole))

    def set_locked(self, locked: bool) -> None:
        """演奏 / 试听中锁定选曲，防误触切歌打断当前播放。

        锁定时把选择模式设为 NoSelection：单击不再改变选中项，
        视觉上列表也不出现高亮位移；双击另在 _on_double 里拦截。
        """
        locked = bool(locked)
        if locked == getattr(self, '_locked', False):
            return
        self._locked = locked
        self.list.setSelectionMode(QAbstractItemView.NoSelection if locked
                                   else QAbstractItemView.SingleSelection)
        self.list.setToolTip('演奏 / 试听进行中：先停止再切换曲目'
                             if locked else '')

    def _on_context(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        self.list.setCurrentItem(item)
        path = item.data(Qt.UserRole)
        fav = self._is_fav(os.path.basename(path))
        menu = QMenu(self)
        act_fav = menu.addAction('取消收藏' if fav else '收藏')
        menu.addSeparator()
        act_del = menu.addAction('从曲库删除')
        chosen = menu.exec(self.list.viewport().mapToGlobal(pos))
        if chosen is act_fav:
            self._toggle_favorite(path)
        elif chosen is act_del:
            self._delete_current()

    def eventFilter(self, obj, event) -> bool:
        """列表视口上的事件：星标命中就切换收藏，并且不改动当前选中项。

        装作没看见是行不通的 —— 不拦的话点星标会连带把这首歌选中，
        「收藏」就变成了「切歌」，演奏中还会干扰。
        """
        if obj is self.list.viewport():
            kind = event.type()
            if kind in (QEvent.MouseButtonPress, QEvent.MouseMove):
                pos = event.position().toPoint()
                item = self.list.itemAt(pos)
                on_star = item is not None and _ScoreItemDelegate.star_rect(
                    self.list.visualItemRect(item)).contains(pos)
                if kind == QEvent.MouseMove:
                    self.list.viewport().setCursor(
                        Qt.PointingHandCursor if on_star else Qt.ArrowCursor)
                elif on_star and event.button() == Qt.LeftButton:
                    self._toggle_favorite(item.data(Qt.UserRole))
                    return True
        return super().eventFilter(obj, event)

    def _toggle_favorite(self, path: str) -> None:
        if not path:
            return
        name = os.path.basename(path)
        now = not self._is_fav(name)
        self._favs = lstate.set_favorite(self.directory, name, now)
        self._apply_filter()
        title = os.path.splitext(name)[0]
        self._flash_hint('已收藏「%s」' % title if now
                         else '已取消收藏「%s」' % title)

    def import_files(self, paths: List[str]) -> List[str]:
        added = []
        for src in paths:
            if not os.path.isfile(src) or not src.lower().endswith(AUDIO_EXT):
                continue
            base = os.path.basename(src)
            stem, ext = os.path.splitext(base)
            target = os.path.join(self.directory, base)
            counter = 1
            while os.path.exists(target) and not _same_file(src, target):
                target = os.path.join(self.directory,
                                      '%s (%d)%s' % (stem, counter, ext))
                counter += 1
                if counter > 999:
                    target = None
                    break
            if target is None:
                continue
            if _same_file(src, target):
                added.append(target)
                continue
            try:
                shutil.copy2(src, target)
                added.append(target)
            except OSError as exc:
                QMessageBox.warning(self, '导入失败', '%s\n%s' % (base, exc))
        if added:
            self.refresh()
            # 同样要滚过去，否则导入的曲目排在列表后面时用户看不到变化
            self.select_path(added[-1])
            self.imported.emit(added)
        return added

    def _delete_current(self) -> None:
        path = self.current_path()
        if not path:
            return
        name = os.path.basename(path)
        ret = QMessageBox.question(
            self, '删除曲谱',
            '确定要从曲库删除「%s」吗？\n\n%s\n\n'
            '删除后不会再被自动导回（程序目录里的同名曲目会被跳过）。'
            % (name, path),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            os.remove(path)
        except OSError as exc:
            QMessageBox.warning(self, '删除失败', str(exc))
            return
        # 删除名单（墓碑）：程序目录曲库的启动补齐、内置曲目安装都会跳过它，
        # 否则用户删掉的曲子下次启动又被拷回来。
        lstate.mark_deleted(self.directory, name)
        self._meta_cache.pop(path, None)
        self.refresh()
        self.removed.emit(path)


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 参数卡片
# ---------------------------------------------------------------------------

class _SliderField(QWidget):
    """一个紧凑滑块：上排「标签 …… 数值」，下排滑块。"""

    valueChanged = Signal(float)

    def __init__(self, minimum: float, maximum: float, value: float,
                 scale: float = 1.0, fmt: str = '%.2f', suffix: str = '',
                 tooltip: str = '', parent=None,
                 display_scale: float = 1.0):
        super().__init__(parent)
        self.scale = scale
        self.fmt = fmt
        self.suffix = suffix
        #: 数值标签的显示比例。scale 只决定滑块刻度分辨率（比如 0.4..1.0
        #: 摊到 40..100 整数步进），标签要按百分比显示时也得放大同样倍数，
        #: 否则 '%.0f' % 0.9 永远显示成 "0%"。
        self.display_scale = display_scale

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel('')
        self.label.setObjectName('muted')
        self.value_label = QLabel('')
        self.value_label.setObjectName('paramValue')
        head.addWidget(self.label)
        head.addStretch(1)
        head.addWidget(self.value_label)
        lay.addLayout(head)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(int(round(minimum * scale)))
        self.slider.setMaximum(int(round(maximum * scale)))
        self.slider.setValue(int(round(value * scale)))
        self.slider.valueChanged.connect(self._on_change)
        lay.addWidget(self.slider)

        if tooltip:
            self.setToolTip(tooltip)
            self.slider.setToolTip(tooltip)
        self._update_label(value)

    def set_label(self, text: str) -> None:
        self.label.setText(text)

    def value(self) -> float:
        return self.slider.value() / self.scale

    def set_value(self, value: float) -> None:
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(int(round(value * self.scale)))
        self.slider.blockSignals(blocked)
        self._update_label(value)

    def _on_change(self, _raw: int) -> None:
        value = self.value()
        self._update_label(value)
        self.valueChanged.emit(value)

    def _update_label(self, value: float) -> None:
        self.value_label.setText((self.fmt % (value * self.display_scale))
                                 + self.suffix)


class ParamCard(QFrame):
    """参数卡片：音轨/移调/开关一行，三个滑块一排，底部说明。"""

    changed = Signal()
    previewTimbreChanged = Signal(str)

    def __init__(self, options, cost, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)

        # --- 第一行：音轨 / 移调 / 开关 / 策略 ---
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        lbl_track = QLabel('旋律音轨')
        lbl_track.setObjectName('muted')
        row1.addWidget(lbl_track)

        self.cb_track = QComboBox()
        self.cb_track.setMinimumWidth(200)
        self.cb_track.addItem('全部音轨（合并）', None)
        self.cb_track.currentIndexChanged.connect(self._emit_changed)
        row1.addWidget(self.cb_track, 2)

        lbl_tr = QLabel('移调')
        lbl_tr.setObjectName('muted')
        row1.addWidget(lbl_tr)

        self.sp_transpose = QSpinBox()
        self.sp_transpose.setRange(-24, 24)
        self.sp_transpose.setValue(options.transpose)
        self.sp_transpose.setSuffix(' 半音')
        self.sp_transpose.setFixedWidth(96)
        self.sp_transpose.valueChanged.connect(self._emit_changed)
        row1.addWidget(self.sp_transpose)

        self.chk_fold = QCheckBox('八度折叠')
        self.chk_fold.setChecked(options.fold_octaves)
        self.chk_fold.toggled.connect(self._emit_changed)
        row1.addWidget(self.chk_fold)

        self.chk_auto_oct = QCheckBox('自动选八度')
        self.chk_auto_oct.setChecked(getattr(options, 'auto_octave', False))
        self.chk_auto_oct.setToolTip(
            '在整数八度平移里挑「音域命中率」最高的一档，'
            '并列取离平均音高最近的。\n'
            '移植自 midikey-player 的 AutoBaseOctave；开着时仍可叠加手动移调。')
        self.chk_auto_oct.toggled.connect(self._emit_changed)
        row1.addWidget(self.chk_auto_oct)

        lbl_st = QLabel('指法')
        lbl_st.setObjectName('muted')
        row1.addWidget(lbl_st)

        self.cb_strategy = QComboBox()
        for name, cls in STRATEGIES.items():
            self.cb_strategy.addItem(cls.description, name)
        self._set_combo(self.cb_strategy, options.strategy)
        self.cb_strategy.currentIndexChanged.connect(self._emit_changed)
        self.cb_strategy.setMinimumWidth(190)
        row1.addWidget(self.cb_strategy, 1)
        root.addLayout(row1)

        # --- 第二行：三个滑块 ---
        row2 = QHBoxLayout()
        row2.setSpacing(26)

        self.f_speed = _SliderField(
            0.25, 2.0, options.speed, 100, '%.2f', '×',
            '整体演奏速度。手跟不上就降到 0.8。')
        self.f_speed.set_label('演奏速度')
        self.f_gate = _SliderField(
            0.4, 1.0, options.gate, 100, '%.0f', '%',
            '音符连贯度。越大连奏越顺；音符粘连分不清时降到 70% 左右。',
            display_scale=100)
        self.f_gate.set_label('音符连贯度')
        self.f_breath = _SliderField(
            0, 300, options.breath_ms, 1, '%.0f', ' ms',
            '乐句结尾的换气停顿。长乐句吹不过来就加大。')
        self.f_breath.set_label('句尾换气')

        for field in (self.f_speed, self.f_gate, self.f_breath):
            field.valueChanged.connect(self._emit_changed)
            row2.addWidget(field, 1)
        root.addLayout(row2)

        # --- 第三行：次级选项 + 试听音色 ---
        row3 = QHBoxLayout()
        row3.setSpacing(10)

        lbl_prev = QLabel('试听音色')
        lbl_prev.setObjectName('muted')
        row3.addWidget(lbl_prev)

        self.cb_timbre = QComboBox()
        self.cb_timbre.addItem('标准', 'default')
        self.cb_timbre.addItem('柔和', 'soft')
        self.cb_timbre.addItem('明亮', 'bright')
        self.cb_timbre.currentIndexChanged.connect(
            lambda: self.previewTimbreChanged.emit(self.cb_timbre.currentData()))
        row3.addWidget(self.cb_timbre)

        lbl_chord = QLabel('和弦取音')
        lbl_chord.setObjectName('muted')
        row3.addWidget(lbl_chord)

        self.cb_chord = QComboBox()
        for value, text in (('highest', '最高音（旋律优先）'),
                            ('lowest', '最低音'), ('loudest', '力度最大'),
                            ('first', '最先出现')):
            self.cb_chord.addItem(text, value)
        self._set_combo(self.cb_chord, options.chord_policy)
        self.cb_chord.currentIndexChanged.connect(self._emit_changed)
        row3.addWidget(self.cb_chord)

        self.chk_tail = QCheckBox('和声尾巴补回')
        self.chk_tail.setChecked(getattr(options, 'chord_tail', False))
        self.chk_tail.setToolTip(
            '和弦取音为「最高音 / 最低音」时：被压掉的声部若比主音更长，'
            '主音结束后补回它的尾巴，和声线条不断。\n'
            '多声部 MIDI 直转推荐开启。移植自 midikey-player。')
        self.chk_tail.toggled.connect(self._emit_changed)
        row3.addWidget(self.chk_tail)

        lbl_fold = QLabel('折叠方向')
        lbl_fold.setObjectName('muted')
        row3.addWidget(lbl_fold)

        self.cb_fold = QComboBox()
        for value, text in (('nearest', '就近'), ('down', '优先降'), ('up', '优先升')):
            self.cb_fold.addItem(text, value)
        self._set_combo(self.cb_fold, options.fold_prefer)
        self.cb_fold.currentIndexChanged.connect(self._emit_changed)
        row3.addWidget(self.cb_fold)
        row3.addStretch(1)

        self.btn_reset = QPushButton('恢复默认')
        self.btn_reset.setObjectName('ghost')
        self.btn_reset.clicked.connect(self._reset)
        row3.addWidget(self.btn_reset)
        root.addLayout(row3)

        note = QLabel('保留原谱节奏与休止；句尾换气会收短尾音，不移动后续拍点。')
        note.setObjectName('keyHint')
        note.setWordWrap(True)
        root.addWidget(note)

    # -- 辅助 --

    @staticmethod
    def _set_combo(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _emit_changed(self, *_args) -> None:
        if not self._loading:
            self.changed.emit()

    def _reset(self) -> None:
        from ..arrange import Options
        defaults = Options()
        self._loading = True
        try:
            self.f_speed.set_value(defaults.speed)
            self.f_gate.set_value(defaults.gate)
            self.f_breath.set_value(defaults.breath_ms)
            self.sp_transpose.setValue(defaults.transpose)
            self._set_combo(self.cb_chord, defaults.chord_policy)
            self._set_combo(self.cb_fold, defaults.fold_prefer)
            self._set_combo(self.cb_strategy, defaults.strategy)
            self.chk_fold.setChecked(defaults.fold_octaves)
            self.chk_auto_oct.setChecked(defaults.auto_octave)
            self.chk_tail.setChecked(defaults.chord_tail)
            self.cb_track.setCurrentIndex(0)
        finally:
            self._loading = False
        self.changed.emit()

    # -- 与 Options 同步 --

    def apply_to(self, options) -> None:
        options.speed = round(self.f_speed.value(), 2)
        options.gate = round(self.f_gate.value(), 2)
        options.breath_ms = int(round(self.f_breath.value()))
        options.transpose = int(self.sp_transpose.value())
        options.chord_policy = self.cb_chord.currentData()
        options.fold_prefer = self.cb_fold.currentData()
        options.fold_octaves = self.chk_fold.isChecked()
        options.auto_octave = self.chk_auto_oct.isChecked()
        options.chord_tail = self.chk_tail.isChecked()
        options.strategy = self.cb_strategy.currentData()
        options.track = self.cb_track.currentData()

    def load_from(self, options) -> None:
        self._loading = True
        try:
            self.f_speed.set_value(options.speed)
            self.f_gate.set_value(options.gate)
            self.f_breath.set_value(options.breath_ms)
            self.sp_transpose.setValue(options.transpose)
            self._set_combo(self.cb_chord, options.chord_policy)
            self._set_combo(self.cb_fold, options.fold_prefer)
            self._set_combo(self.cb_strategy, options.strategy)
            self.chk_fold.setChecked(options.fold_octaves)
            self.chk_auto_oct.setChecked(options.auto_octave)
            self.chk_tail.setChecked(options.chord_tail)
        finally:
            self._loading = False

    def set_tracks(self, score: Optional[Score], instrument=None,
                   current=None) -> None:
        self._loading = True
        try:
            self.cb_track.clear()
            self.cb_track.addItem('全部音轨（合并）', None)
            if score is None:
                return
            for t in score.track_summary():
                text = '音轨 %d · %d 音符 · %s..%s' % (
                    t['track'], t['notes'],
                    note_name(t['pitch_min']), note_name(t['pitch_max']))
                if instrument is not None:
                    playable = sum(1 for n in score.track_notes(t['track'])
                                   if instrument.is_playable(n.pitch))
                    text += ' · 覆盖 %.0f%%' % (
                        100.0 * playable / max(t['notes'], 1))
                self.cb_track.addItem(text, t['track'])
            if current is not None:
                index = self.cb_track.findData(current)
                if index >= 0:
                    self.cb_track.setCurrentIndex(index)
        finally:
            self._loading = False
