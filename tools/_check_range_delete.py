# -*- coding: utf-8 -*-
"""整段删除专项检查：

  1. 工具栏按钮进入点选模式，状态栏有引导
  2. 时间轴点两次 → 段内（重叠）音符全部删除，数量与预期一致
  3. Esc / 右键 取消，不删除
  4. Ctrl+Z 撤销后恢复原状
  5. 新增按钮后工具栏在最小窗口宽度下不溢出
"""
from __future__ import annotations

import os
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


def pump(ms=300):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def load_first_song(win):
    for name in sorted(os.listdir(win.library_dir)):
        if not name.lower().endswith(('.mid', '.midi', '.json')):
            continue
        try:
            score = load_score(os.path.join(win.library_dir, name))
            plan = arrange(score, win.instrument, win.options)
        except Exception:
            continue
        if plan.duration > 12 and len(plan.steps) > 40:
            win.score = score
            win.score_path = os.path.join(win.library_dir, name)
            win.plan = plan
            win.roll.set_plan(plan)
            win._refresh_info(score)
            win._ensure_editor_doc()
            return plan
    return None


def axis_click(editor, frac_x):
    y = int(editor.height() - 8)
    QTest.mouseClick(editor, Qt.LeftButton,
                     pos=QPoint(int(editor.width() * frac_x), y))


def count_in_range(notes, a, b):
    return sum(1 for n in notes if n.start < b and n.end > a)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_qss())
    win = MainWindow(Config(), os.path.join(ROOT, 'songs'))
    win.show()
    win.resize(1360, 880)
    pump(600)

    plan = load_first_song(win)
    if plan is None:
        print('  [跳过] 没有合适的曲目')
        return 1
    win.set_mode('edit')
    pump(200)
    ed = win.editor.editor
    msgs = []
    win.editor.statusMessage.connect(msgs.append)

    print('--- 整段删除 ---')
    win.editor.toolbar.btn_range.click()
    check('按钮进入点选模式', ed._range_pick and not ed._range)
    check('状态栏有引导', msgs and '整段删除' in msgs[-1], msgs[-1] if msgs else '')

    doc = ed.doc()
    before = len(doc.notes)
    # 先把视图收小一点，让两个点击点之间有足量音符
    ed.set_zoom(0.3)
    ta = ed._x_to_time(ed.width() * 0.25)   # 与点击完全一致的换算（含 gutter）
    tb = ed._x_to_time(ed.width() * 0.55)
    expect = count_in_range(doc.notes, ta, tb)
    check('选段内有可删音符', expect > 0, '%d 个' % expect)

    axis_click(ed, 0.25)
    check('第 1 次点击设起点', ed._range_pick and len(ed._range) == 1)
    axis_click(ed, 0.55)
    pump(250)
    after = len(doc.notes)
    check('第 2 次点击删除段内音符', after == before - expect,
          'before=%d after=%d expect删%d' % (before, after, expect))
    check('删除后退出点选模式', not ed._range_pick and ed._range is None)

    # 撤销恢复
    QTest.keyClick(ed, Qt.Key_Z, Qt.ControlModifier)
    pump(100)
    check('Ctrl+Z 撤销恢复', len(doc.notes) == before,
          '%d -> %d' % (after, len(doc.notes)))

    print('--- 取消路径 ---')
    win.editor.toolbar.btn_range.click()
    axis_click(ed, 0.3)
    QTest.keyClick(ed, Qt.Key_Escape)
    check('Esc 取消', not ed._range_pick and ed._range is None)
    check('Esc 后没有删除', len(doc.notes) == before)

    win.editor.toolbar.btn_range.click()
    axis_click(ed, 0.3)
    QTest.mouseClick(ed, Qt.RightButton, pos=QPoint(60, int(ed.height() - 8)))
    check('右键取消', not ed._range_pick and ed._range is None)
    check('右键后没有删除', len(doc.notes) == before)

    print('--- 剪掉时间（ripple delete，与整段删除独立）---')
    win.editor.toolbar.btn_cut.click()
    check('剪掉时间按钮进入点选模式', ed._range_pick and ed._pick_action == 'cut')
    # 整段删除按钮点击 → 切换模式而不是叠加
    win.editor.toolbar.btn_range.click()
    check('点整段删除切换回 delete 模式',
          ed._range_pick and ed._pick_action == 'delete')
    win.editor.toolbar.btn_cut.click()          # 再切回 cut，_range 应重置
    check('切回 cut 模式且起点已重置', ed._pick_action == 'cut'
          and ed._range is None)

    doc = ed.doc()
    ed.set_zoom(0.4)
    ta = ed._x_to_time(ed.width() * 0.3)
    tb = ed._x_to_time(ed.width() * 0.6)
    gap = tb - ta
    before_n = len(doc.notes)
    dur_before = ed._duration
    expect = count_in_range(doc.notes, ta, tb)
    # 剪掉前「tb 之后」的音符；剪掉后它们会出现在 ta 之后（前移 gap 秒）
    tail_before = sorted((round(n.start, 3), n.pitch)
                         for n in doc.notes if n.start >= tb - 1e-6)
    check('剪掉区间内有音符', expect > 0, '%d 个' % expect)

    axis_click(ed, 0.3)
    axis_click(ed, 0.6)
    pump(250)
    after_n = len(doc.notes)
    check('剪掉删除段内音符', after_n == before_n - expect,
          '%d -> %d（期望删 %d）' % (before_n, after_n, expect))
    tail_after = sorted((round(n.start, 3), n.pitch)
                        for n in doc.notes if n.start >= ta - 1e-6)
    check('段后音符整体前移 gap 秒',
          len(tail_after) == len(tail_before) and
          all(abs((s1 - gap) - s2) < 0.05 and p1 == p2
              for (s1, p1), (s2, p2) in zip(tail_before, tail_after)),
          'tail %d -> %d' % (len(tail_before), len(tail_after)))
    check('总时长缩短了', ed._duration < dur_before - 0.01,
          'duration %.2f -> %.2f' % (dur_before, ed._duration))
    QTest.keyClick(ed, Qt.Key_Z, Qt.ControlModifier)
    pump(100)
    check('剪掉可撤销', len(doc.notes) == before_n)

    print('--- 工具栏布局（最小宽度 1080）---')
    win.resize(1080, 760)
    pump(400)
    bar = win.editor.toolbar
    over = [b.text() for b in bar.findChildren(type(win.editor.toolbar.btn_undo))
            if b.isVisible() and b.mapTo(bar, b.rect().topLeft()).x()
            + b.width() > bar.width()]
    check('工具栏按钮不超出编辑器宽度', not over, str(over))

    print()
    total, passed = len(RESULTS), sum(RESULTS)
    print('=' * 70)
    print('通过 %d 项，失败 %d 项' % (passed, total - passed))
    print('=' * 70)
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
