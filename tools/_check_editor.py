# -*- coding: utf-8 -*-
"""编辑器全功能自检：EditDoc 全部操作 + 撤销往返 + 面板交互。

背景（2026-09-20）：用户反馈「撤销撤销不了」。撤销栈本身没错，
根因是编辑器工具栏的输入框（BPM/拍号/吸附）会把 Ctrl+Z 吃掉当成
「撤销框里的文字」。修复 = QShortcut 挂 WidgetWithChildrenShortcut；
本套件覆盖：每个编辑操作 undo/redo 状态严格往返、真实曲库数据下的
面板按钮流程、输入框持有焦点时的 Ctrl+Z 回归用例、插入空白/音阶。

用法：python tools/_check_editor.py
"""
from __future__ import annotations

import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
app.setStyle('Fusion')
from gtiharmonica.gui.theme import apply_theme
apply_theme(app, 'dark')

from gtiharmonica.edit import EditDoc
from gtiharmonica.score import load_score

failures = []


def check(name, cond, detail=''):
    if cond:
        print('  OK   %s' % name)
    else:
        failures.append(name)
        print('  FAIL %s %s' % (name, detail))


def fingerprint(doc):
    return tuple(sorted((n.pitch, round(n.start, 4), round(n.duration, 4))
                        for n in doc.notes))


# ------------------------------------------------------------------
# 真实数据：从曲库编一首曲子建立编辑文档
# ------------------------------------------------------------------
from gtiharmonica.config import Config
from gtiharmonica.arrange import arrange

cfg = Config()
instrument, options, cost, scheduler = cfg.build()
plan = None
for f in sorted(glob.glob(os.path.join(ROOT, 'songs', '*.json'))):
    try:
        sc = load_score(f)
        p = arrange(sc, instrument, options)
        if len(p.steps) > 40:
            plan = p
            break
    except Exception:
        continue
assert plan is not None, '曲库里没有可用的曲子'

doc = EditDoc.from_steps(plan.steps, title='check')
print('=== 编辑器自检（样本 %d 音）===' % len(doc))


def roundtrip(name, fn):
    """每个用例自取基准：fn 改动 → undo 回原 → redo 回新。"""
    before = fingerprint(doc)
    fn()
    after = fingerprint(doc)
    ok_undo = doc.undo()
    back = fingerprint(doc)
    ok_redo = doc.redo()
    again = fingerprint(doc)
    check('%s（undo 往返）' % name,
          ok_undo and back == before and ok_redo and again == after)


roundtrip('插入音符', lambda: doc.insert(72, doc.duration + 0.5, 0.25))
roundtrip('平移（move）', lambda: doc.move(range(3), 0.25, 0))
roundtrip('改时值（resize）', lambda: doc.resize([0], 0.25, 'right'))
roundtrip('改音高（set_pitch）',
          lambda: doc.set_pitch([0], doc.notes[0].pitch + 2))
roundtrip('删除（delete）', lambda: doc.delete([1, 2]))
roundtrip('剪掉时间（cut_time）', lambda: doc.cut_time(1.0, 2.0))
roundtrip('拆音（split）',
          lambda: doc.split(0, doc.notes[0].start + 0.2))
roundtrip('量化（quantize）', lambda: doc.quantize(range(len(doc)), 0.5))
roundtrip('调内吸附（snap_to_scale）',
          lambda: doc.snap_to_scale(range(len(doc))))
roundtrip('重叠修整（fix_overlaps）', lambda: doc.fix_overlaps())
roundtrip('改 BPM（set_context）',
          lambda: doc.set_context(bpm=doc.bpm + 4))
roundtrip('对齐小节线（realign_phase）', lambda: doc.realign_phase())


# 准备两枚相邻碎片；准备的 insert 先清栈，不参与 merge 的撤销断言
n = doc.duration + 0.5
doc.insert(72, n, 0.25)
doc.insert(72, n + 0.2, 0.25)
doc.clear_history()
roundtrip('碎片合并（merge）',
          lambda: doc.merge(range(len(doc) - 2, len(doc))))

# ------------------------------------------------------------------
# 插入空白 / 插入音阶（数据层）
# ------------------------------------------------------------------
before_pairs = [(n.start, n.pitch) for n in doc.notes]
doc.insert_time(3.0, 1.5)
moved_ok = all(abs(n.start - s - 1.5) < 1e-6
               for (s, p), n in zip(before_pairs, doc.notes) if s >= 3.0)
still_ok = all(abs(n.start - s) < 1e-6
               for (s, p), n in zip(before_pairs, doc.notes) if s < 3.0)
