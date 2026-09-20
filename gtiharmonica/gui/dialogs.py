"""对话框：策略分析、校准向导、设置、使用说明、关于。"""
from __future__ import annotations

import os
import sys
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtCore import QUrl
from .theme import C
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout,
                               QFrame, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QRadioButton, QSlider,
                               QSpinBox, QTableWidget, QTableWidgetItem,
                               QTextBrowser, QVBoxLayout, QWidget)

from .. import __version__
from ..arrange import Options
from ..fingering import METRIC_HEADERS, STRATEGIES
from ..instrument import note_name
from ..player import SchedulerConfig


# ---------------------------------------------------------------------------
# 策略分析
# ---------------------------------------------------------------------------

class AnalysisDialog(QDialog):
    """横向对比 4 种指法策略，帮助挑选最适合当前曲目的方案。"""

    HEADERS = ['策略'] + METRIC_HEADERS + ['成本', '时长(s)', '备注']

    def __init__(self, score, instrument, options: Options, cost, parent=None):
        super().__init__(parent)
        self.setWindowTitle('指法策略分析 —— %s' % score.title)
        self.resize(960, 560)
        self.score = score
        self.instrument = instrument
        self.options = options
        self.cost = cost
        self.chosen_strategy: Optional[str] = None
        self._worker = None
        self._rows: List[tuple] = []

        self._build()
        QTimer.singleShot(50, self._start)

    def _build(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel('音轨：'))
        self.cb_track = QComboBox()
        self.cb_track.addItem('全部音轨（合并）', None)
        for t in self.score.track_summary():
            self.cb_track.addItem(
                '音轨 %d · %d 音符 · %s..%s' % (t['track'], t['notes'],
                                                note_name(t['pitch_min']),
                                                note_name(t['pitch_max'])),
                t['track'])
        self.cb_track.currentIndexChanged.connect(self._start)
        top.addWidget(self.cb_track, 1)

        self.btn_rerun = QPushButton('重新分析')
        self.btn_rerun.clicked.connect(self._start)
        top.addWidget(self.btn_rerun)
        root.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        root.addWidget(self.bar)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)

        self.note = QLabel('')
        self.note.setWordWrap(True)
        self.note.setObjectName('dim')
        root.addWidget(self.note)

        buttons = QDialogButtonBox()
        self.btn_apply = QPushButton('应用选中的策略')
        self.btn_apply.setObjectName('primary')
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        buttons.addButton(self.btn_apply, QDialogButtonBox.AcceptRole)
        buttons.addButton('关闭', QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # -- 分析 --

    def _start(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.table.setRowCount(0)
        self._rows.clear()
        self.bar.setVisible(True)
        self.btn_apply.setEnabled(False)
        self.note.setText('正在对比 %d 种策略…' % len(STRATEGIES))

        from .worker import AnalyzeWorker
        track = self.cb_track.currentData()
        tracks = [track] if track is not None or len(self.score.tracks()) <= 1 \
            else [None] + self.score.tracks()
        self._worker = AnalyzeWorker(self.score, self.instrument, self.options,
                                     self.cost, tracks, self)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, rows, errors) -> None:
        self.bar.setVisible(False)
        self._rows = list(rows)
        self.table.setRowCount(len(rows))

        # 找出各项最优，用于加注
        best_switch = min((r[2].modifier_switches for r in rows), default=0)
        best_press = min((r[2].total_presses for r in rows), default=0)
        best_cost = min((r[3] for r in rows), default=0.0)
        best_motion = min((r[2].motion for r in rows), default=0.0)

        for i, (name, track, m, value, duration) in enumerate(rows):
            marks = []
            if m.modifier_switches == best_switch:
                marks.append('切换最少')
            if m.total_presses == best_press:
                marks.append('按键最少')
            if abs(value - best_cost) < 1e-9:
                marks.append('成本最优')
            if abs(m.motion - best_motion) < 1e-9:
                marks.append('移动最少')

            cells = [name] + m.as_row() + [
                '%.1f' % value, '%.1f' % duration, ' / '.join(marks)]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if j == 0:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if j == len(cells) - 1 and text:
                    item.setForeground(Qt.green)
                self.table.setItem(i, j, item)

        self.table.resizeRowsToContents()
        if errors:
            self.note.setText('部分组合无法编排：' + '；'.join(errors[:3]))
        else:
            self.note.setText(
                '提示：「成本最优」由成本模型算出，通常是综合最省力的方案；'
                '若你更容易按错修饰键，优先看「切换最少」那一行。'
                '选中一行后可以点下面的按钮直接应用。')
        self.btn_apply.setEnabled(bool(rows))
        self.table.selectRow(0)

    def _apply(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._rows):
            self.chosen_strategy = self._rows[row][0]
            self.accept()


# ---------------------------------------------------------------------------
# 校准
# ---------------------------------------------------------------------------

class CalibrateDialog(QDialog):
    """引导式校准：测量注入延迟、最短可识别音长、按键通过率。

    注意：这一页会**真的向游戏发送按键**，所以每步都需要用户主动点击。
    """

    def __init__(self, instrument, parent=None):
        super().__init__(parent)
        self.setWindowTitle('校准游戏响应')
        self.resize(720, 620)
        self.instrument = instrument
        self._backend = None
        self.result = None
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)

        warn = QLabel(
            '⚠ 本页会向**当前前台窗口**发送真实按键。请先切到游戏的口琴演奏界面，'
            '不要对着其他程序做校准。')
        warn.setWordWrap(True)
        warn.setObjectName('warnBox')
        root.addWidget(warn)

        # 基准
        box1 = QGroupBox('1. 注入延迟基准（不依赖游戏，可随时运行）')
        v1 = QVBoxLayout(box1)
        self.btn_bench = QPushButton('开始基准测试（发送 300 组按键）')
        self.btn_bench.clicked.connect(self._run_bench)
        v1.addWidget(self.btn_bench)
        self.out_bench = QPlainTextEdit()
        self.out_bench.setReadOnly(True)
        self.out_bench.setMaximumHeight(110)
        self.out_bench.setFont(QFont('Consolas', 9))
        v1.addWidget(self.out_bench)
        root.addWidget(box1)

        # 最短音长
        box2 = QGroupBox('2. 最短可识别音长（二分搜索，需要听游戏里的声音）')
        v2 = QVBoxLayout(box2)
        v2.addWidget(QLabel(
            '点开始后，程序会连发 3 个相同的音；只要 3 个都听见就点「听到了」，'
            '否则点「没听到」。大约需要 5~7 轮。'))
        row = QHBoxLayout()
        self.btn_probe = QPushButton('开始测量')
        self.btn_probe.clicked.connect(self._start_probe)
        row.addWidget(self.btn_probe)
        self.btn_heard = QPushButton('听到了')
        self.btn_heard.setEnabled(False)
        self.btn_heard.clicked.connect(lambda: self._answer_probe(True))
        row.addWidget(self.btn_heard)
        self.btn_missed = QPushButton('没听到')
        self.btn_missed.setEnabled(False)
        self.btn_missed.clicked.connect(lambda: self._answer_probe(False))
        row.addWidget(self.btn_missed)
        v2.addLayout(row)
        self.out_probe = QPlainTextEdit()
        self.out_probe.setReadOnly(True)
        self.out_probe.setMaximumHeight(110)
        self.out_probe.setFont(QFont('Consolas', 9))
        v2.addWidget(self.out_probe)
        root.addWidget(box2)

        # 按键检查
        box3 = QGroupBox('3. 按键通过率（检查 8 个音阶键 + 3 个修饰键）')
        v3 = QVBoxLayout(box3)
        v3.addWidget(QLabel(
            '逐个发送按键组合，你判断游戏里是否发出声音。\n'
            '若整组都没声音，通常是窗口失焦或游戏键位与本程序不一致。'))
        row3 = QHBoxLayout()
        self.btn_keys = QPushButton('开始检查')
        self.btn_keys.clicked.connect(self._start_keys)
        row3.addWidget(self.btn_keys)
        self.btn_key_yes = QPushButton('听到了')
        self.btn_key_yes.setEnabled(False)
        self.btn_key_yes.clicked.connect(lambda: self._answer_key(True))
        row3.addWidget(self.btn_key_yes)
        self.btn_key_no = QPushButton('没听到')
        self.btn_key_no.setEnabled(False)
        self.btn_key_no.clicked.connect(lambda: self._answer_key(False))
        row3.addWidget(self.btn_key_no)
        v3.addLayout(row3)
        self.out_keys = QPlainTextEdit()
        self.out_keys.setReadOnly(True)
        self.out_keys.setMaximumHeight(110)
        self.out_keys.setFont(QFont('Consolas', 9))
        v3.addWidget(self.out_keys)
        root.addWidget(box3)

        buttons = QDialogButtonBox()
        self.btn_apply = QPushButton('把结果应用到配置')
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        buttons.addButton(self.btn_apply, QDialogButtonBox.AcceptRole)
        buttons.addButton('关闭', QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # -- 后端 --

    def _ensure_backend(self):
        if self._backend is not None:
            return self._backend
        from ..backend import SendInputBackend, foreground_window, load_user32
        api = load_user32()
        hwnd, title, _ = foreground_window(api)
        self._target_title = title
        self._backend = SendInputBackend(hwnd, keep_focus=False)
        return self._backend

    def _log(self, widget: QPlainTextEdit, text: str) -> None:
        widget.appendPlainText(text)
        widget.verticalScrollBar().setValue(
            widget.verticalScrollBar().maximum())

    # -- 1. 基准 --

    def _run_bench(self) -> None:
        from ..calibrate import bench_sendinput
        self.btn_bench.setEnabled(False)
        self.out_bench.clear()
        try:
            backend = self._ensure_backend()
            self._log(self.out_bench, '目标窗口：%r' % getattr(self, '_target_title', ''))
            self._log(self.out_bench, '测量中…')
            self.repaint()
            stats = bench_sendinput(backend, rounds=300)
            self._log(self.out_bench, '平均 %.0f µs   p50 %.0f µs   p95 %.0f µs   最大 %.0f µs'
                      % (stats['mean_us'], stats['p50_us'],
                         stats['p95_us'], stats['max_us']))
            if stats['mean_us'] > 400:
                self._log(self.out_bench,
                          '⚠ 单次注入偏慢，可能是杀毒软件实时扫描或进程优先级过低。')
            else:
                self._log(self.out_bench, '✓ 注入延迟正常。')
        except Exception as exc:
            self._log(self.out_bench, '失败：%s' % exc)
        finally:
            self.btn_bench.setEnabled(True)

    # -- 2. 最短音长 --

    def _start_probe(self) -> None:
        self.out_probe.clear()
        self._probe_low = 0.005
        self._probe_high = 0.12
        self._probe_best = None
        self._probe_round = 0
        self.btn_probe.setEnabled(False)
        QTimer.singleShot(50, self._probe_step)

    def _probe_step(self) -> None:
        from ..calibrate import _tap
        from ..instrument import Fingering

        if self._probe_high - self._probe_low <= 0.003:
            best = self._probe_best
            if best is None:
                self._log(self.out_probe, '× 即便 %.0f ms 也听不到，请检查窗口焦点与键位。'
                          % (self._probe_high * 1000))
            else:
                self._log(self.out_probe, '✓ 最短可识别音长约 %.1f ms' % (best * 1000))
                self.result_min_note = best
                self.btn_apply.setEnabled(True)
            self.btn_probe.setEnabled(True)
            self.btn_heard.setEnabled(False)
            self.btn_missed.setEnabled(False)
            return

        self._probe_round += 1
        self._probe_mid = (self._probe_low + self._probe_high) / 2
        try:
            backend = self._ensure_backend()
            fing = Fingering(self.instrument.keys[0])
            for _ in range(3):
                _tap(backend, fing, self._probe_mid)
                QApplication_process_events()
                import time
                time.sleep(0.28)
        except Exception as exc:
            self._log(self.out_probe, '失败：%s' % exc)
            self.btn_probe.setEnabled(True)
            return

        self._log(self.out_probe, '第 %d 轮：试探音长 %.1f ms —— 3 个音都听到了吗？'
                  % (self._probe_round, self._probe_mid * 1000))
        self.btn_heard.setEnabled(True)
        self.btn_missed.setEnabled(True)

    def _answer_probe(self, heard: bool) -> None:
        self.btn_heard.setEnabled(False)
        self.btn_missed.setEnabled(False)
        if heard:
            self._probe_best = self._probe_mid
            self._probe_high = self._probe_mid
        else:
            self._probe_low = self._probe_mid
        QTimer.singleShot(30, self._probe_step)

    # -- 3. 按键检查 --

    def _start_keys(self) -> None:
        from ..instrument import Fingering

        self.out_keys.clear()
        self._key_queue = [(k, ()) for k in self.instrument.keys]
        self._key_queue += [
            (self.instrument.keys[0], (self.instrument.lower,)),
            (self.instrument.keys[0], (self.instrument.semitone,)),
            (self.instrument.keys[0], (self.instrument.upper,)),
        ]
        self._key_ok = []
        self._key_bad = []
        self.btn_keys.setEnabled(False)
        QTimer.singleShot(30, self._key_step)

    def _key_step(self) -> None:
        from ..calibrate import _tap
        from ..instrument import Fingering
        import time

        if not self._key_queue:
            self._log(self.out_keys, '')
            self._log(self.out_keys, '通过 %d 项，失败 %d 项'
                      % (len(self._key_ok), len(self._key_bad)))
            if self._key_bad:
                self._log(self.out_keys, '失败：%s' % ', '.join(self._key_bad))
                self._log(self.out_keys,
                          '→ 若整组失败，多半是窗口未聚焦，或游戏键位与本程序不一致。')
            self.btn_keys.setEnabled(True)
            self.btn_key_yes.setEnabled(False)
            self.btn_key_no.setEnabled(False)
            if self._key_ok:
                self.btn_apply.setEnabled(True)
            return

        self._key_current = self._key_queue.pop(0)
        key, mods = self._key_current
        label = '+'.join(list(mods) + [key])
        self._key_label = label
        try:
            backend = self._ensure_backend()
            _tap(backend, Fingering(key, mods), 0.09)
        except Exception as exc:
            self._log(self.out_keys, '%-30s 发送失败：%s' % (label, exc))
            self._key_bad.append(label)
            QTimer.singleShot(30, self._key_step)
            return

        self._log(self.out_keys, '已发送 %-28s —— 游戏里有声音吗？' % label)
        self.btn_key_yes.setEnabled(True)
        self.btn_key_no.setEnabled(True)

    def _answer_key(self, heard: bool) -> None:
        self.btn_key_yes.setEnabled(False)
        self.btn_key_no.setEnabled(False)
        label = getattr(self, '_key_label', '?')
        (self._key_ok if heard else self._key_bad).append(label)
        QTimer.singleShot(30, self._key_step)

    # -- 应用 --

    def _apply(self) -> None:
        self.accept()


def QApplication_process_events() -> None:
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):

    def __init__(self, countdown: int, keep_focus: bool, minimize: bool,
                 scheduler: SchedulerConfig, parent=None, loop: bool = False,
                 theme_mode: str = 'dark'):
        super().__init__(parent)
        self.setWindowTitle('设置')
        self.resize(520, 420)
        self._scheduler = scheduler

        root = QVBoxLayout(self)

        box1 = QGroupBox('演奏')
        f1 = QFormLayout(box1)
        self.sp_countdown = QSpinBox()
        self.sp_countdown.setRange(0, 15)
        self.sp_countdown.setValue(countdown)
        self.sp_countdown.setSuffix(' 秒')
        self.sp_countdown.setToolTip('点「演奏」后留给你切回游戏的时间。设 0 表示立即开始。')
        f1.addRow('开场倒计时', self.sp_countdown)

        self.chk_focus = QCheckBox('目标窗口失去焦点时自动停止')
        self.chk_focus.setChecked(keep_focus)
        self.chk_focus.setToolTip('强烈建议保持开启，避免误操作到其他窗口。')
        f1.addRow('', self.chk_focus)

        self.chk_min = QCheckBox('开始演奏时自动最小化本窗口')
        self.chk_min.setChecked(minimize)
        f1.addRow('', self.chk_min)

        self.chk_loop = QCheckBox('循环播放（播完自动从头再来，F9 停止）')
        self.chk_loop.setChecked(loop)
        f1.addRow('', self.chk_loop)

        self.cb_theme = QComboBox()
        self.cb_theme.addItem('深色', 'dark')
        self.cb_theme.addItem('浅色', 'light')
        self.cb_theme.addItem('跟随系统', 'auto')
        tidx = self.cb_theme.findData(theme_mode)
        self.cb_theme.setCurrentIndex(max(0, tidx))
        self.cb_theme.setToolTip('立即生效。跟随系统会根据 Windows 的深浅色自动切换。')
        f1.addRow('外观', self.cb_theme)
        root.addWidget(box1)

        box2 = QGroupBox('调度精度')
        f2 = QFormLayout(box2)
        self.sp_spin = QDoubleSpinBox()
        self.sp_spin.setRange(0.0005, 0.02)
        self.sp_spin.setDecimals(4)
        self.sp_spin.setSingleStep(0.0005)
        self.sp_spin.setValue(scheduler.spin_window)
        self.sp_spin.setSuffix(' 秒')
        self.sp_spin.setToolTip(
            '等待下一个音符时的自旋窗口。越小节奏越准，但 CPU 占用越高。\n'
            '默认 0.002（2 ms）在绝大多数机器上够用。')
        f2.addRow('自旋窗口', self.sp_spin)

        self.chk_tight = QCheckBox('紧凑调度（音符间无空隙时省略一次等待）')
        self.chk_tight.setChecked(scheduler.tight)
        self.chk_tight.setToolTip('减少唤醒次数，连续音符会更稳。')
        f2.addRow('', self.chk_tight)

        self.cb_timing = QComboBox()
        self.cb_timing.addItem('稳健（30fps / 卡顿机器）', 'safe')
        self.cb_timing.addItem('标准（60fps 推荐）', 'standard')
        self.cb_timing.addItem('极限（高帧率）', 'aggressive')
        idx = self.cb_timing.findData(getattr(scheduler, 'timing', 'standard'))
        self.cb_timing.setCurrentIndex(max(0, idx))
        self.cb_timing.setToolTip(
            '目标游戏按帧采样按键状态：修饰键与音键同一帧到达会漏音。\n'
            '档位决定修饰键提前按下多久（稳健 70ms / 标准 40ms / 极限 20ms）。\n'
            '「稳健」还会在同键快速重复时顺延后音，保证每个音都读得到。')
        f2.addRow('输入兼容', self.cb_timing)
        root.addWidget(box2)

        box3 = QGroupBox('快捷方式')
        f3 = QVBoxLayout(box3)
        from .. import shortcut as _shortcut
        if not _shortcut.resolve_target():
            note = QLabel('开发环境下不可用：快捷方式需要指向打包后的 exe。')
            note.setObjectName('dim')
            note.setWordWrap(True)
            f3.addWidget(note)
        else:
            note = QLabel('在桌面和开始菜单各建一个「%s」快捷方式，'
                          '下次不用再翻文件夹。' % _shortcut.LINK_NAME)
            note.setObjectName('dim')
            note.setWordWrap(True)
            f3.addWidget(note)
            row = QHBoxLayout()
            self.btn_shortcut = QPushButton('创建快捷方式')
            self.btn_shortcut.clicked.connect(self._make_shortcut)
            row.addWidget(self.btn_shortcut)
            if _shortcut.already_exists():
                self.btn_shortcut.setText('重建快捷方式')
            row.addStretch(1)
            f3.addLayout(row)
            self.lbl_shortcut = QLabel('')
            self.lbl_shortcut.setObjectName('dim')
            self.lbl_shortcut.setWordWrap(True)
            f3.addWidget(self.lbl_shortcut)
        root.addWidget(box3)

        self.lbl_target = QLabel('')
        self.lbl_target.setObjectName('dim')
        self.lbl_target.setWordWrap(True)
        root.addWidget(self.lbl_target)
        self._refresh_target()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _refresh_target(self) -> None:
        try:
            from ..backend import foreground_window, load_user32
            api = load_user32()
            hwnd, title, pid = foreground_window(api)
            self.lbl_target.setText('当前前台窗口：%r (pid=%d)\n'
                                    '演奏目标就是倒计时结束那一刻的前台窗口。'
                                    % (title or '无', pid))
        except Exception as exc:
            self.lbl_target.setText('无法读取前台窗口：%s' % exc)

    def _make_shortcut(self) -> None:
        from .. import shortcut as _shortcut
        made = _shortcut.create()
        if made:
            self.lbl_shortcut.setText('✓ 已创建：\n' + '\n'.join(made))
            self.btn_shortcut.setText('重建快捷方式')
        else:
            self.lbl_shortcut.setText(
                '× 创建失败。可以手动操作：右键 大肥鲸洲琴工具包.exe '
                '→ 发送到 → 桌面快捷方式。')

    def values(self):
        return (self.sp_countdown.value(), self.chk_focus.isChecked(),
                self.chk_min.isChecked(), self.chk_loop.isChecked())

    def theme_mode(self) -> str:
        return self.cb_theme.currentData() or '''dark'''

    def scheduler(self) -> SchedulerConfig:
        cfg = SchedulerConfig()
        cfg.spin_window = self.sp_spin.value()
        cfg.tight = self.chk_tight.isChecked()
        cfg.timing = self.cb_timing.currentData() or 'standard'
        cfg.poll_interval = self._scheduler.poll_interval
        return cfg


