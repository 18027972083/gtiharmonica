"""编辑器界面自检：在进程内驱动 Qt，检查交互与真实渲染结果。

本机不能做真实的鼠标点击（审批弹窗被禁用），所以这里直接构造
QMouseEvent 调事件处理函数，再用 widget.grab() 读真实像素来验证
「到底画出来没有」—— 只看内部状态的话，画错了也发现不了。

运行：python tools/_check_editor_ui.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QPointF, Qt                      # noqa: E402
from PySide6.QtGui import QColor, QMouseEvent                       # noqa: E402
from PySide6.QtWidgets import QApplication                          # noqa: E402

from gtiharmonica.edit import EditDoc                                # noqa: E402
from gtiharmonica.gui.editor import EditorPanel                      # noqa: E402
from gtiharmonica.gui.theme import C, apply_theme                    # noqa: E402
from gtiharmonica.score import Note                                  # noqa: E402

#: 颜色常量现在由主题统一提供（editor / widgets 不再各存一份拷贝）。
#: 值是 QColor —— theme.C 给的是 QSS 用的字符串，绘制时本来也要包装一次。
#: 深色主题下「选中的高亮色」与「音符底色」是两种不同的颜色，像素断言才有
#: 意义（浅色主题里两者同为品牌蓝，区分不开）。
C_SEL = QColor()
C_NOTE = QColor()


def sync_colors() -> None:
    """按当前主题取色。切主题后要再调一次，否则断言比的是旧颜色。"""
    global C_SEL, C_NOTE
    C_SEL = QColor(C.ACCENT)         # 选中音符的高亮填充
    C_NOTE = QColor(C.ROLL_NOTE)     # 未选中音符的填充


sync_colors()

PASS = 0
FAIL = 0
APP = None


def check(name: str, ok: bool, detail: str = '') -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print('  ok   %s%s' % (name, ('  [%s]' % detail) if detail else ''))
    else:
        FAIL += 1
        print('  FAIL %s%s' % (name, ('  [%s]' % detail) if detail else ''))
    return ok


# ---------------------------------------------------------------------------
# 事件构造
# ---------------------------------------------------------------------------

def press(widget, x, y, button=Qt.LeftButton, mods=Qt.NoModifier):
    widget.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(x, y),
                                       button, button, mods))


def move(widget, x, y, button=Qt.LeftButton, mods=Qt.NoModifier):
    widget.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(x, y),
                                      Qt.NoButton, button, mods))


def release(widget, x, y, button=Qt.LeftButton, mods=Qt.NoModifier):
    widget.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease,
                                         QPointF(x, y), button, Qt.NoButton,
                                         mods))


def double(widget, x, y):
    widget.mouseDoubleClickEvent(
        QMouseEvent(QEvent.MouseButtonDblClick, QPointF(x, y),
                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))


def drag(widget, x0, y0, x1, y1, steps=4, mods=Qt.NoModifier):
    """一次完整的按住—移动—松开。中间插几步，模拟真实拖动。"""
    press(widget, x0, y0, mods=mods)
    for i in range(1, steps + 1):
        move(widget, x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
    release(widget, x1, y1, mods=mods)


def pixel_at(widget, x, y) -> QColor:
    img = widget.grab().toImage()
    return img.pixelColor(int(x), int(y))


def color_near(a: QColor, b: QColor, tol: int = 26) -> bool:
    return (abs(a.red() - b.red()) <= tol and abs(a.green() - b.green()) <= tol
            and abs(a.blue() - b.blue()) <= tol)


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------

def make_doc() -> EditDoc:
    """120 BPM 4/4：一小节 2 秒，一拍 0.5 秒。音符放在白键行上好定位。"""
    notes = []
    pitches = [60, 62, 64, 65, 67]
    for i, p in enumerate(pitches):
        notes.append(Note(pitch=p, start=0.5 + i * 0.5, duration=0.4))
    return EditDoc(notes=notes, title='编辑自检', bpm=120.0,
                   time_sig_num=4, time_sig_den=4)


def build_panel():
    panel = EditorPanel()
    panel.resize(900, 360)
    panel.set_doc(make_doc())
    panel.show()
    APP.processEvents()
    return panel


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------

def test_render() -> None:
    print('\n[1] 真实渲染：音符和小节线都画出来了')

    panel = build_panel()
    ed = panel.editor
    doc = ed.doc()

    n0 = doc.notes[0]
    x = ed._time_to_x(n0.start + n0.duration / 2)
    y = ed._pitch_to_y(n0.pitch) + ed._row_height() / 2
    col = pixel_at(ed, x, y)
    check('音符位置画出了音符色', color_near(col, C_NOTE),
          col.name())

    # 小节线：第 2 小节线在 2.0 秒处，取一个没有音符的高度来验
    x_bar = ed._time_to_x(2.0)
    y_top = ed._plot_rect().top() + 3
    col_bar = pixel_at(ed, x_bar, y_top)
    check('小节线比底色亮', col_bar.lightness() > 24, col_bar.name())

    # 空白处应该是底色（证明不是整片糊成音符色）
    col_bg = pixel_at(ed, 3, 3)
    check('角落是背景色', not color_near(col_bg, C_NOTE), col_bg.name())

    check('信息行统计正确', '音符' in panel.infobar.lbl_stats.text(),
          panel.infobar.lbl_stats.text())
    check('工具按钮可用', panel.toolbar.btn_merge.isEnabled())
    panel.close()


def test_click_select() -> None:
    print('\n[2] 单击选中 / Shift 加选')

    panel = build_panel()
    ed, doc = panel.editor, panel.editor.doc()

    def center(n):
        return (ed._time_to_x(n.start + n.duration / 2),
                ed._pitch_to_y(n.pitch) + ed._row_height() / 2)

    x, y = center(doc.notes[0])
    press(ed, x, y)
    release(ed, x, y)
    check('单击选中一个', ed.selection_count() == 1,
          str(ed.selection_count()))
    check('选中的是点的那个', ed.selected_notes()[0] is doc.notes[0])

    x2, y2 = center(doc.notes[2])
    press(ed, x2, y2, mods=Qt.ShiftModifier)
    release(ed, x2, y2, mods=Qt.ShiftModifier)
    check('Shift 加选到两个', ed.selection_count() == 2,
          str(ed.selection_count()))

    # 选中的音符必须真的用高亮色画出来
    col = pixel_at(ed, x, y)
    check('选中音符画成高亮色', color_near(col, C_SEL), col.name())

    # 点空白清空选区
    press(ed, ed._plot_rect().right() - 4, ed._plot_rect().bottom() - 4)
    release(ed, ed._plot_rect().right() - 4, ed._plot_rect().bottom() - 4)
    check('点空白清空选区', ed.selection_count() == 0)
    panel.close()


def test_drag_move() -> None:
    print('\n[3] 拖动移动：时间与音高')

    panel = build_panel()
    ed, doc = panel.editor, panel.editor.doc()
    ed.set_snap_grid(0.25)                      # 1/16 拍 = 0.125 秒

    n = doc.notes[0]
    before_start, before_pitch = n.start, n.pitch
    x = ed._time_to_x(n.start + n.duration / 2)
    y = ed._pitch_to_y(n.pitch) + ed._row_height() / 2

    row_h = ed._row_height()
    # 往右拖 0.6 秒、往上拖两行（两个半音）
    drag(ed, x, y, x + ed._pixels_per_second() * 0.6, y - row_h * 2)

    check('时间被拖动了', abs(n.start - before_start) > 0.1,
          '%.3f -> %.3f' % (before_start, n.start))
    check('音高被拖高了 2 个半音', n.pitch == before_pitch + 2,
          '%d -> %d' % (before_pitch, n.pitch))
    check('落点吸附到网格',
          abs((n.start / 0.125) - round(n.start / 0.125)) < 1e-6,
          '%.4f' % n.start)
    check('一次拖动只记一步撤销', len(doc._undo) == 1, str(len(doc._undo)))

    doc.undo()
    # undo 会把整个列表换成快照副本，必须重新取对象（这正是编辑器内部
    # 用 uid 而不是对象身份跟踪选区的原因）
    m = doc.notes[0]
    check('撤销后完全还原',
          abs(m.start - before_start) < 1e-9 and m.pitch == before_pitch,
          '%.3f %d' % (m.start, m.pitch))
    check('撤销后选区没有丢',
          ed.selection_count() == 1, str(ed.selection_count()))
    panel.close()


def test_drag_resize() -> None:
    print('\n[4] 拖边缘改时值')

    panel = build_panel()
    ed, doc = panel.editor, panel.editor.doc()
    ed.set_snap_grid(0.125)                     # 1/8 拍 = 0.0625 秒

    n = doc.notes[0]
    before_dur, before_start = n.duration, n.start
    x_right = ed._time_to_x(n.end)
    y = ed._pitch_to_y(n.pitch) + ed._row_height() / 2

    drag(ed, x_right, y, x_right + ed._pixels_per_second() * 0.5, y)
    check('右边缘拖动改长了时值', n.duration > before_dur + 0.2,
          '%.3f -> %.3f' % (before_dur, n.duration))
    check('右边缘拖动不动起点', abs(n.start - before_start) < 1e-9)

    # 左边缘：终点必须钉死
    n2 = doc.notes[1]
    end_before = n2.end
    x_left = ed._time_to_x(n2.start)
    y2 = ed._pitch_to_y(n2.pitch) + ed._row_height() / 2
    drag(ed, x_left, y2, x_left - ed._pixels_per_second() * 0.3, y2)
    check('左边缘拖动终点钉死', abs(n2.end - end_before) < 1e-6,
          '%.4f vs %.4f' % (n2.end, end_before))
    check('左边缘拖动改了起点', n2.start < n2.end - 0.01)
    panel.close()


def test_band_select() -> None:
    print('\n[5] 框选与合并长音（长音问题的兜底路径）')

    panel = build_panel()
    ed, doc = panel.editor, panel.editor.doc()

    # 先把前三个音改成同音高的碎片
    doc.notes[0].pitch = 72
    doc.notes[1].pitch = 72
    doc.notes[2].pitch = 72
    doc.notes[0].start, doc.notes[0].duration = 1.0, 0.3
    doc.notes[1].start, doc.notes[1].duration = 1.31, 0.3
    doc.notes[2].start, doc.notes[2].duration = 1.62, 0.3
    ed.refresh(reframe=True)
    panel.refresh_labels()

    p0 = ed._pitch_to_y(72) + ed._row_height() * 0.5
    x0 = ed._time_to_x(0.8)
    x1 = ed._time_to_x(2.1)
    row_h = ed._row_height()
    drag(ed, x0, p0 - row_h * 0.5, x1, p0 + row_h * 0.5)
    check('框选选中 3 个碎片', ed.selection_count() == 3,
          str(ed.selection_count()))
    check('信息行提示了可合并碎片',
          '碎片' in panel.infobar.lbl_frag.text(),
          panel.infobar.lbl_frag.text())

    kept = ed.merge_selection()
    check('合并后只剩 1 个音', kept == 1, str(kept))
    m = doc.notes[0]
    check('合并后是长音', m.duration > 0.85, '%.3f' % m.duration)
    check('合并后起点取最早', abs(m.start - 1.0) < 1e-6, '%.3f' % m.start)

    doc.undo()
    check('合并不满意可以撤销', len(doc) == 5, str(len(doc)))
    panel.close()


def test_merge_without_selection() -> None:
    """没选中音符时点「合并长音」应该处理整首，而不是提示「请先选择」。

    用户想的就是「把这首曲子的碎片收拾一下」；逼他先框选是多一道手续，
    而转录出来的碎片往往散落在整首曲子里，手工框选根本不现实。
    """
    print('\n[5b] 未选中时的合并（一键收拾整首碎片）')

    notes = [Note(pitch=72, start=1.0 + k * 0.31, duration=0.3)
             for k in range(3)]
    notes += [Note(pitch=64, start=5.0 + k * 0.31, duration=0.3)
              for k in range(3)]
    doc = EditDoc(notes=notes, title='碎片', bpm=120.0)

    panel = EditorPanel()
    panel.resize(900, 360)
    panel.set_doc(doc)
    panel.show()
    APP.processEvents()
    ed = panel.editor
    ed.clear_selection()
    check('确实没有选中', ed.selection_count() == 0)
    check('信息行提示了可合并碎片', '碎片' in panel.infobar.lbl_frag.text(),
          panel.infobar.lbl_frag.text())

    ed.merge_selection()
    check('未选中也能一键合并整首', len(doc) == 2, '%d 个音' % len(doc))
    check('两段各自并成一个长音',
          sorted(n.pitch for n in doc.notes) == [64, 72],
          str(sorted(n.pitch for n in doc.notes)))
    check('合并后每段都是长音',
          all(n.duration > 0.8 for n in doc.notes),
          str([round(n.duration, 3) for n in doc.notes]))

    doc.undo()
    check('合并不满意可以撤销', len(doc) == 6, '%d 个音' % len(doc))
    panel.close()


def test_insert_and_delete() -> None:
    print('\n[6] 双击插入 / 键盘删除')

    panel = build_panel()
    ed, doc = panel.editor, panel.editor.doc()
    before = len(doc)

    x = ed._time_to_x(3.0)
    y = ed._pitch_to_y(69) + ed._row_height() / 2
    double(ed, x, y)
    check('双击插入了音符', len(doc) == before + 1, '%d -> %d' % (before, len(doc)))
    check('插入后自动选中它', ed.selection_count() == 1)

    from PySide6.QtGui import QKeyEvent
    key = QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier)
    ed.keyPressEvent(key)
    check('Delete 删掉选中', len(doc) == before, str(len(doc)))
    panel.close()


def test_zoom_and_navigation() -> None:
    print('\n[7] 缩放与视图导航')

    # 缩放的可见效果必须拿长曲子测：短曲子本来就铺满整屏，
    # 放大不会改变可见跨度（那是刻意的设计，不是 bug）。
    long_doc = EditDoc(notes=[Note(pitch=60 + (i % 12), start=i * 0.5,
                                   duration=0.4) for i in range(60)],
                       title='长曲', bpm=120.0)
    panel = EditorPanel()
    panel.resize(900, 360)
    panel.set_doc(long_doc)
    panel.show()
    APP.processEvents()
    ed = panel.editor

    check('长曲跨度确实超过一屏', ed._duration > ed.VIEW_SPAN,
          '%.1f 秒' % ed._duration)

    pps0 = ed._pixels_per_second()
    ed.set_zoom(ed.zoom() * 2.0)
    check('放大后每秒占更多像素', ed._pixels_per_second() > pps0 * 1.5,
          '%.1f -> %.1f px/s' % (pps0, ed._pixels_per_second()))
    check('放大后可见时长变短', ed._span() < ed._duration,
          'span=%.2f' % ed._span())

    ed.zoom_fit()
    check('适应窗口后能看到全曲', ed._span() >= ed._duration - 1e-6,
          'span=%.2f dur=%.2f' % (ed._span(), ed._duration))

    # 视图不能被拖到负时间
    ed._view_left = -5.0
    ed._clamp_view()
    check('视图左界不会越过 0', ed._view_left >= 0.0, '%.3f' % ed._view_left)

    # 右边界也不能把内容推出可见窗
    ed._view_left = 1e6
    ed._clamp_view()
    check('视图右界不越过内容末尾',
          ed._view_left + ed._span() <= ed._duration + 1e-6,
          '%.3f + %.3f vs %.3f' % (ed._view_left, ed._span(), ed._duration))

    ed.set_position(1.5)
    check('播放位置传入后不崩', True)
    panel.close()

    # 短曲子：默认铺满整屏不滚动；用户主动放大后才允许滚动看细节
    panel2 = build_panel()
    ed2 = panel2.editor
    ed2.set_zoom(1.0)
    check('短曲子默认铺满整屏不滚动',
          ed2._span() >= ed2._duration - 1e-9,
          'span=%.2f dur=%.2f' % (ed2._span(), ed2._duration))
    ed2.set_zoom(4.0)
    check('主动放大后可以滚动看细节',
          ed2._span() < ed2._duration,
          'span=%.2f dur=%.2f' % (ed2._span(), ed2._duration))
    panel2.close()


def test_context_edits() -> None:
    print('\n[8] 改拍号/BPM 会重排小节线')

    panel = build_panel()
    ed, doc = panel.editor, panel.editor.doc()

    check('4/4 一小节 2 秒', abs(doc.context().bar_sec - 2.0) < 1e-9,
          '%.3f' % doc.context().bar_sec)
    panel.infobar.cb_ts.setCurrentIndex(
        [i for i in range(panel.infobar.cb_ts.count())
         if panel.infobar.cb_ts.itemData(i) == (3, 4)][0])
    APP.processEvents()
    check('改成 3/4 后小节变成 1.5 秒',
          abs(doc.context().bar_sec - 1.5) < 1e-9, '%.3f' % doc.context().bar_sec)
    check('改拍号可以撤销', doc.can_undo)

    panel.infobar.spin_bpm.setValue(90)
    APP.processEvents()
    check('改 BPM 生效', abs(doc.bpm - 90.0) < 1e-6, '%.1f' % doc.bpm)
    check('BPM 改后拍长跟着变',
          abs(doc.context().beat_sec - 60.0 / 90.0) < 1e-9,
          '%.4f' % doc.context().beat_sec)
    panel.close()


def test_no_doc() -> None:
    print('\n[9] 没有文档时不崩')

    panel = EditorPanel()
    panel.resize(600, 300)
    panel.set_doc(None)
    panel.show()
    APP.processEvents()
    check('面板被禁用', not panel.isEnabled())
    img = panel.grab()
    check('空状态能渲染', not img.isNull(),
          '%dx%d' % (img.width(), img.height()))
    panel.close()


if __name__ == '__main__':
    APP = QApplication.instance() or QApplication(sys.argv)
    APP.setStyle('Fusion')
    apply_theme(APP, 'dark')      # 像素断言按深色取色：见文件头的说明
    sync_colors()
    print('=' * 62)
    print('编辑器界面自检')
    print('=' * 62)
    test_render()
    test_click_select()
    test_drag_move()
    test_drag_resize()
    test_band_select()
    test_merge_without_selection()
    test_insert_and_delete()
    test_zoom_and_navigation()
    test_context_edits()
    test_no_doc()
    print('\n' + '=' * 62)
    print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
    print('=' * 62)
    sys.exit(1 if FAIL else 0)
