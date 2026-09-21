"""主窗口。"""
from __future__ import annotations

import os
import time
from typing import Optional

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QMenu, QMessageBox, QProgressDialog,
                               QPushButton, QSizePolicy, QSlider,
                               QStackedWidget, QStatusBar, QToolButton,
                               QVBoxLayout, QWidget)

from .. import __version__
from ..arrange import (Arrangement, Options, arrange, arrange_from_notes)
from ..backend import DryRunBackend, foreground_window, load_user32
from ..config import Config
from ..edit import EditDoc
from ..fingering import total_cost
from ..instrument import Instrument, note_name
from ..player import Player, SchedulerConfig, State
from ..score import Score, load_score
from .. import synth
from .editor import EditorPanel
from .panels import ParamCard, Sidebar
from .theme import (C, MODE_NAMES, apply_theme, combo_style, icon_gear,
                    icon_moon, icon_sun)
from .widgets import KeyStrip, ModifierStrip, PianoRoll

APP_TITLE = '大肥鲸洲琴工具包 —— 三角洲行动口琴自动演奏'
REARRANGE_DELAY = 220        # 参数改动后延迟重算（毫秒）
EDIT_DELAY = 140             # 编辑改动后延迟重算（毫秒）

PREVIEW_TIP = '音符宽度代表时值 · 颜色区分指法复杂度'
EDIT_TIP = ('空白拖动框选 · Shift+点选多选 · 整段删除 / 剪掉时间：时间轴点两次 · '
            'Ctrl+S 保存')