# ---------------------------------------------------------------------------
# 帮助 / 关于
# ---------------------------------------------------------------------------

HELP_HTML = """
<h2>大肥鲸洲琴工具包 使用说明</h2>

<h3>它能做什么</h3>
<p>读取 MIDI / JSON 曲谱，把它编排成游戏口琴的按键序列，自动演奏。
本机不需要安装任何额外环境，也不联网。</p>

<h3>第一次使用</h3>
<ol>
<li>首次打开需要<b>输入卡密激活</b>：在激活窗口里填入卡密即可，
    激活一次以后这台机器直接就能用（验证在本机完成，不联网、不上传信息）。</li>
<li>打开游戏，取出口琴并进入可演奏界面。建议把游戏设为<b>无边框窗口</b>模式。</li>
<li>确认游戏内的按键设置与本程序一致（默认：音阶键 Z X C V B N M <code>,</code>；
    鼠标左键=降八度，中键=升半音，右键=升八度）。</li>
<li>回到本程序，点工具栏「<b>校准</b>」，先跑一次注入基准，再测最短可识别音长。</li>
<li>在左侧选一首曲子，点「<b>试听</b>」确认旋律没错。</li>
<li>点「<b>演奏</b>」，在倒计时内切回游戏口琴界面。演奏随即开始。</li>
</ol>

<h3>演奏中的热键</h3>
<table cellpadding="4">
<tr><td><b>F8</b></td><td>暂停 / 继续</td></tr>
<tr><td><b>F9</b> 或 <b>Esc</b></td><td>停止（会自动松开所有按键）</td></tr>
</table>
<p>这两个键是<b>全局热键</b>：游戏在前台时也收得到，而且不占用按键、不影响游戏
自己用 F8/F9。倒计时那 3 秒里按 F9 / Esc 可以取消这次演奏。<br>
切回本程序看进度会<b>自动暂停</b>（回游戏按 F8 继续，界面会写明原因）；切到别的
程序会直接停止并释放按键，防止把按键误发到别的窗口。</p>

<h3>四个按钮</h3>
<table cellpadding="4">
<tr><td><b>演奏</b></td><td>倒计时后把按键发送到游戏</td></tr>
<tr><td><b>演练</b></td><td>不发送按键，只在界面里走一遍时序，用来检查节奏和按键序列</td></tr>
<tr><td><b>试听</b></td><td>用本机合成器播放，不启动游戏，适合选曲调参</td></tr>
<tr><td><b>停止</b></td><td>立刻中断并释放按键</td></tr>
</table>

<h3>曲谱从哪来（按推荐顺序）</h3>
<p><b>1. 找一份现成 MIDI —— 最省事，效果通常也最好</b><br>
推荐到 midishow 等站点下载。三角洲口琴<b>只能弹单音轨</b>，挑谱时记住三招：</p>
<ul>
<li>搜「<b>原琴</b>」—— 这类谱按口琴音域编好，可完美适配三角洲；</li>
<li>搜「<b>调教用</b>」—— 基本都是单轨，适合口琴；</li>
<li>用站内的<b>音轨数筛选</b>，选「1 个音轨」的 —— 直接能用。</li>
</ul>
<p>下载后把 .mid 拖进窗口，选「单轨提取」（默认项）即入库。</p>

<p><b>2. 手上的 MIDI 是多轨的</b><br>
右上角「导入 MIDI / 曲谱」→ 选「单轨提取」：程序自动挑出主旋律轨、压成单音并
规整时值。多轨谱只有收成一条旋律，口琴才弹得出来。</p>

<p><b>3. 没有 MIDI 才走音频</b> —— 见下面「音频转曲谱」一节。它是备选方案：
从录音里用 AI 扒旋律，细节天然不如人工整理的 MIDI 稳。</p>

<p><b>其它入口</b>：右上角「导入简谱」粘键位简谱文本；选中曲子点「编辑乐谱」可以
自己改，改完点「保存曲谱」写回曲库。MIDI 入库时会让你三选一（推荐「单轨提取」）：
<b>直转</b>（保留声部，编曲简单、旋律清晰的曲子用）／<b>旋律化</b>（先按十六分网格
量化、每个时刻只留最高的音，编曲密集、声部多的曲子用）。两种都可以各来一份存着，
对比着听。</p>

<h3>音频转曲谱（没有 MIDI 时的备选，内置不用装外部工具）</h3>
<p>顶栏点「<b>音频转曲谱</b>」选一个音频文件，或者直接把音频拖进窗口 ——
程序内置了 GAME（OpenVPI 的歌声转 MIDI 模型），本机 CPU 推理，一首 4 分钟的歌
约 30 秒，转完自动加进曲库。支持 mp3 / wav / flac / ogg / m4a 等格式。</p>
<p>先说清定位：<b>能找现成的单轨 MIDI，就优先用 MIDI</b>。AI 从录音扒出来的
旋律，节奏与细节精度比不上人工整理的谱；同一条链路里它是「找不到谱时」的
方案，不是首选。</p>
<ul>
<li>跟着人声 / 主旋律唱的歌效果最好：它输出的是<b>单条旋律线</b>，多声部的曲子
    会被自动压成主旋律（口琴本来也只能弹一个音）。</li>
<li><b>网易云下载的歌是 .ncm 加密格式</b>，先解密成 mp3：装好 ncmdump 后
    <code>python -c "import ncmdump,glob;[ncmdump.dump(f) for f in glob.glob(chr(39)+chr(42)+chr(46)+chr(110)+chr(99)+chr(109)+chr(39))]"</code>
    在本机实测可用（本机已装 ncmdump）。</li>
<li>伴奏很满的歌会混进一些杂音 —— 先用「分离人声」那套处理一遍再转，会干净很多。</li>
<li>转出来的音常见 ±1 半音漂移，在编辑器里点「调内吸附」就能修。</li>
<li>命令行可以批量转：<code>大肥鲸洲琴工具包.exe transcribe 目录 -o 输出目录</code></li>
</ul>

<h3>用外部工具（进阶）：GAME 一键包 + 人声分离</h3>
<p>内置转谱已经覆盖绝大多数情况；这一节留给想自己控制细节的人 —— 比如先做
高质量人声分离，或者一次处理一大批素材。GAME 是 OpenVPI 做的歌声转 MIDI
模型（免费开源），官网一键包与本程序内置的是同一个模型。</p>

<p><b>1. 下载解压（Windows，免安装，不需要 Python）</b><br>
到 GitHub 仓库 <code>openvpi/dataset-tools</code> 的 Releases 下载
<code>GAME_Inference_Demo_Program_0605.zip</code>（约 73 MB），解压到任意目录。
包里自带 small 规格模型（<code>model/GAME-1.0.3-small-onnx</code>）。<br>
想要更好的效果：到 <code>openvpi/GAME</code> 的 Releases 下载 v1.0.3 的 medium 模型，
解压到同一目录，用「Browse...」把 Model Path 指过去（便携包只带 small）。</p>

<p><b>2. 转谱</b>：双击 <code>GameInfer.exe</code>，Model Path 一般已自动填好 →
Input Audio File 选音频 → Output MIDI File 选输出位置 → 点 <b>Convert</b>。</p>
<ul>
<li>官方建议用<b>单声道 WAV</b>（多声道 / FLAC / MP3 只算测试支持）。</li>
<li><b>别直接喂整首歌</b>：GAME 靠人声边界把音频切成不超过 60 秒的片段，混着
    伴奏时它切不出来，会直接报 <i>Slice duration exceeds 60 seconds</i>
    （进度卡在 10% 左右然后弹错误框）。两个办法：
    <ul>
    <li>先把人声分离出来再整首转 —— 效果最好。分离工具都是开源的：
        <ul>
        <li><b>UVR5</b>（图形界面）：GitHub 搜 <code>Anjok07/ultimatevocalremovergui</code>，
            选 MDX-NET 里的 <code>UVR-MDX-NET-Inst_HQ_3</code>，只输出 vocals；</li>
        <li><b>demucs</b>（命令行）：<code>pip install demucs</code> 之后
            <code>demucs --two-stems=vocals 歌曲.mp3</code>，人声在
            <code>separated/htdemucs/歌曲/vocals.wav</code>。</li>
        </ul></li>
    <li>或者把音频手动切成 <b>≤55 秒</b>的小段，逐段转换，最后按顺序把
        MIDI 拼起来（实测可行，代价是切点处可能有个别音被截断）。</li>
    </ul></li>
<li>实测速度（CPU）：55 秒的一段约 10 秒转完；纯人声的短片段更快，几秒钟。
    Execution Provider 切成 DirectML 可以走显卡。</li>
</ul>

<p><b>3. 导进本程序</b>：把生成的 .mid 拖进窗口，选「旋律化」或「直转」即可。
扒出来的音常见 ±1 半音漂移，在编辑器里点「调内吸附」就能修；没做人声分离时
常会混进几段偏低的伴奏音（比如 E2 一带），在编辑器里框选删掉即可。</p>
<p class="muted">GAME 只输出音高与时值，不含音高曲线；想看转出来的 MIDI 长什么样，
可以拖到 signalmidi.app 上预览。</p>

<h3>曲库：收藏 / 删除</h3>
<p>曲目右边的星标点一下就是收藏，标题右边的「全部 / 收藏」只看收藏的那几首，
右键菜单里也能收藏 / 取消收藏。<br>
删除（右键 → 从曲库删除）会记一笔：之后程序目录里的同名曲目不会再被自动导回，
重启也不会冒出来。想找回就把同名文件重新导入一次，这条记录会自动撤销。</p>

<h3>调参思路</h3>
<ul>
<li><b>手跟不上</b> → 降低「演奏速度」到 0.8</li>
<li><b>音符粘连、分不清</b> → 降低「音符连贯度」到 0.7 左右</li>
<li><b>长乐句吹不过来</b> → 加大「句尾换气」到 150 ms</li>
<li><b>总有音弹不出来</b> → 点「策略分析」，换「切换最少」的策略；
    或用「校准」测出真实的最短音长后调大它</li>
<li><b>音域不够</b> → 打开「折叠八度」，或用「移调」把整曲挪进音域</li>
<li><b>多音轨混在一起不好听</b> → 在「音轨」里挑覆盖率最高的那一轨</li>
</ul>

<h3>常见问题</h3>
<p><b>游戏里完全没声音？</b><br>
先确认倒计时结束时游戏在前台（这是演奏目标）；再确认游戏以普通权限运行——
如果游戏是管理员权限，本程序也必须以管理员身份运行，否则 Windows 会拦下模拟按键。</p>

<p><b>偶尔漏音？</b><br>
在「设置」里把自旋窗口调小到 0.001，并关闭杀毒软件的实时扫描试试。</p>

<p><b>按键卡住了？</b><br>
按 F9 或点「停止」，程序会逆序释放所有按过的键。
极端情况下手动按一下卡住的键即可。</p>
"""


