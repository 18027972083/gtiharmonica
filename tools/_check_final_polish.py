"""最终打磨专项检查：

  1. 音符连贯度数值标签按百分比显示（本次修的显示 bug）
  2. 进度条位于旋律预览卡片内部（而不是窗口底部）
  3. 编辑视图的卷帘随播放位置自动滚动
  4. 没有试听音频时 seek 仍然移动播放头（纯视觉跳转）
  5. 演奏中 seek 被拒绝并提示
  6. 编辑器时间轴点击发出跳转请求
"""
from __future__ import annotations

import os
# 公告框是模态的：自检没人点按钮，必须显式关掉（否则 exec() 永久阻塞）
os.environ.setdefault('GTIHARMONICA_NO_ANNOUNCE', '1')
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPoint, QEventLoop, Qt, QTimer      # noqa: E402
from PySide6.QtTest import QTest                                # noqa: E402
from PySide6.QtWidgets import QApplication                      # noqa: E402

from gtiharmonica.config import Config                          # noqa: E402
from gtiharmonica.arrange import arrange                        # noqa: E402
from gtiharmonica.gui.theme import build_qss, build_palette
from gtiharmonica.gui.main import MainWindow                    # noqa: E402
from gtiharmonica.score import load_score                       # noqa: E402

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append(ok)
    print('  [%s] %s%s' % ('通过' if ok else '失败', name,
                           '：' + detail if detail and not ok else ''))


def pump(ms=400):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def load_first_song(win):
    """同步载入曲库第一首（时长 > 10s，跟随滚动才可观测）。"""
    for name in sorted(os.listdir(win.library_dir)):
        if not name.lower().endswith(('.mid', '.midi', '.json')):
            continue
        path = os.path.join(win.library_dir, name)
        try:
            score = load_score(path)
            plan = arrange(score, win.instrument, win.options)
        except Exception:
            continue
        if plan.duration <= 12:
            continue
        win.score = score
        win.score_path = path
        win.plan = plan
        win.roll.set_plan(plan)
        win._refresh_info(score)
        win._update_progress(0.0, plan.duration)
        return score, plan
    return None, None


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_qss())
    win = MainWindow(Config(), os.path.join(ROOT, 'songs'))
    win.show()
    pump(600)

    print('--- 1. 参数滑块数值标签 ---')
    win.params.f_gate.set_value(0.9)
    check('连贯度 0.9 显示 90%', win.params.f_gate.value_label.text() == '90%',
          win.params.f_gate.value_label.text())
    win.params.f_gate.set_value(0.7)
    check('连贯度 0.7 显示 70%', win.params.f_gate.value_label.text() == '70%',
          win.params.f_gate.value_label.text())
    win.params.f_speed.set_value(1.0)
    check('速度 1.0 显示 1.00×',
          win.params.f_speed.value_label.text() == '1.00×',
          win.params.f_speed.value_label.text())
    win.params.f_breath.set_value(80)
    check('换气 80 显示 80 ms',
          win.params.f_breath.value_label.text() == '80 ms',
          win.params.f_breath.value_label.text())

    print('--- 2. 进度条位置（应在旋律预览卡片内）---')
    card = win.roll.parentWidget()
    while card is not None and card.objectName() != 'card':
        card = card.parentWidget()
    slider_in_card = False
    w = win.slider.parentWidget()
    while w is not None:
        if w is card:
            slider_in_card = True
            break
        w = w.parentWidget()
    check('进度条在卡片内', slider_in_card and card is not None)
    if card is not None:
        sy = win.slider.mapTo(card, win.slider.rect().topLeft()).y()
        roll_y = win.roll.mapTo(card, win.roll.rect().topLeft()).y()
        check('进度条紧贴卷帘下方', sy > roll_y,
              'slider y=%d roll y=%d' % (sy, roll_y))

    print('--- 3/4. 播放联动与跳转 ---')
    score, plan = load_first_song(win)
    if plan is None:
        print('  [跳过] 曲库里没有时长 >12s 的曲子')
    else:
        print('  曲目：%s（%.1fs，%d 步）'
              % (score.title, plan.duration, len(plan.steps)))

        # 编辑文档 + 跟随滚动
        check('建立编辑文档', win._ensure_editor_doc())
        win.set_mode('edit')
        pump(200)
        ed = win.editor.editor
        start_left = ed._view_left
        t = 0.0
        while t < plan.duration * 0.9:
            t += 1.0
            win.editor.set_position(t)
        moved = ed._view_left - start_left
        check('编辑器卷帘自动滚动', moved > 1.0,
              'view_left 只前进 %.2fs' % moved)

        # 预览卷帘也滚动
        win.set_mode('preview')
        pump(100)
        r_left = win.roll._view_left
        win.roll.set_position(plan.duration * 0.5, -1)
        check('预览卷帘自动滚动',
              win.roll._view_left > r_left or r_left > 0)

        # 无音频 seek
        win.audio._wav = None
        win._seek_from_roll(5.0)
        check('无音频 seek 移动播放头', abs(win.roll._position - 5.0) < 0.3,
              'position=%.3f' % win.roll._position)
        check('seek 同步编辑器', abs(ed._position - 5.0) < 0.3,
              'editor position=%.3f' % ed._position)
        want = int(5.0 / plan.duration * 1000)
        check('seek 同步进度条', abs(win.slider.value() - want) <= 1,
              'slider=%d want=%d' % (win.slider.value(), want))
        check('seek 同步时间标签',
              win.time_left.text().startswith('00:05'),
              win.time_left.text())

        # 演奏中 seek 拒绝
        class FakeWorker:
            def isRunning(self):
                return True
        saved = win.play_worker
        win.play_worker = FakeWorker()
        win._seek_from_roll(8.0)
        win.play_worker = saved
        check('演奏中 seek 被拒绝', abs(win.roll._position - 5.0) < 0.3,
              'position=%.3f（应保持 5.0）' % win.roll._position)
        check('给出提示', 'F8' in win.status_label.text(),
              win.status_label.text())

        # 编辑器时间轴点击 → seek
        win.set_mode('edit')
        pump(100)
        before = win.roll._position
        editor_widget = ed
        axis_y = int(editor_widget.height() - 8)   # 底部时间轴条内
        QTest.mouseClick(editor_widget, Qt.LeftButton,
                         pos=QPoint(int(editor_widget.width() * 0.8), axis_y))
        after = win.roll._position
        check('时间轴点击跳转', abs(after - before) > 0.5,
              'before=%.2f after=%.2f' % (before, after))

    print()
    total, passed = len(RESULTS), sum(RESULTS)
    print('=' * 70)
    print('通过 %d 项，失败 %d 项' % (passed, total - passed))
    print('=' * 70)
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
