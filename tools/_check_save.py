# -*- coding: utf-8 -*-
"""编辑器保存入口检查：按钮、Ctrl+S、与顶部按钮共用同一保存逻辑。"""
from __future__ import annotations

import os
# 公告框是模态的：自检没人点按钮，必须显式关掉（否则 exec() 永久阻塞）
os.environ.setdefault('GTIHARMONICA_NO_ANNOUNCE', '1')
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEventLoop, Qt, QTimer      # noqa: E402
from PySide6.QtTest import QTest                        # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

from gtiharmonica.config import Config                  # noqa: E402
from gtiharmonica.arrange import arrange                # noqa: E402
from gtiharmonica.gui.theme import build_qss, build_palette
from gtiharmonica.gui.main import MainWindow            # noqa: E402
from gtiharmonica.score import load_score               # noqa: E402

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append(ok)
    print('  [%s] %s%s' % ('通过' if ok else '失败', name,
                           '：' + detail if detail and not ok else ''))


def pump(ms=300):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_qss())
    win = MainWindow(Config(), os.path.join(ROOT, 'songs'))
    win.show()
    win.resize(1360, 880)
    pump(600)

    for name in sorted(os.listdir(win.library_dir)):
        if not name.lower().endswith(('.mid', '.midi', '.json')):
            continue
        try:
            score = load_score(os.path.join(win.library_dir, name))
            plan = arrange(score, win.instrument, win.options)
        except Exception:
            continue
        if len(plan.steps) > 40:
            break
    win.score = score
    win.score_path = os.path.join(win.library_dir, name)
    win.plan = plan
    win.roll.set_plan(plan)
    win._ensure_editor_doc()
    win.set_mode('edit')
    pump(200)

    tmp = os.path.join(tempfile.gettempdir(), 'deltaharp_save_test.json')
    if os.path.exists(tmp):
        os.remove(tmp)
    win._ask_save_target = lambda *a, **k: tmp
    calls = []
    win.editor.saveRequested.connect(lambda: calls.append(1))

    print('--- 编辑器保存入口 ---')
    check('工具栏有保存按钮', win.editor.toolbar.btn_save.isVisible(),
          win.editor.toolbar.btn_save.text())
    check('顶部按钮已改名「保存编辑结果」',
          win.btn_save_arrangement.text() == '保存编辑结果',
          win.btn_save_arrangement.text())

    # Ctrl+S
    ed = win.editor.editor
    ed.setFocus()
    QTest.keyClick(ed, Qt.Key_S, Qt.ControlModifier)
    pump(300)
    check('Ctrl+S 触发保存信号', len(calls) == 1)
    check('Ctrl+S 写出文件', os.path.isfile(tmp),
          win.status_label.text())

    # 工具栏按钮
    if os.path.exists(tmp):
        os.remove(tmp)
    win.editor.toolbar.btn_save.click()
    pump(300)
    check('保存按钮写出文件', os.path.isfile(tmp))

    if os.path.exists(tmp):
        reloaded = load_score(tmp)
        check('保存的文件能重新载入', len(reloaded.notes) == len(ed.doc().notes),
              '%d vs %d' % (len(reloaded.notes), len(ed.doc().notes)))
        os.remove(tmp)

    print('--- 工具栏布局（最小宽度 1080）---')
    win.resize(1080, 760)
    pump(400)
    bar = win.editor.toolbar
    over = [b.text() for b in bar.findChildren(type(win.editor.toolbar.btn_undo))
            if b.isVisible() and b.mapTo(bar, b.rect().topLeft()).x()
            + b.width() > bar.width()]
    check('工具栏不溢出', not over, str(over))

    print()
    total, passed = len(RESULTS), sum(RESULTS)
    print('=' * 70)
    print('通过 %d 项，失败 %d 项' % (passed, total - passed))
    print('=' * 70)
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