class KeymapDialog(QDialog):
    """键位 / 音高 / 快捷键速查。"""

    def __init__(self, instrument, countdown: int = 3, keep_focus: bool = True,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle('键位 / 音高 / 快捷键')
        self.resize(600, 640)
        root = QVBoxLayout(self)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(self._build_html(instrument, countdown, keep_focus))
        root.addWidget(browser, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

    @staticmethod
    def _build_html(instrument, countdown: int, keep_focus: bool) -> str:
        from ..instrument import STEPS, note_name

        keys = []
        for key, step in zip(instrument.keys, STEPS):
            pitch = instrument.base + step
            keys.append(
                '<tr><td align="center"><b>%s</b></td>'
                '<td align="center" style="color:%s">%d</td>'
                '<td align="center">%s</td></tr>'
                % (key.upper(), C.MUTED, pitch, note_name(pitch)))

        lo, hi = instrument.playable_range()
        mods = [
            (instrument.lower, '按住 · 降一个八度'),
            (instrument.semitone,
             '按住 · 升半音' if instrument.semitone_step > 0 else '按住 · 降半音'),
            (instrument.upper, '按住 · 升一个八度'),
        ]
        mod_rows = ''.join(
            '<tr><td>%s</td><td style="color:%s">%s</td></tr>'
            % (name, C.MUTED, desc) for name, desc in mods)

        return """
        <style>
          body { color: %s; font-family: 'Microsoft YaHei UI'; font-size: 13px; }
          h3 { color: %s; margin: 4px 0 6px 0; }
          table { border-collapse: collapse; width: 100%%; }
          td { padding: 5px 8px; border-bottom: 1px solid %s; }
          code { color: %s; }
          .muted { color: %s; }
        </style>

        <h3>音阶键</h3>
        <table>
          <tr><td class="muted">按键</td><td class="muted" align="center">MIDI</td>
              <td class="muted" align="center">音名</td></tr>
          %s
        </table>

        <h3>按住式修饰键（鼠标）</h3>
        <table>%s</table>

        <h3>音域</h3>
        <p class="muted">可演奏 <b style="color:%s">%d</b> 个音高，
        范围 %s .. %s（MIDI %d..%d）。</p>

        <h3>控制快捷键</h3>
        <table>
          <tr><td><b>F8</b></td><td class="muted">开始 / 暂停 / 继续</td></tr>
          <tr><td><b>F9</b></td><td class="muted">停止，并释放所有按住的键</td></tr>
          <tr><td><b>Esc</b></td><td class="muted">停止（界面有焦点时）</td></tr>
        </table>

        <h3>当前设置</h3>
        <table>
          <tr><td>开场倒计时</td><td class="muted">%d 秒</td></tr>
          <tr><td>失去焦点自动停止</td><td class="muted">%s</td></tr>
        </table>

        <h3>说明</h3>
        <p class="muted">
          键位与游戏内设置不一致时，请先改游戏，或在
          <code>config.json</code> 的 <code>instrument</code> 段里同步修改。<br>
          默认 base = %d，即 %s 对应 %s。
        </p>
        # 参数顺序 = 占位符在 HTML 里的文档顺序：
        # 样式块 5 色 → 键位行 → 修饰键行 → 「可演奏」高亮色 → 数值序列
        """ % (C.TEXT, C.ACCENT, C.BORDER, C.ACCENT, C.MUTED,
               '\n'.join(keys), mod_rows, C.ROLL_NOTE,
               len(instrument.fingerings()),
               note_name(lo), note_name(hi), lo, hi,
               countdown, '是' if keep_focus else '否',
               instrument.base, instrument.keys[0].upper(),
               note_name(instrument.base))


class HelpDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('使用说明')
        self.resize(760, 640)
        root = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(HELP_HTML)
        root.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)


#: 公告标识：**改这个值就等于发一条新公告** —— 老用户升级后会再看一次。
#: 读已标记记录在 QSettings 的 announce_seen 里（存标识字符串，不存布尔）。
ANNOUNCE_ID = '2026-09-20-add-score-guide'

ANNOUNCEMENT_HTML = """
<h2>添加曲谱的三种方式</h2>
<p>三角洲口琴<b>只能弹单音轨</b>。按下面的顺序找谱，效果从好到差——</p>

<h3>1 · 找一份现成 MIDI（推荐）</h3>
<p>到 midishow 等站点下载，挑谱三招：</p>
<ul>
<li>搜「<b>原琴</b>」—— 这类谱按口琴音域编好，可完美适配三角洲；</li>
<li>搜「<b>调教用</b>」—— 基本都是单轨，适合口琴；</li>
<li>用站内的<b>音轨数筛选</b>，选「1 个音轨」的。</li>
</ul>
<p>下载后把 .mid 拖进窗口 → 选「<b>单轨提取</b>」（默认项）→ 自动入库。</p>

<h3>2 · 手上的 MIDI 是多轨的</h3>
<p>右上角「导入 MIDI / 曲谱」→ 选「单轨提取」：程序自动挑出主旋律轨、
压成单音并规整时值。多轨谱只有收成一条旋律，口琴才弹得出来。</p>

<h3>3 · 没有 MIDI 才走音频</h3>
<p>顶栏「音频转曲谱」从录音里用 AI 扒旋律。它是<b>备选方案</b>：
节奏与细节精度不如人工整理的谱，能找谱就优先找谱。</p>

<p><b>另有两条小路</b>：右上角「导入简谱」直接粘贴键位简谱文本；
选中曲子点「编辑乐谱」可以自己改谱。</p>
<p style="color:#888">挑谱技巧与全部参数说明，见「齿轮菜单 → 使用说明」。</p>
"""


class AnnouncementDialog(QDialog):
    """公告：添加曲谱的方式。首次运行或版本更新后自动显示一次。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('公告 · 添加曲谱的三种方式')
        self.resize(640, 560)
        root = QVBoxLayout(self)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(ANNOUNCEMENT_HTML)
        root.addWidget(browser)

        buttons = QDialogButtonBox()
        help_btn = buttons.addButton('查看完整说明',
                                     QDialogButtonBox.ActionRole)
        ok_btn = buttons.addButton('知道了', QDialogButtonBox.AcceptRole)
        help_btn.clicked.connect(self._open_help)
        ok_btn.clicked.connect(self.accept)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

    def _open_help(self) -> None:
        HelpDialog(self).exec()


class ActivationDialog(QDialog):
    """卡密激活：输入卡密 → 校验 → 写入本机凭证。

    用在启动流程里（主窗口之前）：exec() 返回 Accepted 表示已激活。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('卡密激活')
        self.setMinimumWidth(470)
        root = QVBoxLayout(self)
        root.setSpacing(12)

        title = QLabel('<h2>卡密激活</h2>')
        root.addWidget(title)

        info = QLabel(
            '本工具需要卡密激活后在本机使用。<br>'
            '激活一次即可，以后打开这台机器直接就能用 ——'
            '验证完全在本机完成，不联网、不上传任何信息。')
        info.setObjectName('muted')
        info.setWordWrap(True)
        root.addWidget(info)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText('请输入卡密')
        self.edit.returnPressed.connect(self._try_activate)
        root.addWidget(self.edit)

        self.hint = QLabel('')
        self.hint.setObjectName('muted')
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        buttons = QDialogButtonBox()
        ok = buttons.addButton('激活', QDialogButtonBox.AcceptRole)
        quit_btn = buttons.addButton('退出', QDialogButtonBox.RejectRole)
        ok.clicked.connect(self._try_activate)
        quit_btn.clicked.connect(self.reject)
        root.addWidget(buttons)

        from ..activation import machine_id
        mid = QLabel('本机机器码：%s（激活遇到问题时报给作者核对）'
                     % machine_id())
        mid.setObjectName('muted')
        mid.setWordWrap(True)
        root.addWidget(mid)

    def _try_activate(self) -> None:
        from ..activation import activate

        text = self.edit.text()
        if not text.strip():
            self.hint.setText('请输入卡密。')
            return
        if activate(text):
            self.hint.setText('激活成功，正在启动…')
            self.accept()
            return
        self.hint.setText('卡密不正确，请检查后重试。')
        self.edit.selectAll()
        self.edit.setFocus()


class AboutDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('关于')
        self.resize(520, 400)
        root = QVBoxLayout(self)

        title = QLabel('<h2>大肥鲸洲琴工具包</h2>')
        root.addWidget(title)
        root.addWidget(QLabel('版本 %s' % __version__))

        text = QTextBrowser()
        text.setOpenExternalLinks(True)
        text.setHtml("""
        <p>《三角洲行动》口琴自动演奏工具。</p>

        <p>通过 Win32 <code>SendInput</code> 发送<b>扫描码</b>模拟键鼠，
        因此能被游戏（DirectInput / Raw Input）正确识别。</p>

        <h4>技术要点</h4>
        <ul>
        <li>纯标准库实现，无需第三方依赖即可运行</li>
        <li>MIDI 解析与音频合成为自研实现</li>
        <li>指法选择支持 4 种策略，其中 <code>optimal</code> 使用动态规划求全局最优</li>
        <li>界面基于 PySide6 (Qt)</li>
        </ul>

        <h4>免责声明</h4>
        <p>本工具仅供个人学习与娱乐使用。模拟键鼠属于游戏自动化的灰色地带，
        请自行确认是否符合游戏用户协议，并避免在竞技场景使用。
        使用本工具产生的任何后果由使用者自行承担。</p>
        """)
        root.addWidget(text, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)


# ---------------------------------------------------------------------------
# 音频转曲谱
# ---------------------------------------------------------------------------

_KEY_NAMES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')


# ---------------------------------------------------------------------------
# MIDI 导入方式二选一
# ---------------------------------------------------------------------------

class _ImportOptionCard(QFrame):
    """可整体点击的选项卡：点卡片任意位置 = 选中它。

    单选钮本身在卡片内部，卡片与单选钮不同父级，光靠 QRadioButton 的
    同父互斥不成立 —— 互斥由对话框里的 QButtonGroup 保证，这里负责
    把「点卡片空白处」也变成选中。"""

    def __init__(self, radio, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        self.setCursor(Qt.PointingHandCursor)
        self._radio = radio

    def mousePressEvent(self, event) -> None:
        self._radio.setChecked(True)
        super().mousePressEvent(event)


class ImportModeDialog(QDialog):
    """MIDI 入库二选一：直转（保留声部）/ 旋律化（先简化再入库）。

    两条链各有擅长的曲子 —— 编曲简单/旋律清晰的用直转，多声部密集的
    用旋律化（先网格量化 + 取最高音，把取舍前置，见 melody.melodize）。
    """

    def __init__(self, files: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle('选择 MIDI 导入方式')
        self.setMinimumWidth(620)
        self.mode = 'track'     # 默认落在推荐项：单轨提取（原琴谱 / 网上 MIDI）

        v = QVBoxLayout(self)
        v.setSpacing(12)

        names = '、'.join(os.path.basename(f) for f in files[:3])
        if len(files) > 3:
            names += ' 等 %d 个文件' % len(files)
        head = QLabel('《%s》要用哪种方式入库？' % names)
        head.setObjectName('section')
        head.setWordWrap(True)
        v.addWidget(head)

        self._buttons = []
        # 单选钮分属各自的卡片，必须用 QButtonGroup 显式互斥；
        # 否则会出现「两个都选中」或「两个都没选」。
        group = QButtonGroup(self)
        group.setExclusive(True)
        options = [
            ('direct', '直转 · 保留声部',
             '整份 MIDI 原样进入编排：全部音高按八度折叠收进口琴的音域，'
             '节奏保持文件里的原始精度。旋律声部清晰、编曲不复杂的曲子'
             '适合这一种，结果通常最接近原曲。'),
            ('track', '单轨提取 · 推荐（网上的 MIDI / 原琴谱）',
             '三角洲口琴只能弹单音轨，而下载来的 MIDI 大多是钢琴或多声部'
             '编曲 —— 这一步挑出主旋律轨、压成单音，并把时值对齐到 120BPM'
             '的十六分网格。实测与人工整理好的口琴谱旋律一致度 0.11 半音/步。'),
            ('melody', '旋律化 · 先简化再入库',
             '先按十六分音符网格量化，每个时刻只保留最高的那个音，'
             '拼成一条干净的单旋律线（低音与内声部会被舍弃），再进编排。'
             '编曲密集、声部多、直接转容易糊成一团的曲子适合这一种 —— '
             '把「留哪些音」的取舍提前做完，结果更干净。'),
        ]
        for value, title, desc in options:
            rb = QRadioButton(title)
            rb.setObjectName('section')
            card = _ImportOptionCard(rb)
            cv = QVBoxLayout(card)
            cv.setContentsMargins(14, 10, 14, 10)
            cv.setSpacing(4)
            cv.addWidget(rb)
            desc_lbl = QLabel(desc)
            desc_lbl.setObjectName('muted')
            desc_lbl.setWordWrap(True)
            cv.addWidget(desc_lbl)
            group.addButton(rb)
            rb.toggled.connect(lambda on, val=value: self._pick(val, on))
            if value == self.mode:
                rb.setChecked(True)
            v.addWidget(card)
            self._buttons.append(rb)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        v.addWidget(box)

    def _pick(self, value: str, on: bool) -> None:
        if on:
            self.mode = value