check('插入空白（后移正确、前段不动）', moved_ok and still_ok)
undo_ok = doc.undo()
check('插入空白（undo 往返）',
      undo_ok and [(n.start, n.pitch) for n in doc.notes] == before_pairs)

doc.set_context(scale='major', key=0)
at = doc.duration + 1.0          # 全曲之外的空区：插入的音好剥离
cnt = doc.insert_scale(at, 'updown', 0.5)
inserted = [n for n in doc.notes if n.start >= at - 1e-6]
in_scale = all((n.pitch % 12) in (0, 2, 4, 5, 7, 9, 11) for n in inserted)
durs = {round(n.duration, 4) for n in inserted}
check('插入音阶（数量/调内/时值统一）',
      cnt == len(inserted) and cnt > 0 and in_scale and len(durs) == 1,
      'cnt=%d inserted=%d in_scale=%s durs=%s'
      % (cnt, len(inserted), in_scale, durs))
undo_ok = doc.undo()
check('插入音阶（undo 往返）', undo_ok and not any(
    n.start >= at - 1e-6 for n in doc.notes))

# ------------------------------------------------------------------
# 面板级：真实控件 + 撤销按钮 + 输入框焦点下 Ctrl+Z（回归用例）
# ------------------------------------------------------------------
from gtiharmonica.gui.editor import EditorPanel
from PySide6.QtWidgets import QInputDialog

panel = EditorPanel()
panel.set_doc(EditDoc.from_steps(plan.steps, title='check'))
panel.refresh_labels()
panel.show()
loop = QEventLoop(); QTimer.singleShot(400, loop.quit); loop.exec()

doc2 = panel.editor.doc()
n_before = len(doc2)

# 2a. 删一个音 → 点「撤销」按钮 → 数量回来
# （绕过编辑器 API 直接改 doc，changed 信号不会发，按钮使能要手动打开）
doc2.delete([0])
panel.toolbar.btn_undo.setEnabled(True)
panel.toolbar.btn_undo.click()
check('面板「撤销」按钮', len(panel.editor.doc()) == n_before)

# 2b. 回归用例：焦点在 BPM 输入框上按 Ctrl+Z，必须作用到乐谱
fp_orig = fingerprint(doc2)
doc2.set_pitch([0], doc2.notes[0].pitch + 3)
fp_edited = fingerprint(doc2)
check('set_pitch 已产生改动（前置断言）', fp_edited != fp_orig)
spin = panel.infobar.spin_bpm
spin.setFocus()
QTest.keyClick(spin, Qt.Key_Z, Qt.ControlModifier)
check('焦点在输入框时 Ctrl+Z 撤销了 set_pitch（回归）',
      fingerprint(doc2) == fp_orig)

# 2c. 插入空白（面板流程，QInputDialog 打桩）
panel.editor.set_position(5.0)
orig_getdouble = QInputDialog.getDouble
QInputDialog.getDouble = staticmethod(lambda *a, **k: (1.5, True))
fp_pre = fingerprint(panel.editor.doc())
panel.insert_gap_at_playhead()
QInputDialog.getDouble = orig_getdouble
fp_post = fingerprint(panel.editor.doc())
moved = sum(1 for (p0, s0, d0), (p1, s1, d1)
            in zip(sorted(fp_pre), sorted(fp_post)) if abs(s1 - s0) > 1e-6)
check('面板「插入空白」流程（播放头 5.0s + 1.5s）',
      moved > 0 and fp_post != fp_pre)
panel.editor.undo()
check('插入空白后撤销往返', fingerprint(panel.editor.doc()) == fp_pre)

# 2d. 插入音阶（面板流程，打桩两个输入框）
orig_getitem = QInputDialog.getItem
orig_getdouble2 = QInputDialog.getDouble
QInputDialog.getItem = staticmethod(lambda *a, **k: ('上行 + 下行', True))
QInputDialog.getDouble = staticmethod(lambda *a, **k: (0.5, True))
fp_pre2 = fingerprint(panel.editor.doc())
panel.insert_scale_dialog()
QInputDialog.getItem = orig_getitem
QInputDialog.getDouble = orig_getdouble2
fp_post2 = fingerprint(panel.editor.doc())
added = len(fp_post2) - len(fp_pre2)
check('面板「插入音阶」流程', added > 0)
panel.editor.undo()
check('插入音阶后撤销往返', fingerprint(panel.editor.doc()) == fp_pre2)

panel.close()