def _fmt_time(seconds: float) -> str:
    total = max(int(round(seconds)), 0)
    return '%02d:%02d' % (total // 60, total % 60)


class _PreflightAbort(Exception):
    """播放前自检被用户取消 —— 静默吞掉，不打扰。"""


class MainWindow(QMainWindow):

    def __init__(self, config: Config, library_dir: str):
        super().__init__()
        self.config = config
        self.instrument, self.options, self.cost, self.scheduler = config.build()
        self.library_dir = library_dir

        self.score: Optional[Score] = None
        self.plan: Optional[Arrangement] = None
        self.score_path: Optional[str] = None

        self.play_worker = None
        self.synth_worker = None
        self.load_worker = None
        self.analyze_worker = None
        self.audio = synth.Player()
        self.audio_timer = QTimer(self)
        # 16ms ≈ 60fps，播放头才跟得顺。配合卷帘的静态层缓存，
        # 每帧实际只重画播放头和当前音符，不会因为提帧而吃满 CPU。
        self.audio_timer.setInterval(16)
        self.audio_timer.timeout.connect(self._poll_audio)

        self._countdown_left = 0
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._countdown_step)
        # 倒计时那几秒焦点已经在游戏里了，界面的 QShortcut 收不到按键，
        # 得自己轮询一下全局键状态，不然 F9 / Esc 取消不了
        self._hotkey_probe = None       # player.HotkeyWatcher，用到才建
        self._countdown_probe = QTimer(self)
        self._countdown_probe.setInterval(33)
        self._countdown_probe.timeout.connect(self._probe_countdown_cancel)

        self.settings = QSettings('GTIHarmonica', 'app')
        self.keep_focus = self.settings.value('keep_focus', True, bool)
        self.countdown_seconds = int(self.settings.value('countdown', 3))
        self.minimize_on_play = self.settings.value('minimize_on_play', True, bool)
        self.loop_play = self.settings.value('loop_play', False, bool)
        # 游戏在前台按 F8 直接开始演奏（设置里可关）
        self.f8_standby = self.settings.value('f8_standby', True, bool)
        self._standby_watcher = None
        self._standby_timer = QTimer(self)
        self._standby_timer.setInterval(60)
        self._standby_timer.timeout.connect(self._standby_check)
        self._standby_timer.start()
        self.theme_mode = self.settings.value('theme_mode', 'light')

        # 演奏悬浮窗：切到游戏后仍然看得到进度（位置记忆在 settings）
        from .overlay import OverlayWindow
        self.overlay = OverlayWindow(self.settings)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(REARRANGE_DELAY)
        self._debounce.timeout.connect(self.re_arrange)

        #: 'preview' | 'edit' —— 同一个卡片区域的两个视图
        self.mode = 'preview'
        #: 编辑文档。只在载入新曲目时重建，来回切视图不会丢掉编辑。
        self.editor_doc: Optional[EditDoc] = None
        self._edit_debounce = QTimer(self)
        self._edit_debounce.setSingleShot(True)
        self._edit_debounce.setInterval(EDIT_DELAY)
        self._edit_debounce.timeout.connect(self.apply_editor)

        self._build_ui()
        self._load_config_into_ui()

        self.library.refresh()
        self._update_actions()
        self._set_status('就绪。先在游戏里取出口琴，再选曲目点「演奏」。')

    # ------------------------------------------------------------------
    # 界面构建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle(APP_TITLE)
        # 允许把文件拖进来：曲谱直接入库，音频走转录流程
        self.setAcceptDrops(True)
        self.resize(1420, 900)
        self.setMinimumSize(1080, 680)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- 左侧边栏 ---
        self.library = Sidebar(self.library_dir, __version__)
        self.library.setFixedWidth(288)
        self.library.selected.connect(self.on_library_selected)
        self.library.activated.connect(self.on_library_selected)
        self.library.imported.connect(lambda paths: self._set_status(
            '已导入 %d 首曲目' % len(paths)))
        self.library.showKeymap.connect(self.show_keymap)
        outer.addWidget(self.library)

        # --- 右侧主区 ---
        main = QWidget()
        outer.addWidget(main, 1)
        mv = QVBoxLayout(main)
        mv.setContentsMargins(26, 18, 26, 12)
        mv.setSpacing(13)

        # --- 顶部动作条 ---
        # 原来收在「⋯」里的高频项（策略分析 / 校准 / 主题切换）平铺出来，
        # 设置 / 使用说明 / 关于这类低频项收进右侧齿轮。安静按钮（ghost）
        # 承担工具类动作，只有「保存编排曲谱」保留底色，主次一眼分明。
        # 注意用 lambda 包一层：clicked 会带一个 bool 参数，
        # 直接连过去会当成方法的第一个形参（比如 initial_file）。
        bar = QFrame()
        bar.setObjectName('topBar')
        top = QHBoxLayout(bar)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(7)
        eyebrow = QLabel('大肥鲸洲琴工具包 / 旋律练习')
        eyebrow.setObjectName('eyebrow')
        top.addWidget(eyebrow)
        top.addStretch(1)

        self.btn_analysis = QPushButton('策略分析')
        self.btn_analysis.setObjectName('ghost')
        self.btn_analysis.setToolTip(
            '对比各种演奏策略的按键次数与修饰键切换成本，可一键套用')
        self.btn_analysis.clicked.connect(lambda: self.show_analysis())
        top.addWidget(self.btn_analysis)

        self.btn_calibrate = QPushButton('校准')
        self.btn_calibrate.setObjectName('ghost')
        self.btn_calibrate.setToolTip('逐个键位试音，核对游戏里的按键映射')
        self.btn_calibrate.clicked.connect(lambda: self.show_calibrate())
        top.addWidget(self.btn_calibrate)

        top.addWidget(self._make_vsep())

        self.btn_import = QPushButton('导入 MIDI / 曲谱')
        self.btn_import.setObjectName('ghost')
        self.btn_import.setToolTip(
            '选择 MIDI 或曲谱文件加入曲库。\n'
            'MIDI 入库时可选「直转」或「旋律化（心似烟火链路）」')
        self.btn_import.clicked.connect(lambda: self._import_from_dialog())
        top.addWidget(self.btn_import)

        self.btn_jianpu = QPushButton('导入简谱')
        self.btn_jianpu.setObjectName('ghost')
        self.btn_jianpu.setToolTip(
            '粘贴键位简谱文本（支持原琴谱 / 呱呱谱 / jpeditor），\n'
            '直接转成可弹的曲谱')
        self.btn_jianpu.clicked.connect(
            lambda: self.open_jianpu_dialog())
        top.addWidget(self.btn_jianpu)

        # 音频转曲谱：内置 GAME ONNX 模型，拖入歌文件也能触发
        self.btn_audio = QPushButton('音频转曲谱')
        self.btn_audio.setObjectName('ghost')
        self.btn_audio.setToolTip(
            '选一个音频文件（mp3 / wav / flac / ogg），\n'
            '直接识别出人声/主旋律，转成曲谱加入曲库。\n'
            '也可以把音频文件直接拖进窗口。')
        self.btn_audio.clicked.connect(self._transcribe_from_dialog)
        top.addWidget(self.btn_audio)

        self.btn_save_arrangement = QPushButton('保存编排曲谱')
        self.btn_save_arrangement.setToolTip(
            '保存全部音轨的编排和裁切，可另存新曲目或覆盖原曲')
        self.btn_save_arrangement.clicked.connect(self.save_arrangement)
        top.addWidget(self.btn_save_arrangement)

        top.addWidget(self._make_vsep())

        self.btn_theme = QToolButton()
        self.btn_theme.setObjectName('iconBtn')
        self.btn_theme.setCursor(Qt.PointingHandCursor)
        self.btn_theme.setToolTip('切换浅色 / 深色外观（设置里可选跟随系统）')
        self.btn_theme.clicked.connect(self.toggle_theme)
        top.addWidget(self.btn_theme)

        self.btn_gear = QToolButton()
        self.btn_gear.setObjectName('iconBtn')
        self.btn_gear.setCursor(Qt.PointingHandCursor)
        self.btn_gear.setPopupMode(QToolButton.InstantPopup)
        self.btn_gear.setToolTip('设置 · 使用说明 · 关于')
        gear_menu = QMenu(self.btn_gear)
        gear_menu.addAction('设置…', self.show_settings)
        gear_menu.addSeparator()
        gear_menu.addAction('使用说明', self.show_help)
        gear_menu.addAction('如何添加曲谱', self.show_add_guide)
        gear_menu.addAction('关于', self.show_about)
        self.btn_gear.setMenu(gear_menu)
        top.addWidget(self.btn_gear)

        self._refresh_toolbar_icons()
        mv.addWidget(bar)

        self.title_label = QLabel('未选择曲目')
        self.title_label.setObjectName('title')
        mv.addWidget(self.title_label)

        self.info = QLabel('从左侧选一首曲子，或把 MIDI 文件拖进窗口。')
        self.info.setObjectName('muted')
        self.info.setWordWrap(True)
        mv.addWidget(self.info)

        # --- 卡片：旋律预览 ---
        card = QFrame()
        card.setObjectName('card')
        cv = QVBoxLayout(card)
        cv.setContentsMargins(20, 15, 20, 16)
        cv.setSpacing(10)

        head = QHBoxLayout()
        cap = QLabel('旋律预览')
        cap.setObjectName('section')
        head.addWidget(cap)
        head.addStretch(1)
        self.mode_switch = self._build_mode_switch()
        head.addWidget(self.mode_switch)
        head.addStretch(1)
        self.tip_label = QLabel(PREVIEW_TIP)
        self.tip_label.setObjectName('keyHint')
        head.addWidget(self.tip_label)
        cv.addLayout(head)

        # 同一个位置放两个视图：预览看旋律轮廓，编辑直接改乐谱。
        # 用 stack 而不是两个独立卡片 —— 它们是同一件事的两种视角，
        # 并排放会让人以为要同时看两个。
        self.stack = QStackedWidget()

        preview_page = QWidget()
        pv = QVBoxLayout(preview_page)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(10)
        self.roll = PianoRoll()
        self.roll.seekRequested.connect(self._seek_from_roll)
        pv.addWidget(self.roll, 1)

        self.mods = ModifierStrip()
        self.mods.configure(self.instrument)
        pv.addWidget(self.mods)
        self.stack.addWidget(preview_page)

        self.editor = EditorPanel()
        self.editor.changed.connect(self._on_editor_changed)
        self.editor.statusMessage.connect(self._set_status)
        self.editor.seekRequested.connect(self._seek_from_roll)
        self.editor.saveRequested.connect(self.save_arrangement)
        self.stack.addWidget(self.editor)

        cv.addWidget(self.stack, 1)

        # --- 播放进度：贴着乐谱放，预览/编辑两个视图共用 ---
        # 之前放在窗口最底部（参数卡下面），和乐谱隔了整整两张卡，
        # 看起来就像个无关的设置项；拖动跳转是乐谱的基本交互，
        # 必须让视线不用跳远就能找到它。
        tl = QHBoxLayout()
        tl.setSpacing(10)
        self.time_left = QLabel('00:00.000')
        self.time_left.setObjectName('mono')
        tl.addWidget(self.time_left)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setValue(0)
        self.slider.setToolTip('拖动跳转播放位置（试听中即时生效）')
        self.slider.sliderMoved.connect(self._seek_from_slider)
        tl.addWidget(self.slider, 1)

        self.time_right = QLabel('00:00')
        self.time_right.setObjectName('mono')
        tl.addWidget(self.time_right)
        cv.addLayout(tl)

        self.time_hint = QLabel('拖动进度条或点击时间轴可跳转 · 演奏中请先暂停')
        self.time_hint.setObjectName('keyHint')
        self.time_hint.setAlignment(Qt.AlignRight)
        cv.addWidget(self.time_hint)

        # 按键行放在两个视图**之外**：编辑的时候同样想知道「这个音落在哪个
        # 键上」，演奏时它还要跟着高亮。塞在预览页里的话，编辑视图下的键位
        # 联动就完全看不到了。
        self.keys = KeyStrip()
        self.keys.configure(self.instrument)
        cv.addWidget(self.keys)

        mv.addWidget(card, 1)

        # --- 卡片：参数 ---
        self.params = ParamCard(self.options, self.cost)
        self.params.changed.connect(self._on_params_changed)
        self.params.previewTimbreChanged.connect(self._on_timbre_changed)
        mv.addWidget(self.params)

        self._build_controls(mv)

        self.setStatusBar(QStatusBar())
        self.status_label = QLabel('')
        self.status_label.setObjectName('status')
        self.statusBar().addWidget(self.status_label, 1)

        # F8/F9 应用内快捷键：此前 F8/F9 只在演奏中由全局轮询接管，
        # 窗口里按了什么都不发生（提示文字却写着「开始/暂停」）。
        # 演奏中焦点在游戏上，仍走 player 的全局热键，两者不冲突。
        from PySide6.QtGui import QKeySequence, QShortcut
        sc = QShortcut(QKeySequence('F8'), self)
        sc.setContext(Qt.ApplicationShortcut)
        sc.activated.connect(self._on_f8)
        sc = QShortcut(QKeySequence('F9'), self)
        sc.setContext(Qt.ApplicationShortcut)
        sc.activated.connect(self.stop_all)

    # ------------------------------------------------------------------
    # 编排预览 / 乐谱编辑
    # ------------------------------------------------------------------

    def _make_vsep(self) -> QFrame:
        """顶栏动作分组之间的细分隔线。"""
        line = QFrame()
        line.setObjectName('vsep')
        line.setFixedSize(1, 16)
        return line

    def _refresh_toolbar_icons(self) -> None:
        """重画顶栏图标。图标 pixmap 缓存了当前主题下的颜色，
        深浅切换后必须重新生成，否则留在旧配色里。"""
        self.btn_theme.setIcon(icon_sun() if C.mode == 'light'
                               else icon_moon())
        self.btn_gear.setIcon(icon_gear())

    def _build_mode_switch(self) -> QWidget:
        """分段控件：同一个位置的两个视图。"""
        group = QFrame()
        group.setObjectName('segGroup')
        row = QHBoxLayout(group)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)

        self.btn_mode_preview = QPushButton('编排预览')
        self.btn_mode_edit = QPushButton('乐谱编辑')
        for btn, mode in ((self.btn_mode_preview, 'preview'),
                          (self.btn_mode_edit, 'edit')):
            btn.setObjectName('seg')
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, m=mode: self.set_mode(m))
            row.addWidget(btn)
        self.btn_mode_preview.setChecked(True)
        self.btn_mode_edit.setToolTip(
            '直接编辑乐谱：拖动改音高与位置、拖两端改时值、\n'
            '框选后一键把碎片合并成长音')
        return group

    def set_mode(self, mode: str) -> None:
        if mode not in ('preview', 'edit'):
            return
        if mode == 'edit' and not self._ensure_editor_doc():
            self.btn_mode_preview.setChecked(True)
            self._set_status('先选一首曲子，再进入乐谱编辑')
            return

        self.mode = mode
        self.stack.setCurrentIndex(1 if mode == 'edit' else 0)
        self.btn_mode_preview.setChecked(mode == 'preview')
        self.btn_mode_edit.setChecked(mode == 'edit')
        self.tip_label.setText(EDIT_TIP if mode == 'edit' else PREVIEW_TIP)
        # 顶部按钮在两种模式里保存的东西不同，文字必须跟着换：
        # 编辑模式下它和编辑器里的「保存曲谱 / Ctrl+S」是同一个动作。
        self.btn_save_arrangement.setText(
            '保存编辑结果' if mode == 'edit' else '保存编排曲谱')
        self._update_actions()
        if mode == 'edit':
            self.editor.refresh_labels()
            self._set_status('乐谱编辑：拖动音符改音高与位置，选中后可以合并成长音')
        else:
            self._set_status('编排预览')

    def _ensure_editor_doc(self) -> bool:
        """保证编辑文档存在。

        已经建立过就保留 —— 用户切到预览看一眼再切回来，编辑不能丢。
        只有载入新曲目时才重建（见 _discard_editor_doc）。
        """
        if self.editor_doc is not None and len(self.editor_doc):
            return True
        if self.plan is None or not len(self.plan.steps):
            return False
        ctx = None
        if self.score is not None:
            try:
                ctx = self.score.notation()
            except Exception:
                ctx = None
        self.editor_doc = EditDoc.from_steps(
            self.plan.steps, title=self.plan.title, ctx=ctx,
            source=self.score_path)
        self.editor.set_doc(self.editor_doc)
        return True

    def _discard_editor_doc(self) -> None:
        self._edit_debounce.stop()
        self.editor_doc = None
        self.editor.set_doc(None)
        if self.mode != 'preview':
            self.mode = 'preview'
            self.stack.setCurrentIndex(0)
            self.btn_mode_preview.setChecked(True)
            self.btn_mode_edit.setChecked(False)
            self.tip_label.setText(PREVIEW_TIP)

    def _on_editor_changed(self) -> None:
        # 拖动一次会发很多次 changed，防抖一下，别每帧都重算指法
        self._edit_debounce.start()

    def apply_editor(self) -> None:
        """把编辑结果变成可演奏的编排。

        这里必须走 arrange_from_notes 而不是 arrange：编辑器里的音高已经是
        最终要弹的音高，再经过一次移调与八度折叠就是错音。
        """
        doc = self.editor_doc
        if doc is None or not len(doc):
            return
        self.params.apply_to(self.options)
        try:
            plan = arrange_from_notes(
                doc.notes, self.instrument, self.options, title=doc.title,
                stats=self.plan.stats if self.plan else None)
        except (ValueError, RuntimeError) as exc:
            self._set_status('编辑结果无法演奏：%s' % exc)
            return

        self.plan = plan
        self.roll.set_plan(plan)
        if self.score is not None:
            self._refresh_info(self.score)
        self._update_progress(0.0, plan.duration)
        self._update_actions()

        dropped = plan.stats.get('dropped', 0)
        if dropped:
            self._set_status('已应用编辑：%d 个音符超出乐器音域，演奏时会跳过'
                             % dropped)
        else:
            self._set_status('已应用编辑：%d 个音符' % len(plan.steps))

    def _build_controls(self, parent_layout) -> None:
        row = QHBoxLayout()
        row.setSpacing(10)

        self.btn_preview = QPushButton('本机试听')
        self.btn_preview.setMinimumWidth(132)
        self.btn_preview.setToolTip(
            '用本机合成器播放编排结果，不启动游戏。\n再点一次暂停，拖动时间轴可跳转。')
        self.btn_preview.clicked.connect(self.toggle_preview)
        row.addWidget(self.btn_preview)

        self.btn_play = QPushButton('开始演奏   F8')
        self.btn_play.setObjectName('primary')
        self.btn_play.setMinimumHeight(48)
        self.btn_play.setToolTip(
            '倒计时后把按键发送到游戏。\n演奏目标 = 倒计时结束那一刻的前台窗口。')
        self.btn_play.clicked.connect(self.start_play)
        row.addWidget(self.btn_play, 1)

        self.btn_dry = QPushButton('演练')
        self.btn_dry.setToolTip('不向游戏发送按键，只在界面里走一遍时序')
        self.btn_dry.clicked.connect(lambda: self.start_play(dry_run=True))
        row.addWidget(self.btn_dry)

        # 暂停按钮不再单独占位（F8 已覆盖），保留对象以兼容状态刷新逻辑
        self.btn_pause = QPushButton('暂停')
        self.btn_pause.setVisible(False)
        self.btn_pause.clicked.connect(self.toggle_pause)

        self.btn_stop = QPushButton('停止   F9')
        self.btn_stop.setObjectName('stop')
        self.btn_stop.setMinimumHeight(48)
        self.btn_stop.setMinimumWidth(132)
        self.btn_stop.clicked.connect(self.stop_all)
        row.addWidget(self.btn_stop)

        parent_layout.addLayout(row)

    def _load_config_into_ui(self) -> None:
        self.params.load_from(self.options)
        timbre = self.settings.value('timbre', 'default')
        index = self.params.cb_timbre.findData(timbre)
        if index >= 0:
            self.params.cb_timbre.setCurrentIndex(index)

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _update_actions(self) -> None:
        has_plan = self.plan is not None and len(self.plan.steps) > 0
        playing = self.play_worker is not None and self.play_worker.isRunning()
        paused = playing and self.play_worker.is_paused
        previewing = self.audio.playing

        self.btn_mode_edit.setEnabled(has_plan)
        self.btn_play.setEnabled(has_plan and not playing and not previewing)
        self.btn_dry.setEnabled(has_plan and not playing)
        self.btn_stop.setEnabled(playing or previewing)
        self.btn_preview.setEnabled(has_plan and not playing)
        self.btn_play.setText('已暂停   F8' if paused else '开始演奏   F8')
        # 演奏 / 试听中锁定选曲：防误触把正在播放的编排在脚下换掉
        self.library.set_locked(playing or previewing)

    # ------------------------------------------------------------------
    # 曲谱加载与编排
    # ------------------------------------------------------------------

    def _apply_titlebar_theme(self) -> None:
        """Windows 标题栏跟随深浅色。Qt 的 setColorScheme 负责全局配色
        方案；部分 Qt 版本不因此刷新 DWM 标题栏，所以再用窗口属性
        兜底一遍 —— 两者叠加不冲突。"""
        try:
            from PySide6.QtGui import QGuiApplication
            scheme = (Qt.ColorScheme.Light if C.mode == 'light'
                      else Qt.ColorScheme.Dark)
            QGuiApplication.styleHints().setColorScheme(scheme)
        except Exception:
            pass
        try:
            import ctypes
            hwnd = int(self.winId())
            value = ctypes.c_int(1 if C.mode == 'dark' else 0)
            for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(value),
                        ctypes.sizeof(value)) == 0:
                    break
        except Exception:
            pass

    def showEvent(self, event) -> None:
        self._apply_titlebar_theme()
        # 窗口位置/尺寸不能超出屏幕工作区：1600p 屏 + 175% 缩放时
        # 默认 1420x900 加标题栏正好超出下沿，状态栏会被任务栏遮住
        # （表现为左下角状态文字永远缺半截）。
        if not getattr(self, '_geo_clamped', False):
            self._geo_clamped = True
            ag = self.screen().availableGeometry()
            r = self.frameGeometry()
            # 尺寸超出工作区就先缩（客户区等比缩回），再平移夹回界内
            if r.width() > ag.width() or r.height() > ag.height():
                self.resize(self.width() - max(0, r.width() - ag.width()),
                            self.height() - max(0, r.height() - ag.height()))
                r = self.frameGeometry()
            nx = max(ag.left(), min(self.x(), ag.right() - r.width()))
            ny = max(ag.top(), min(self.y(), ag.bottom() - r.height()))
            if (nx, ny) != (self.x(), self.y()):
                self.move(nx, ny)
        super().showEvent(event)
        # 下拉框必须挂控件级样式表，app 级 QSS 压不住它的原生渐变绘制
        # （详见 theme.combo_style）。放在首次显示时做，避免 __init__
        # 里控件还没建全。
        if not getattr(self, '_controls_polished', False):
            self._controls_polished = True
            style = combo_style()
            for combo in self.findChildren(QComboBox):
                combo.setStyleSheet(style)
        # 公告放在主窗口显示之后再弹（showEvent 里直接 exec 会重入事件循环），
        # 每次进程只查一次。
        if not getattr(self, '_announce_checked', False):
            self._announce_checked = True
            QTimer.singleShot(300, self._maybe_show_announcement)

    def save_arrangement(self) -> None:
        """把当前编排结果存成曲谱文件。

        对应原版的「保存编排曲谱」：保存全部音轨的编排和裁切，
        可以另存新曲目或覆盖原曲。存下来的音符已经过移调、八度折叠
        和单音化，重新载入即是可直接演奏的曲谱。

        在乐谱编辑视图下保存的是**编辑后的乐谱本身** —— 它已经是最终
        音高，存下来重新打开就是所见即所得。
        """
        from ..score import Note, Score, save_json_score

        if self.mode == 'edit' and self.editor_doc is not None \
                and len(self.editor_doc):
            out = self.editor_doc.to_score()
            if not len(out.notes):
                self._set_status('编辑结果里没有音符')
                return
            target = self._ask_save_target(
                out, '保存编辑后的乐谱',
                '保存的是你在编辑器里改过的乐谱，小节与拍号一并保存。')
            if not target:
                return
            try:
                save_json_score(out, target)
            except Exception as exc:
                QMessageBox.warning(self, '无法保存乐谱', str(exc))
                return
            self.editor_doc.save(target)
            self.score_path = target
            self.library.refresh()
            self._set_status('已保存编辑结果：%s（%d 个音符）'
                             % (os.path.basename(target), len(out.notes)))
            return

        if self.score is None or self.plan is None or not self.plan.steps:
            self._set_status('当前没有可保存的编排')
            return

        notes = [Note(pitch=s.pitch, start=round(s.start, 4),
                      duration=round(s.duration, 4), velocity=85, track=0)
                 for s in self.plan.steps]
        if not notes:
            self._set_status('当前音轨没有可播放的音符')
            return
        out = Score(title=self.score.title, notes=notes,
                    source=self.score.source,
                    bpm=self.score.bpm, time_sig_num=self.score.time_sig_num,
                    time_sig_den=self.score.time_sig_den,
                    phase=self.score.phase, key=self.score.key,
                    scale=self.score.scale)

        has_source = bool(self.score_path) and os.path.isfile(self.score_path or '')
        target = self._ask_save_target(
            out, '保存编排曲谱',
            '全部音轨会保存当前编排和裁切。' if has_source
            else '该曲目还没有对应的曲库文件，将另存为新曲谱。')
        if not target:
            return

        try:
            save_json_score(out, target)
        except Exception as exc:
            QMessageBox.warning(self, '无法保存编排', str(exc))
            return
        self.library.refresh()
        self._set_status('已保存编排：%s（%d 个音符）'
                         % (os.path.basename(target), len(notes)))

    def _ask_save_target(self, out: Score, title: str,
                         note: str) -> Optional[str]:
        """弹「覆盖原曲 / 另存为新曲谱 / 取消」，返回目标路径。

        编排结果和编辑结果都要走这一步，所以抽出来共用 —— 两处各写一遍
        迟早会改漏一处（比如只给其中一处加上「补 .json 后缀」）。
        """
        has_source = bool(self.score_path) and os.path.isfile(self.score_path or '')

        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText('保存《%s》（%d 个音符）' % (out.title, len(out.notes)))
        box.setInformativeText(note)
        btn_overwrite = None
        if has_source:
            btn_overwrite = box.addButton('覆盖原曲', QMessageBox.DestructiveRole)
        btn_saveas = box.addButton('另存为新曲谱…', QMessageBox.AcceptRole)
        box.addButton('取消', QMessageBox.RejectRole)
        box.exec()

        clicked = box.clickedButton()
        if clicked is None or (clicked is not btn_saveas
                               and clicked is not btn_overwrite):
            return None
        if clicked is btn_overwrite:
            return self.score_path

        base = out.title or 'arrangement'
        safe = ''.join(c for c in base if c not in '\\/:*?"<>|').strip() or 'arrangement'
        start_dir = os.path.join(self.library_dir, safe + '.json')
        target, _ = QFileDialog.getSaveFileName(
            self, '另存为新曲谱', start_dir, '曲谱 (*.json)')
        if not target:
            return None
        if not target.lower().endswith('.json'):
            target += '.json'
        return target

    def open_jianpu_dialog(self) -> None:
        """打开「导入简谱」，完成后刷新曲库并载入结果。"""
        from .jianpu_dialog import JianpuDialog

        busy = (self.play_worker is not None and self.play_worker.isRunning())
        if busy or self.audio.playing:
            self._set_status('请先停止演奏，再导入简谱')
            return
        dialog = JianpuDialog(self.library_dir, self)
        accepted = dialog.exec() == QDialog.Accepted
        if accepted and dialog.saved_path:
            self.library.refresh()
            self.library.select_path(dialog.saved_path)
            self.on_library_selected(dialog.saved_path)
            self._set_status('简谱已加入曲库：%s'
                             % os.path.basename(dialog.saved_path))

    # ------------------------------------------------------------------
    # 拖拽导入
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        """拖入文件：曲谱（MIDI / JSON）入库；音频走内置转谱。"""
        from ..audio2score import AUDIO_EXTS

        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile()]
        if not paths:
            return
        event.acceptProposedAction()

        scores = [p for p in paths
                  if p.lower().endswith(('.mid', '.midi', '.json'))
                  and os.path.isfile(p)]
        audios = [p for p in paths
                  if p.lower().endswith(AUDIO_EXTS) and os.path.isfile(p)]
        skipped = len(paths) - len(scores) - len(audios)

        if scores:
            self.import_paths(scores)
        if audios:
            if len(audios) > 1:
                self._set_status('一次转一个音频：先转 %s'
                                 % os.path.basename(audios[0]))
            self.transcribe_audio_file(audios[0])
        if not scores and not audios:
            self._set_status('拖入的文件不支持（曲谱：MIDI / JSON；'
                             '音频：mp3 / wav / flac / ogg …）')
        elif skipped:
            self._set_status('已忽略 %d 个不支持的文件' % skipped)

    def _import_from_dialog(self) -> None:
        """顶栏「导入 MIDI / 曲谱」：选文件后走同一套导入路由。"""
        patterns = ('曲谱 (*.mid *.midi *.json);;MIDI (*.mid *.midi);;'
                    'JSON (*.json);;所有文件 (*)')
        paths, _ = QFileDialog.getOpenFileNames(self, '导入曲谱',
                                                self.library_dir, patterns)
        if paths:
            self.import_paths(paths)

    # -- 音频转曲谱（内置 GAME ONNX）--

    def _transcribe_from_dialog(self) -> None:
        """顶栏「音频转曲谱」：选一个音频文件。"""
        from ..audio2score import AUDIO_EXTS
        patterns = ('音频 (%s);;所有文件 (*)'
                    % ' '.join('*' + e for e in AUDIO_EXTS))
        path, _ = QFileDialog.getOpenFileName(self, '选择音频文件',
                                              self.library_path(), patterns)
        if path:
            self.transcribe_audio_file(path)

    def library_path(self) -> str:
        """最近一次打开文件对话框的起始目录（曲库目录）。"""
        return getattr(self, 'library_dir', '') or os.path.expanduser('~')

    def _library_target(self, filename: str) -> str:
        """曲库里的目标路径；重名时加 (1)(2)…，不覆盖已有曲目。"""
        stem, ext = os.path.splitext(filename)
        target = os.path.join(self.library_dir, filename)
        n = 1
        while os.path.exists(target):
            target = os.path.join(self.library_dir,
                                  '%s (%d)%s' % (stem, n, ext))
            n += 1
        return target

    def transcribe_audio_file(self, path: str) -> None:
        """音频 → 曲谱：确认 → 后台推理（带进度、可取消）→ 写入曲库。"""
        from ..audio2score import model_available
        from .worker import AudioTranscribeWorker

        if not model_available():
            QMessageBox.warning(
                self, '无法转谱',
                '内置的转谱模型不完整（gtiharmonica/assets/game_model）。\n'
                '请使用完整解压的版本，或重新下载安装包。')
            return

        name = os.path.splitext(os.path.basename(path))[0]
        ret = QMessageBox.question(
            self, '音频转曲谱',
            '要把「%s」转成曲谱吗？\n\n'
            '· 本机 CPU 推理，一首 4 分钟的歌约 30 秒\n'
            '· 跟着人声 / 主旋律唱的歌效果最好；伴奏很满的歌会混进一些\n'
            '  杂音，先用「分离人声」处理一遍再转会干净很多\n\n'
            '结果会作为新曲目加进曲库。' % name,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret != QMessageBox.Yes:
            return

        dlg = QProgressDialog('准备中…', '取消', 0, 100, self)
        dlg.setWindowTitle('音频转曲谱')
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)

        worker = AudioTranscribeWorker(path, title=name, parent=self)
        self._transcribe_worker = worker       # 持有引用，别让线程被回收

        def on_progress(stage: str, done: int, total: int) -> None:
            if stage == '加载音频':
                dlg.setLabelText('正在读取音频…')
                dlg.setValue(3)
            elif stage == '切片':
                dlg.setLabelText('正在切分段落…')
                dlg.setValue(6)
            else:
                dlg.setLabelText('正在识别音符…（第 %d/%d 段）'
                                 % (min(done + 1, total), max(total, 1)))
                dlg.setValue(8 + int(90 * done / max(total, 1)))

        def on_done(score) -> None:
            dlg.close()
            from ..score import save_json_score
            dest = self._library_target(name + '(音频转谱).json')
            try:
                save_json_score(score, dest)
            except OSError as exc:
                QMessageBox.warning(self, '写入曲库失败', str(exc))
                return
            self.library.refresh()
            self.library.select_path(dest)
            self.on_library_selected(dest)
            self._set_status('音频转谱完成：%s（%d 个音符，时长 %s）'
                             % (os.path.basename(dest), len(score.notes),
                                self._fmt_dur(score.duration)))

        def on_failed(msg: str) -> None:
            dlg.close()
            if msg.startswith('已取消'):
                self._set_status('音频转谱已取消')
                return
            QMessageBox.warning(self, '转谱失败', msg)

        worker.progress.connect(on_progress)
        worker.done.connect(on_done)
        worker.failed.connect(on_failed)
        dlg.canceled.connect(worker.cancel)
        worker.start()

    @staticmethod
    def _fmt_dur(seconds: float) -> str:
        total = max(int(round(seconds)), 0)
        return '%d:%02d' % (total // 60, total % 60)

    def import_paths(self, paths: list) -> None:
        """入库统一路由：JSON 直接复制；MIDI 先选「直转 / 旋律化」。

        两条链各有擅长的曲子（不同编曲密度效果差异很大），所以都保留，
        由用户按曲子挑。旋律化产物是规整好的单旋律线 JSON，bpm 一并写入。
        """
        from .dialogs import ImportModeDialog

        mids = [p for p in paths if p.lower().endswith(('.mid', '.midi'))]
        jsons = [p for p in paths if p.lower().endswith('.json')]
        other = len(paths) - len(mids) - len(jsons)

        mode = 'direct'
        if mids:
            dlg = ImportModeDialog(mids, self)
            if not dlg.exec():
                self._set_status('已取消导入')
                return
            mode = dlg.mode

        added = []
        if jsons:
            added += self.library.import_files(jsons)
        if mids:
            if mode == 'direct':
                added += self.library.import_files(mids)
            elif mode == 'track':
                for p in mids:
                    out = self._track_to_library(p)
                    if out:
                        added.append(out)
            else:
                for p in mids:
                    out = self._melodize_to_library(p)
                    if out:
                        added.append(out)

        if not added:
            self._set_status('没有文件入库')
            return
        self.library.refresh()
        self.library.select_path(added[0])
        self.on_library_selected(added[0])
        self._set_status('已入库 %d 首：%s'
                         % (len(added), os.path.basename(added[0])))

    def _track_to_library(self, path: str):
        """单轨提取链路：多轨 MIDI → 主旋律轨 + 单音化 + 网格量化。

        这是「网上找的原琴谱 / 改編 MIDI」最稳的一条路：实测复现人工整理
        好的曲谱时，旋律一致度 0.11 半音/步、时值中位 125ms 对 120ms。
        """
        from ..melody import melody_track_score, pick_melody_track
        from ..score import save_json_score

        try:
            score = load_score(path)
            track = pick_melody_track(score)
            out_score = melody_track_score(score)
        except Exception as exc:
            QMessageBox.warning(self, '单轨提取失败',
                                '%s：%s' % (os.path.basename(path), exc))
            return None
        base = os.path.splitext(os.path.basename(path))[0]
        dest = self._library_target(base + '(单轨).json')
        try:
            save_json_score(out_score, dest)
        except Exception as exc:
            QMessageBox.warning(self, '单轨提取失败',
                                '%s：%s' % (os.path.basename(path), exc))
            self._set_status('已从 %d 条轨里挑出主旋律轨（第 %d 轨）'
                             % (len(score.tracks()), track))
        return dest

    def _melodize_to_library(self, path: str):
        """心似烟火链路：MIDI → 十六分网格旋律线 → 曲库 JSON。"""
        from ..melody import melodize
        from ..score import save_json_score

        try:
            score = load_score(path)
            mel = melodize(score)
        except Exception as exc:
            QMessageBox.warning(self, '旋律化失败',
                                '%s\n%s' % (os.path.basename(path), exc))
            return None
        base = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(self.library_dir, base + '(旋律化).json')
        n = 1
        while os.path.exists(out):
            out = os.path.join(self.library_dir,
                               '%s(旋律化%d).json' % (base, n))
            n += 1
        try:
            save_json_score(mel, out)
        except Exception as exc:
            QMessageBox.warning(self, '旋律化失败', str(exc))
            return None
        return out

    def on_library_selected(self, path: str) -> None:
        if not path or not os.path.exists(path):
            return
        # 兜底：任何路径（含拖入导入后自动选中）在播放中都不换曲
        if ((self.play_worker is not None and self.play_worker.isRunning())
                or self.audio.playing):
            self._set_status('演奏 / 试听进行中，先停止再切换曲目')
            return
        if self.load_worker is not None and self.load_worker.isRunning():
            return
        self.score_path = path
        self._set_status('正在解析 %s …' % os.path.basename(path))

        from .worker import LoadWorker

        # 立刻按当前参数构造一次 Options 快照，避免后台线程读到半改状态
        self.params.apply_to(self.options)
        self.load_worker = LoadWorker(path, self.instrument, self.options, self)
        self.load_worker.ready.connect(self._on_score_loaded)
        self.load_worker.failed.connect(self._on_load_failed)
        self.load_worker.start()

    def _on_score_loaded(self, score: Score, plan: Arrangement) -> None:
        # 换了曲目，编辑文档必须作废 —— 否则编辑的是上一首的谱子
        self._discard_editor_doc()
        self.score = score
        self.plan = plan
        self.roll.set_plan(plan)
        self.params.set_tracks(score, self.instrument, self.options.track)
        self._refresh_info(score)
        self._update_progress(0.0, plan.duration)
        self._update_actions()
        self._set_status('已载入「%s」：%d 个音，%.1f 秒'
                         % (score.title, len(plan.steps), plan.duration))

    def _on_load_failed(self, message: str) -> None:
        self._discard_editor_doc()
        self.score = None
        self.plan = None
        self.roll.set_plan(None)
        self._update_actions()
        QMessageBox.warning(self, '无法读取曲谱', message)
        self._set_status('载入失败')

    def _on_params_changed(self) -> None:
        self._debounce.start()

    def re_arrange(self) -> None:
        if self.score is None:
            return
        if self.mode == 'edit' and self.editor_doc is not None \
                and len(self.editor_doc):
            # 编辑模式下参数改动只重算指法、时间轴与换气，音高以编辑器里的
            # 为准。否则用户改一下「移调」，刚编辑好的谱子就被整体搬走了。
            self.apply_editor()
            return
        self.params.apply_to(self.options)
        try:
            plan = arrange(self.score, self.instrument, self.options)
        except ValueError as exc:
            self._set_status('编排失败：%s' % exc)
            return
        self.plan = plan
        self.roll.set_plan(plan)
        self._refresh_info(self.score)
        self._update_actions()

    def _refresh_info(self, score: Score) -> None:
        if self.plan is None:
            self.title_label.setText('未选择曲目')
            self.info.setText('从左侧选一首曲子，或把 MIDI 文件拖进窗口。')
            return
        m = self.plan.metrics
        stats = self.plan.stats
        self.title_label.setText(score.title)

        parts = [
            '%d 个旋律音符' % len(self.plan.steps),
            '时长 %s' % _fmt_time(self.plan.duration),
        ]
        # 谱面节奏可信度：MIDI 级 = 机器转谱逐音精确；松散 = 听记/重建
        # 节奏，建议试听互证。《戒烟》的教训：错谱也可能网格整齐，这只
        # 提示节奏精度，不背书音高。
        from ..score import grid_quality
        parts.append('谱面 %s' % grid_quality(score.notes))
        if stats.get('reduced'):
            parts.append('单音化 %d' % stats['reduced'])
        if stats.get('folded'):
            parts.append('八度折叠 %d' % stats['folded'])
        if stats.get('dropped'):
            parts.append('超域丢弃 %d' % stats['dropped'])
        parts += [
            '策略 %s' % self.plan.strategy,
            '按键 %d 次' % m.total_presses,
            '修饰键切换 %d 次' % m.modifier_switches,
            '按住式鼠标变调',
        ]
        self.info.setText('  /  '.join(parts))

    # ------------------------------------------------------------------
    # 演奏
    # ------------------------------------------------------------------

    def _target_window(self):
        api = load_user32()
        hwnd, title, pid = foreground_window(api)
        return api, hwnd, title, pid

    def start_play(self, dry_run: bool = False) -> None:
        if self.plan is None or not len(self.plan.steps):
            return
        if self.play_worker is not None and self.play_worker.isRunning():
            return
        self.audio.stop()
        self.audio_timer.stop()

        self._pending_dry_run = dry_run
        self._countdown_left = max(self.countdown_seconds, 0)

        try:
            if not dry_run:
                self._run_preflight()
        except _PreflightAbort:
            return

        if self._countdown_left <= 0:
            self._begin_play()
            return

        self.overlay.show_countdown(self._countdown_left)
        if self.minimize_on_play and not dry_run:
            self.showMinimized()
        self._set_status('请切到游戏的口琴演奏界面……')
        self.btn_play.setEnabled(False)
        self._countdown_timer.start()
        self._countdown_probe.start()

    def _probe_countdown_cancel(self) -> None:
        """倒计时期间按 F9 / Esc 取消（此时焦点通常在游戏里）。

        程序自己在前台时不轮询：那种情况下 QShortcut 已经覆盖了 F9。
        """
        from ..player import HotkeyWatcher, VK_ESCAPE, VK_F9
        if self._hotkey_probe is None:
            self._hotkey_probe = HotkeyWatcher()
        if self._hotkey_probe.self_foreground():
            return
        watcher = self._hotkey_probe
        if watcher.down(VK_F9) or watcher.down(VK_ESCAPE):
            watcher.wait_release(VK_F9)
            watcher.wait_release(VK_ESCAPE)
            self.stop_all()
            self._set_status('已取消演奏（倒计时里按了 F9 / Esc）')

    def _run_preflight(self) -> None:
        """播放前环境自检：输入法状态 + 本进程提权（阻止级问题弹窗）。"""
        if not self.settings.value('preflight_check', True, bool):
            return
        try:
            from ..preflight import run_preflight
            report = run_preflight(need_admin=False)
            self._set_status('自检：' + report.summary)
        except Exception:
            return
        if not report.ime.passed:
            ret = QMessageBox.warning(
                self, '输入法提醒',
                '当前输入法处于中文模式，按键会被输入法截走，\n'
                '游戏可能收不到任何音。\n\n'
                '建议按 Shift 切到英文模式后再开始。仍要继续吗？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ret != QMessageBox.Yes:
                self._set_status('已取消：请先切换输入法')
                raise _PreflightAbort()

    def _restart_elevated(self) -> bool:
        """以管理员身份重启自身（带上当前曲目），成功发起返回 True。

        权限策略是「平时不提权，演奏时再提权」：不提权才能接收资源管理器
        拖入的文件（UIPI 会拒绝中完整性的 Explorer 拖进高完整性窗口）。
        检测到游戏提权而自己没提权时，用这里重启并带上当前曲目。
        开发态（非打包）不做重启 —— 直接提示用管理员终端启动。
        """
        import sys as _sys
        if not getattr(_sys, 'frozen', False):
            return False
        try:
            import ctypes
            args = []
            if self.score_path and os.path.isfile(self.score_path):
                args.append(self.score_path)
            params = ' '.join('"%s"' % a for a in args)
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, 'runas', _sys.executable, params, self.library_dir, 1)
            return rc > 32              # <=32 表示失败（多半是用户点了「否」）
        except Exception:
            return False

    def _check_target_elevation(self, hwnd: int, title: str) -> bool:
        """目标游戏以管理员运行而本程序没有提权时，SendInput 会全被拒绝。"""
        try:
            from ..preflight import is_admin, is_target_elevated
            if is_admin() or not is_target_elevated(hwnd):
                return True
        except Exception:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle('需要管理员权限')
        box.setText('「%s」以管理员身份运行，而本程序没有提权，\n'
                    '按键会被系统静默拒绝（表现为一个音都弹不出）。' % title)
        box.setInformativeText('可以重启为新实例（管理员）并带上当前曲目 —— '
                               '之后点「开始演奏」即可；\n'
                               '导入/编辑/试听等操作保持免提权，拖入文件也不受影响。')
        btn_restart = box.addButton('以管理员重启', QMessageBox.AcceptRole)
        box.addButton('取消', QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is btn_restart:
            self._save_config()
            if self._restart_elevated():
                QApplication.instance().quit()
                return False
            self._set_status('开发模式下请用管理员权限的终端重新启动')
        self._set_status('已取消：需要以管理员身份运行本程序')
        return False

    def _countdown_step(self) -> None:
        self._countdown_left -= 1
        if self._countdown_left > 0:
            self.overlay.show_countdown(self._countdown_left)
            self._set_status('%d 秒后开始演奏，请切到游戏……' % self._countdown_left)
            return
        self._countdown_timer.stop()
        self._countdown_probe.stop()
        self.overlay.show_countdown(0)
        self._begin_play()

    def _begin_play(self) -> None:
        dry_run = getattr(self, '_pending_dry_run', False)

        if dry_run:
            self.showNormal()
            self.raise_()
            self.activateWindow()
            backend = DryRunBackend(verbose=False)
            plan = self.plan
            self.play_worker = None
            self.overlay.stop_hidden()      # 倒计时结束，收起悬浮窗
            self._run_dry_run(plan)
            return

        api, hwnd, title, pid = self._target_window()
        own = int(self.winId())
        if not self._check_target_elevation(hwnd, title or '游戏'):
            # 这里必须把悬浮窗收起来：不然它会一直停在倒计时数字上
            self.overlay.stop_hidden()
            self._update_actions()
            return
        if hwnd == own or not title:
            self.overlay.stop_hidden()
            self.showNormal()
            self.raise_()
            self.activateWindow()
            QMessageBox.information(
                self, '需要先切到游戏',
                '倒计时结束时前台窗口是本程序自己，无法确定演奏目标。\n\n'
                '请先切换到《三角洲行动》的口琴演奏界面，再点「演奏」。')
            self._update_actions()
            self._set_status('已取消：目标窗口无效')
            return

        from .worker import PlayWorker

        self.params.apply_to(self.options)
        try:
            plan = arrange(self.score, self.instrument, self.options)
        except ValueError as exc:
            self.overlay.stop_hidden()
            QMessageBox.warning(self, '编排失败', str(exc))
            self._update_actions()
            return
        self.plan = plan
        self.roll.set_plan(plan)

        self.play_worker = PlayWorker(plan, hwnd, self.scheduler,
                                      keep_focus=self.keep_focus,
                                      loops=(0 if self.loop_play else 1),
                                      parent=self)
        self.play_worker.tick.connect(self._on_tick)
        self.play_worker.stateChanged.connect(self._on_play_state)
        self.play_worker.done.connect(self._on_play_done)
        self.play_worker.failed.connect(self._on_play_failed)
        self.play_worker.start()

        self.overlay.begin(plan, plan.duration)
        self._set_status('正在向「%s」演奏（F8 暂停 / F9 停止）' % title)
        self._update_actions()

    def _run_dry_run(self, plan: Arrangement) -> None:
        """演练：逐条走一遍时序但不发送按键，用于确认节奏和目标窗口。"""
        import threading

        self.btn_play.setEnabled(False)
        self.btn_dry.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._dry_stop = threading.Event()
        self._set_status('演练中（不发送按键）…')

        def worker():
            started = time.perf_counter()
            steps = plan.steps
            for index, step in enumerate(steps):
                if self._dry_stop.is_set():
                    break
                due = started + step.start
                while True:
                    delta = due - time.perf_counter()
                    if delta <= 0 or self._dry_stop.is_set():
                        break
                    time.sleep(min(delta, 0.02))
                if self._dry_stop.is_set():
                    break
                self.roll.set_position(step.start, index)
                self.keys.set_active(step.key, step.modifiers)
                self._update_progress(step.start, plan.duration)
                time.sleep(step.duration)
            self.keys.set_active(None, ())
            self.roll.set_position(0.0, -1)
            self._update_progress(0.0, plan.duration)
            QTimer.singleShot(0, self._dry_finished)

        self._dry_thread = threading.Thread(target=worker, daemon=True)
        self._dry_thread.start()

    def _dry_finished(self) -> None:
        self._set_status('演练结束')
        self._update_actions()

    def _on_f8(self) -> None:
        """F8：演奏中=暂停/继续；试听中=暂停试听；空闲=开始演奏。"""
        if self.play_worker is not None and self.play_worker.isRunning():
            self.toggle_pause()
            return
        if self.audio.playing:
            self.toggle_preview()
            return
        if self.plan is None or not len(self.plan.steps):
            self._set_status('先在左侧选一首曲子，再按 F8 开始演奏')
            return
        self.start_play()

    def toggle_pause(self) -> None:
        if self.play_worker is None or not self.play_worker.isRunning():
            return
        if self.play_worker.is_paused:
            self.play_worker.resume()
        else:
            self.play_worker.pause()
        self._update_actions()

    def stop_all(self) -> None:
        self._countdown_timer.stop()
        self._countdown_probe.stop()
        if getattr(self, '_dry_stop', None) is not None:
            self._dry_stop.set()
        if self.play_worker is not None and self.play_worker.isRunning():
            self.play_worker.stop()
        self.audio.stop()
        self.audio_timer.stop()
        self.keys.set_active(None, ())
        self.roll.set_position(0.0, -1)
        self.editor.set_position(0.0)
        self.btn_preview.setText(' 试听')
        self._update_progress(0.0, self.plan.duration if self.plan else 0.0)
        self._update_actions()

    # -- 演奏回调 --

    def _on_tick(self, index: int, position: float, step) -> None:
        self.keys.set_active(step.key, step.modifiers)
        self.mods.set_active(step.modifiers)
        self.roll.set_position(position, index - 1)
        # 编辑视图也用同一个播放头，切过去能看到演奏走到哪了
        self.editor.set_position(position)
        self._update_progress(position, self.plan.duration if self.plan else 0.0)

    def _on_play_state(self, state: str) -> None:
        if state == 'paused':
            if self._self_is_foreground():
                # 切回本程序会自动暂停（不暂停的话下一次按键就会误发到
                # 本程序里）。把原因写清楚，用户回游戏按 F8 就能接着弹。
                self.overlay.set_state('已暂停（你切回了本程序）· F8 继续',
                                       paused=True)
                self._set_status('演奏已暂停：你切回了本程序。回到游戏后按 F8 继续')
            else:
                # 前台既不是本程序也不是游戏（切去看攻略/聊天了）：
                # 同样是暂停，别让用户以为演奏丢了
                self.overlay.set_state('已暂停（不在游戏窗口）· F8 继续',
                                       paused=True)
                self._set_status('演奏已暂停：当前前台不是游戏窗口。'
                                 '回到游戏后按 F8 继续')
        elif state == 'playing':
            self.overlay.set_state('')
        self._update_actions()

    def _self_is_foreground(self) -> bool:
        """本程序自己是不是前台窗口（判断「暂停是不是切回来造成的」）。"""
        try:
            from ..backend import foreground_is_self, load_user32
            if getattr(self, '_fg_api', None) is None:
                self._fg_api = load_user32()
            return foreground_is_self(self._fg_api)
        except Exception:
            return False

    def _foreground_is_game(self) -> bool:
        """前台窗口看起来是不是游戏。

        两道判据取其一：标题含 delta / 三角洲，或者窗口占满整块屏幕
        （独占全屏和无边框窗口都满足）。窗口化的小游戏窗口不在此列，
        这正是我们想要的 —— 别在浏览器里按 F8 就开弹。
        """
        try:
            import ctypes
            from ..backend import foreground_window, load_user32
            api = load_user32()
            hwnd, title, _pid = foreground_window(api)
            if not hwnd:
                return False
            low = (title or '').lower()
            if 'delta' in low or '三角洲' in (title or ''):
                return True

            class _RECT(ctypes.Structure):
                _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                            ('right', ctypes.c_long), ('bottom', ctypes.c_long)]

            rect = _RECT()
            if not api.GetWindowRect(hwnd, ctypes.byref(rect)):
                return False
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            return (abs(w - api.GetSystemMetrics(0)) <= 8
                    and abs(h - api.GetSystemMetrics(1)) <= 8)
        except Exception:
            return False

    def _standby_check(self) -> None:
        """待命热键：游戏在前台时按 F8 直接开始演奏。

        演奏中 F8 由 player 自己的轮询处理（暂停/继续）；这里只管
        「还没开始弹」的那段 —— 用户在游戏里按一下就能开始，不用切回
        本程序点按钮。设置里可以关掉。
        """
        if not self.f8_standby:
            return
        if self.play_worker is not None and self.play_worker.isRunning():
            return
        if self._countdown_timer.isActive():
            return
        if self.score is None or self.plan is None or not len(self.plan.steps):
            return
        if self._self_is_foreground():
            return              # 本程序在前台：F8 交给界面快捷键
        try:
            from ..player import VK_F8, HotkeyWatcher
            if self._standby_watcher is None:
                self._standby_watcher = HotkeyWatcher()
            watcher = self._standby_watcher
            if not watcher.down(VK_F8):
                return
            watcher.wait_release(VK_F8)
        except Exception:
            return
        if not self._foreground_is_game():
            return              # 在别的程序里：别误触（F8 在编辑器里很常用）
        self._set_status('检测到 F8：直接在游戏里开始演奏')
        self.start_play()

    def _on_play_done(self, state: str, timing: dict, stats: dict) -> None:
        self.keys.set_active(None, ())
        self.play_worker = None
        self.overlay.set_state({'finished': '演奏完成', 'stopped': '已停止',
                                'error': '演奏中断'}.get(state, state))
        self.overlay.finish()
        self._update_actions()

        msgs = {'finished': '演奏完成', 'stopped': '已停止',
                'error': '演奏中断'}
        text = msgs.get(state, state)
        detail = []
        if timing.get('samples'):
            detail.append('时序 平均 %+.2f ms / p95 %+.2f ms'
                          % (timing['mean_ms'], timing['p95_ms']))
        if stats.get('send_calls'):
            detail.append('注入 %d 次，平均 %.0f µs'
                          % (stats['send_calls'], stats['avg_us']))
            if stats.get('errors'):
                detail.append('失败 %d 次' % stats['errors'])
        self._set_status(text + ('　|　' + '　'.join(detail) if detail else ''))

    def _on_play_failed(self, message: str) -> None:
        self.play_worker = None
        self.keys.set_active(None, ())
        self.overlay.set_state('演奏中断')
        self.overlay.finish()
        self._update_actions()
        first = message.splitlines()[0] if message else '未知错误'
        self._set_status('演奏中断：%s' % first)
        QMessageBox.warning(self, '演奏中断', message)

    # ------------------------------------------------------------------
    # 试听
    # ------------------------------------------------------------------

    def _current_timbre(self):
        key = self.params.cb_timbre.currentData()
        return {'soft': synth.Timbre.soft(),
                'bright': synth.Timbre.bright()}.get(key, synth.Timbre())

    def _on_timbre_changed(self, key: str) -> None:
        self.settings.setValue('timbre', key)
        if self.audio.playing:
            self.toggle_preview()      # 重新渲染

    def toggle_preview(self) -> None:
        if self.audio.playing:
            self.audio.pause()
            self.audio_timer.stop()
            self.btn_preview.setText(' 试听')
            self._update_actions()
            return

        if self.plan is None or not len(self.plan.steps):
            return

        if self.plan is not None and self.audio._wav is not None and \
                getattr(self, '_preview_plan_id', None) == id(self.plan):
            # 同一份编排，直接从上次位置续播
            self.audio.play(self.audio._start_pos)
            self.audio_timer.start()
            self.btn_preview.setText(' 停止试听')
            self._update_actions()
            return

        if not synth.Player.is_available():
            QMessageBox.information(self, '无法试听',
                                    '当前系统没有可用的 winsound，试听功能不可用。\n'
                                    '你仍然可以用「演练」查看按键序列。')
            return

        self.btn_preview.setEnabled(False)
        self._set_status('正在渲染试听音频…')
        from .worker import SynthWorker
        self.synth_worker = SynthWorker(self.plan, self._current_timbre(),
                                        parent=self)
        self.synth_worker.ready.connect(self._on_synth_ready)
        self.synth_worker.failed.connect(self._on_synth_failed)
        self.synth_worker.start()

    def _on_synth_ready(self, wav: bytes, duration: float) -> None:
        self.audio.load(wav)
        self._preview_plan_id = id(self.plan) if self.plan else None
        ok = self.audio.play(0.0)
        if ok:
            self.audio_timer.start()
            self.btn_preview.setText(' 停止试听')
            self._set_status('试听中（%d KB，%.1f 秒）—— 再点一次可暂停'
                             % (len(wav) // 1024, duration))
        else:
            self._set_status('试听播放失败')
        self._update_actions()

    def _on_synth_failed(self, message: str) -> None:
        self._update_actions()
        QMessageBox.warning(self, '渲染失败', message)
        self._set_status('试听渲染失败')

    def _poll_audio(self) -> None:
        if not self.audio.playing:
            self.audio_timer.stop()
            self.btn_preview.setText(' 试听')
            self._update_actions()
            return
        pos = self.audio.position()
        self.roll.set_position(pos, self._index_at(pos))
        # 编辑视图的播放头也要喂：否则切到「乐谱编辑」试听时，
        # 卷帘纹丝不动，用户会以为播放死了（两个视图必须同步走）。
        self.editor.set_position(pos)
        self._update_progress(pos, self.audio.duration)
        if self.audio.finished:
            self.audio.stop()
            self.audio_timer.stop()
            self.btn_preview.setText(' 试听')
            self.roll.set_position(0.0, -1)
            self.editor.set_position(0.0)
            self._update_progress(0.0, self.audio.duration)
            self._set_status('试听结束')
            self._update_actions()

    def _index_at(self, position: float) -> int:
        """当前时间落在哪个音符上（-1 表示没有）。

        这里刻意用线性扫描：实测 798 个音符下只要 0.086ms/次，
        折合 20Hz 轮询约 0.17% CPU，不构成瓶颈。而且 plan.steps
        不保证按 start 有序，改用二分反而会算错。
        """
        if self.plan is None:
            return -1
        for i, step in enumerate(self.plan.steps):
            if step.start <= position <= step.end:
                return i
        return -1

    def _seek_from_roll(self, seconds: float) -> None:
        """跳转到指定时间。预览卷帘点击、编辑器时间轴点击、进度条共用。

        没有渲染过试听音频时也允许跳 —— 那是纯视觉的播放头移动，
        「看看第 30 秒有什么音」不需要先点一次试听。
        """
        if self.plan is None:
            return
        if self.play_worker is not None and self.play_worker.isRunning():
            self._set_status('演奏中不能跳转，请先按 F8 暂停')
            return
        seconds = max(0.0, min(seconds, self.plan.duration))
        # 试听音频只在它对应的就是当前编排时才跟着跳，
        # 否则旧音频的时长和现在的编排对不上，跳过去是错位。
        if self.audio._wav is not None \
                and getattr(self, '_preview_plan_id', None) == id(self.plan):
            self.audio.seek(seconds)
            if self.audio.playing:
                self.audio.play(seconds)
        self.roll.set_position(seconds, self._index_at(seconds))
        self.editor.set_position(seconds)
        self._update_progress(seconds, self.plan.duration)

    def _update_progress(self, position: float, duration: float) -> None:
        self.overlay.set_position(position)
        frac = min(position / duration, 1.0) if duration > 0 else 0.0
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(int(frac * 1000))
        self.slider.blockSignals(blocked)
        self.time_left.setText('%02d:%06.3f' % (int(position) // 60,
                                                position % 60))
        self.time_right.setText('%02d:%02d' % (int(duration) // 60,
                                               int(duration) % 60))

    def _seek_from_slider(self, raw: int) -> None:
        """用户拖动时间轴：和卷帘点击走同一个跳转逻辑。"""
        if self.plan is None:
            return
        self._seek_from_roll(self.plan.duration * (raw / 1000.0))

    def show_keymap(self) -> None:
        """显示键位 / 音高 / 快捷键速查。"""
        from .dialogs import KeymapDialog
        KeymapDialog(self.instrument, self.countdown_seconds,
                     self.keep_focus, self).exec()

    # ------------------------------------------------------------------
    # 对话框
    # ------------------------------------------------------------------

    def show_analysis(self) -> None:
        if self.score is None:
            QMessageBox.information(
                self, '策略分析',
                '先在左侧选一首曲子，再打开策略分析。\n\n'
                '策略分析会把各种演奏策略（指法编排算法）逐个跑一遍，'
                '对比按键次数与修饰键切换成本，选一个直接套用 —— '
                '用来在「省键」和「顺手」之间做取舍。')
            return
        from .dialogs import AnalysisDialog
        self.params.apply_to(self.options)
        dlg = AnalysisDialog(self.score, self.instrument, self.options,
                             self.cost, self)
        dlg.exec()
        if dlg.chosen_strategy:
            index = self.params.cb_strategy.findData(dlg.chosen_strategy)
            if index >= 0:
                self.params.cb_strategy.setCurrentIndex(index)
                self._set_status('已切换到策略 %s' % dlg.chosen_strategy)

    def show_calibrate(self) -> None:
        from .dialogs import CalibrateDialog
        CalibrateDialog(self.instrument, self).exec()

    def toggle_theme(self) -> None:
        """一键深浅互换（顶栏月亮/太阳按钮，设置里可选跟随系统）。"""
        self.theme_mode = 'light' if C.mode == 'dark' else 'dark'
        self.settings.setValue('theme_mode', self.theme_mode)
        self._apply_theme_now()

    def show_settings(self) -> None:
        from .dialogs import SettingsDialog
        dlg = SettingsDialog(self.countdown_seconds, self.keep_focus,
                             self.minimize_on_play, self.scheduler, self)
        if dlg.exec():
            self.countdown_seconds, self.keep_focus, self.minimize_on_play = \
                dlg.values()
            self.settings.setValue('countdown', self.countdown_seconds)
            self.settings.setValue('keep_focus', self.keep_focus)
            self.settings.setValue('minimize_on_play', self.minimize_on_play)
            self.scheduler = dlg.scheduler()
            self._save_config()

    def _apply_theme_now(self) -> None:
        """即时切换深浅色：重设全局 QSS/调色板并刷新自绘控件。"""
        app = QApplication.instance()
        effective = apply_theme(app, self.theme_mode)
        # 下拉框挂着控件级样式表（压住原生渐变绘制的那份），
        # 必须按新主题重挂，否则它们留在旧主题的配色里
        style = combo_style()
        for combo in self.findChildren(QComboBox):
            combo.setStyleSheet(style)
        self._retheme_children(self)
        self._apply_titlebar_theme()
        self._refresh_toolbar_icons()
        self.editor.toolbar.refresh_icons()
        self._set_status('外观已切换：%s' % MODE_NAMES[effective])

    def _retheme_children(self, widget) -> None:
        """让所有控件真正重新应用样式。

        Qt 的坑：app 级 setStyleSheet 换掉后，多数控件会自动重新
        polish，但部分容器/原生子控件只重绘不重算样式 —— 表现为
        背景留在旧主题。unpolish/polish 强制整套样式重新走一遍。
        """
        app = QApplication.instance()
        st = app.style()
        for child in widget.findChildren(QWidget):
            st.unpolish(child)
            st.polish(child)
            child.update()
        widget.update()

    def show_help(self) -> None:
        from .dialogs import HelpDialog
        HelpDialog(self).exec()

    def show_add_guide(self) -> None:
        """常驻入口：公告同款内容，随时可查。"""
        from .dialogs import AnnouncementDialog
        AnnouncementDialog(self).exec()

    def _maybe_show_announcement(self) -> None:
        """首次运行 / 版本更新后显示一次公告（添加曲谱的方式）。

        已读标记存 QSettings 的 announce_seen —— 存的是公告标识字符串，
        将来发新公告只要改 dialogs.ANNOUNCE_ID，老用户会再看一次。
        自动化环境（无人点击）用 GTIHARMONICA_NO_ANNOUNCE=1 跳过。
        """
        from .dialogs import ANNOUNCE_ID, AnnouncementDialog

        if os.environ.get('GTIHARMONICA_NO_ANNOUNCE'):
            return
        if self.settings.value('announce_seen', '') == ANNOUNCE_ID:
            return
        AnnouncementDialog(self).exec()
        self.settings.setValue('announce_seen', ANNOUNCE_ID)

    def show_about(self) -> None:
        from .dialogs import AboutDialog
        AboutDialog(self).exec()

    # ------------------------------------------------------------------
    # 配置与退出
    # ------------------------------------------------------------------

    def _save_config(self) -> None:
        try:
            self.params.apply_to(self.options)
            self.config.instrument = self.instrument.export_config()
            from dataclasses import asdict
            data = asdict(self.options)
            data.pop('cost', None)
            self.config.options = data
            self.config.scheduler = asdict(self.scheduler)
            self.config.save(self.config_path)
        except Exception:
            pass

    def set_config_path(self, path: str) -> None:
        self.config_path = path

    def closeEvent(self, event: QCloseEvent) -> None:
        self.overlay.stop_hidden()
        if self.play_worker is not None and self.play_worker.isRunning():
            ret = QMessageBox.question(
                self, '正在演奏',
                '演奏尚未结束，确定要退出吗？\n（退出会自动释放所有按键）',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            self.play_worker.stop()
            self.play_worker.wait(1500)

        self.audio.stop()
        self._save_config()
        event.accept()


def run_app(library_dir: str, config_path: str) -> int:
    """GUI 入口。"""
    from ..config import Config

    app = QApplication.instance() or QApplication([])
    app.setApplicationName('大肥鲸洲琴工具包')
    app.setApplicationVersion(__version__)

    config = Config.load_or_default(config_path if os.path.exists(config_path) else None)
    window = MainWindow(config, library_dir)
    window.set_config_path(config_path)
    window.show()
    return app.exec()
