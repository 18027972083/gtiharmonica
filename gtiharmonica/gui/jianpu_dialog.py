"""「导入简谱」对话框：键位简谱文本 -> 曲谱，可解析预览、试听、加入曲库。

转换逻辑在 gtiharmonica.jianpu（与命令行工具 tools/jianpu2score.py 共用），
这里只做界面：粘贴文本 -> 实时解析预览 -> 试听 -> 入库。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QSpinBox, QVBoxLayout,
)


class JianpuDialog(QDialog):
    """把键位简谱文本转成可自动演奏的曲谱。"""

    def __init__(self, library_dir: str, parent=None):
        super().__init__(parent)
        from ..jianpu import SYNTAX_HELP

        self.library_dir = library_dir
        self.saved_path = ''          # 存进曲库后的路径
        self.score = None             # 解析成功的 Score
        self._sec = 0.6               # 一拍多少秒
        self._gap = 0.5               # 乐句间气口
        self.synth_worker = None
        self._play_wav = ''
        self._playing = False
        self._syntax = SYNTAX_HELP

        self.setWindowTitle('导入简谱 —— 键位谱直接转曲谱')
        self.resize(760, 720)
        self._build()

        # 文本变化后 300ms 防抖解析：输入即预览，不用点按钮
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._refresh)
        self.edit_text.textChanged.connect(self._debounce.start)
        self.spin_sec.valueChanged.connect(self._refresh)
        self._refresh()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)

        eyebrow = QLabel('DELTA HARP STUDIO / 简谱导入')
        eyebrow.setObjectName('eyebrow')
        root.addWidget(eyebrow)
        title = QLabel('导入简谱')
        title.setObjectName('title')
        root.addWidget(title)
        desc = QLabel('把键位简谱文本转成游戏里能直接弹的曲谱。数字就是琴键，'
                      '不懂乐理也能用 —— 音高写在谱里，节奏用记号标出来。'
                      '也支持直接粘贴 B站「原琴谱」（QWERTYU/ASDFGHJ/ZXCVBNM '
                      '字母谱或 Sigma-呱呱谱），自动按谱面 BPM 换算时值。')
        desc.setObjectName('muted')
        desc.setWordWrap(True)
        root.addWidget(desc)

        # -- ① 简谱文本 --
        card1 = QFrame()
        card1.setObjectName('card')
        l1 = QVBoxLayout(card1)
        l1.setContentsMargins(16, 14, 16, 14)
        l1.setSpacing(8)
        s1 = QLabel('① 简谱文本（每行一个乐句，行尾可以跟歌词当注释）')
        s1.setObjectName('section')
        l1.addWidget(s1)

        self.edit_text = QPlainTextEdit()
        font = QFont('Consolas')
        font.setStyleHint(QFont.Monospace)
        self.edit_text.setFont(font)
        self.edit_text.setPlaceholderText(
            '.1 6_ 7_ .1 | .3 - 0      （窗外的天气）\n'
            '.5_ .5_ .3 5_ .3_ .2_ .1_ .2_ 7 - 0    （就像是你告别的表情）\n'
            '# ↑ 点在数字前=高音(右键)；下划线=半拍；- 追加一拍；0 休止')
        self.edit_text.setMinimumHeight(220)
        l1.addWidget(self.edit_text)

        help_btn = QPushButton('记法速查')
        help_btn.setObjectName('ghost')
        help_btn.setCheckable(True)
        help_btn.toggled.connect(self._toggle_help)
        self.lbl_help = QLabel(self._syntax)
        self.lbl_help.setObjectName('dim')
        self.lbl_help.setWordWrap(True)
        self.lbl_help.setStyleSheet('padding: 6px 10px;')
        self.lbl_help.hide()
        l1.addWidget(help_btn)
        l1.addWidget(self.lbl_help)
        root.addWidget(card1, 1)

        # -- ② 参数 --
        card2 = QFrame()
        card2.setObjectName('card')
        l2 = QVBoxLayout(card2)
        l2.setContentsMargins(16, 14, 16, 14)
        l2.setSpacing(9)
        s2 = QLabel('② 节拍')
        s2.setObjectName('section')
        l2.addWidget(s2)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self._small('歌名'))
        self.edit_title = QLineEdit()
        self.edit_title.setPlaceholderText('默认取第一个 # 注释行')
        row.addWidget(self.edit_title, 2)
        row.addWidget(self._small('一拍时长'))
        self.spin_sec = QSpinBox()
        self.spin_sec.setRange(150, 2000)
        self.spin_sec.setSingleStep(50)
        self.spin_sec.setValue(600)
        self.spin_sec.setSuffix(' ms')
        self.spin_sec.setToolTip('600ms ≈ 100 BPM。觉得弹得快就调大。')
        row.addWidget(self.spin_sec)
        row.addStretch(1)
        l2.addLayout(row)
        root.addWidget(card2)

        # -- 预览 --
        self.lbl_preview = QLabel('粘贴简谱后自动解析')
        self.lbl_preview.setObjectName('status')
        self.lbl_preview.setWordWrap(True)
        root.addWidget(self.lbl_preview)

        # -- 底部 --
        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.btn_audition = QPushButton('试听')
        self.btn_audition.setObjectName('ghost')
        self.btn_audition.setMinimumHeight(36)
        self.btn_audition.setEnabled(False)
        self.btn_audition.clicked.connect(self._audition)
        self.btn_save = QPushButton('加入曲库')
        self.btn_save.setObjectName('primary')
        self.btn_save.setMinimumHeight(36)
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._save)
        self.btn_close = QPushButton('关闭')
        self.btn_close.setMinimumHeight(36)
        self.btn_close.clicked.connect(self.reject)
        foot.addWidget(self.btn_audition)
        foot.addStretch(1)
        foot.addWidget(self.btn_save)
        foot.addWidget(self.btn_close)
        root.addLayout(foot)

    def _small(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName('dim')
        return lab

    def _toggle_help(self, on: bool) -> None:
        self.lbl_help.setVisible(on)

    # ------------------------------------------------------------------
    # 解析预览
    # ------------------------------------------------------------------

    def _title_guess(self, text: str) -> str:
        for line in text.splitlines():
            s = line.strip()
            if s.startswith('#') and len(s) > 1:
                guess = s.lstrip('#').strip()
                if guess:
                    return guess
        return ''

    def _refresh(self) -> None:
        """重新解析文本框内容并更新预览 / 按钮状态。"""
        from ..jianpu import build_score, describe, parse_text
        from ..yuanqin import looks_like_yuanqin, parse_yuanqin, describe_yuanqin

        text = self.edit_text.toPlainText()
        if not text.strip():
            self.score = None
            self.lbl_preview.setText('粘贴简谱后自动解析')
            self.btn_save.setEnabled(False)
            self.btn_audition.setEnabled(False)
            return

        # 原琴谱（sigma player 键盘谱 / 呱呱谱）走专用解析器，
        # 自带 BPM，时值不跟随「每拍毫秒」设置
        if looks_like_yuanqin(text):
            try:
                self.score = parse_yuanqin(text, self.edit_title.text().strip()
                                           or self._title_guess(text) or '原琴曲')
            except ValueError as exc:
                self.score = None
                self.lbl_preview.setText('⚠ %s' % exc)
                self.btn_save.setEnabled(False)
                self.btn_audition.setEnabled(False)
                return
            if not self.edit_title.text().strip() and self._title_guess(text):
                self.edit_title.setText(self._title_guess(text))
            self.lbl_preview.setText('✓ 原琴谱 %s'
                                     % describe_yuanqin(text, self.score))
            self.btn_save.setEnabled(True)
            self.btn_audition.setEnabled(True)
            return

        rows, errors = parse_text(text)
        if errors:
            self.score = None
            shown = '\n'.join(errors[:4]) + (
                '\n… 共 %d 处' % len(errors) if len(errors) > 4 else '')
            self.lbl_preview.setText('⚠ 记法有 %d 处问题：\n%s'
                                     % (len(errors), shown))
            self.btn_save.setEnabled(False)
            self.btn_audition.setEnabled(False)
            return
        sec = self.spin_sec.value() / 1000.0
        try:
            self.score = build_score(text, self.edit_title.text().strip()
                                     or self._title_guess(text) or '简谱曲',
                                     sec=sec, gap=self._gap)
        except ValueError as exc:
            self.score = None
            self.lbl_preview.setText('⚠ %s' % exc)
            self.btn_save.setEnabled(False)
            self.btn_audition.setEnabled(False)
            return
        if not self.edit_title.text().strip() and self._title_guess(text):
            self.edit_title.setText(self._title_guess(text))
        self.lbl_preview.setText('✓ %s' % describe(self.score, sec))
        self.btn_save.setEnabled(True)
        self.btn_audition.setEnabled(True)

    # ------------------------------------------------------------------
    # 试听（本对话框内置的渲染/播放路径）
    # ------------------------------------------------------------------

    def _audition(self) -> None:
        if self.score is None or not self.score.notes:
            return
        if self._playing:
            self._stop_audio()
            return
        from ..arrange import Options, arrange
        from ..instrument import Instrument
        from .worker import SynthWorker
        try:
            plan = arrange(self.score, Instrument(), Options())
        except Exception as exc:
            self.lbl_preview.setText('⚠ 试听失败：%s' % exc)
            return
        self.btn_audition.setEnabled(False)
        self.synth_worker = SynthWorker(plan, sample_rate=22050, parent=self)
        self.synth_worker.ready.connect(self._on_audio_ready)
        self.synth_worker.failed.connect(self._on_audio_failed)
        self.synth_worker.start()

    def _on_audio_failed(self, message: str) -> None:
        self.lbl_preview.setText('⚠ 试听失败：%s' % message)
        self.btn_audition.setEnabled(True)

    def _on_audio_ready(self, wav: bytes, duration: float) -> None:
        import tempfile
        import winsound
        self._stop_audio()
        try:
            fd, path = tempfile.mkstemp(prefix='gtiharmonica-jianpu-',
                                        suffix='.wav')
            os.close(fd)
            with open(path, 'wb') as fh:
                fh.write(wav)
        except Exception as exc:
            self.lbl_preview.setText('⚠ 试听失败：%s' % exc)
            self.btn_audition.setEnabled(True)
            return
        self._play_wav = path
        try:
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC
                               | winsound.SND_NODEFAULT)
        except Exception as exc:
            self.lbl_preview.setText('⚠ 试听失败：%s' % exc)
            self.btn_audition.setEnabled(True)
            return
        self._playing = True
        self.btn_audition.setText('停止试听')
        self.btn_audition.setEnabled(True)
        QTimer.singleShot(int(max(duration, 0.5) * 1000) + 400,
                          self._audition_done)

    def _audition_done(self) -> None:
        if self._playing:
            self._stop_audio()

    def _stop_audio(self) -> None:
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
        self._playing = False
        self.btn_audition.setText('试听')
        if self._play_wav and os.path.exists(self._play_wav):
            try:
                os.remove(self._play_wav)
            except OSError:
                pass
        self._play_wav = ''

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------

    def _save(self) -> None:
        if self.score is None or not self.score.notes:
            return
        from ..score import save_json_score
        try:
            os.makedirs(self.library_dir, exist_ok=True)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, '无法写入曲库', str(exc))
            return
        base = self.score.title or 'jianpu'
        safe = ''.join(c for c in base if c not in '\\/:*?"<>|').strip() or 'jianpu'
        target = os.path.join(self.library_dir, safe + '.json')
        n = 1
        while os.path.exists(target):
            target = os.path.join(self.library_dir, '%s (%d).json' % (safe, n))
            n += 1
        try:
            save_json_score(self.score, target)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, '保存失败', str(exc))
            return
        self.saved_path = target
        self.accept()

    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._shutdown()
        super().closeEvent(event)

    def reject(self) -> None:
        self._shutdown()
        super().reject()

    def _shutdown(self) -> None:
        self._stop_audio()
        # SynthWorker 是一次性渲染任务，没有 cancel —— 只停声音，
        # 渲染线程让它在结束后自然回收。