print()
if failures:
    print('=== %d 项失败：%s ===' % (len(failures), '；'.join(failures)))
    sys.exit(1)
print('=== 编辑器全部功能自检通过 ===')


# ------------------------------------------------------------------
# 6. 回归用例（2026-09-20）：拖动后撤销按钮可用 / 任意位置选点 / 选中高亮刷新
# ------------------------------------------------------------------
from PySide6.QtCore import QPoint                       # noqa: E402

ed3 = panel.editor
doc3 = ed3.doc()
panel.refresh_labels()
loop = QEventLoop(); QTimer.singleShot(300, loop.quit); loop.exec()

# 6a. 拖动音符后：撤销按钮必须变为可用，点击后位置还原
n = doc3.notes[0]
x0 = ed3._time_to_x(n.start + n.duration / 2)
y0 = ed3._pitch_to_y(n.pitch) + ed3._row_height() / 2
before = (n.start, n.pitch)
QTest.mousePress(ed3, Qt.LeftButton, pos=QPoint(int(x0), int(y0)))
QTest.mouseMove(ed3, QPoint(int(x0) + 40, int(y0)))
QTest.mouseRelease(ed3, Qt.LeftButton, pos=QPoint(int(x0) + 40, int(y0)))
loop = QEventLoop(); QTimer.singleShot(200, loop.quit); loop.exec()
check('拖动后位置已改变（前置）', (doc3.notes[0].start, doc3.notes[0].pitch) != before)
check('拖动后撤销按钮可用（回归）', panel.toolbar.btn_undo.isEnabled())
panel.toolbar.btn_undo.click()
loop = QEventLoop(); QTimer.singleShot(150, loop.quit); loop.exec()
check('点撤销按钮还原拖动（回归）',
      (doc3.notes[0].start, doc3.notes[0].pitch) == before)

# 6b. 整段删除：点绘图区任意位置都能记录时间点
panel.toolbar.btn_range.click()
QTest.mouseClick(ed3, Qt.LeftButton,
                 pos=QPoint(int(ed3.width() * 0.3), int(ed3.height() * 0.4)))
loop = QEventLoop(); QTimer.singleShot(120, loop.quit); loop.exec()
check('任意位置可选第 1 个点（回归）',
      ed3._range is not None and len(ed3._range) == 1)
ed3.cancel_range_pick()

# 6c. 选中音符必须让静态层缓存失效（高亮才会立刻画出）
k0 = ed3._layer_key()
n = doc3.notes[3]
x0 = ed3._time_to_x(n.start + n.duration / 2)
y0 = ed3._pitch_to_y(n.pitch) + ed3._row_height() / 2
QTest.mouseClick(ed3, Qt.LeftButton, pos=QPoint(int(x0), int(y0)))
loop = QEventLoop(); QTimer.singleShot(120, loop.quit); loop.exec()
check('选中驱动缓存键变化（回归）', ed3._layer_key() != k0 and len(ed3._sel) == 1)

print()
if failures:
    print('=== %d 项失败：%s ===' % (len(failures), '；'.join(failures)))
    sys.exit(1)
print('=== 编辑器全部功能自检通过（含 5 项回归用例）===')


# ------------------------------------------------------------------
# 7. 回归（2026-09-20）：切主题后卷帘与编辑画布必须重建静态层
# ------------------------------------------------------------------
ed4 = panel.editor
panel.refresh_labels()
loop = QEventLoop(); QTimer.singleShot(200, loop.quit); loop.exec()

def bg_of(w):
    img = w.grab().toImage()
    c = img.pixelColor(int(w.width() * 0.6), 30)
    return (c.red(), c.green(), c.blue())

from gtiharmonica.gui.theme import apply_theme, C     # noqa: E402
before_mode = C.mode
dark_bg = bg_of(ed4)
apply_theme(app, 'light' if before_mode == 'dark' else 'dark')
ed4.update(); panel.refresh_labels()
loop = QEventLoop(); QTimer.singleShot(150, loop.quit); loop.exec()
switched_bg = bg_of(ed4)
check('切换主题后编辑画布底色重建（回归）', dark_bg != switched_bg)
# 切回原主题，避免影响后续（本套件已到末尾，稳妥起见）
apply_theme(app, before_mode)
ed4.update()
loop = QEventLoop(); QTimer.singleShot(100, loop.quit); loop.exec()

print()
if failures:
    print('=== %d 项失败：%s ===' % (len(failures), '；'.join(failures)))
    sys.exit(1)
print('=== 编辑器全部功能自检通过（含 6 项回归用例）===')
