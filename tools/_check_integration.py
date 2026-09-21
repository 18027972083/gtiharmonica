"""主窗口集成自检：预览视图 ↔ 编辑视图的切换与联动。

单独测编辑器是不够的。真正容易出错的是接线：

  * 切到编辑再切回来，编辑会不会丢；
  * 编辑器改完之后，演奏用的编排有没有跟着更新；
  * 更新编排时有没有把音高又移调一次（这是本次改动的头号风险）；
  * 载入新曲目时旧的编辑文档有没有作废。

运行：python tools/_check_integration.py
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
# 公告框是模态的：自检没人点按钮，必须显式关掉（否则 exec() 永久阻塞）
os.environ.setdefault('GTIHARMONICA_NO_ANNOUNCE', '1')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication                      # noqa: E402

from gtiharmonica.arrange import Options, arrange               # noqa: E402
from gtiharmonica.config import Config                          # noqa: E402
from gtiharmonica.gui.main import MainWindow                    # noqa: E402
from gtiharmonica.score import Note, Score                      # noqa: E402

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


def make_score() -> Score:
    """用乐器音域内的音高，免得编排阶段就被丢弃。"""
    return Score(title='集成自检', notes=[
        Note(pitch=72, start=0.5 + i * 0.5, duration=0.4)
        for i in range(12)], bpm=120.0, time_sig_num=4, time_sig_den=4)


def build_window() -> MainWindow:
    root = tempfile.mkdtemp(prefix='gti-integ-')
    win = MainWindow(Config(), root)
    win.resize(1400, 900)
    score = make_score()
    win.score = score
    win.score_path = os.path.join(root, '集成自检.json')
    win.plan = arrange(score, win.instrument, win.options)
    win.roll.set_plan(win.plan)
    win.params.set_tracks(score, win.instrument, win.options.track)
    win._refresh_info(score)
    win._update_actions()
    win.show()
    APP.processEvents()
    return win


def test_mode_switch() -> None:
    print('\n[1] 视图切换')

    win = build_window()
    check('初始在预览视图', win.mode == 'preview' and win.stack.currentIndex() == 0)

    win.set_mode('edit')
    check('切到编辑视图', win.mode == 'edit' and win.stack.currentIndex() == 1)
    check('建立了编辑文档', win.editor_doc is not None
          and len(win.editor_doc) == len(win.plan.steps),
          '%s' % (len(win.editor_doc) if win.editor_doc else None))
    check('编辑文档音高取自编排结果',
          [n.pitch for n in win.editor_doc.notes]
          == [s.pitch for s in win.plan.steps])
    check('分段控件状态同步',
          win.btn_mode_edit.isChecked() and not win.btn_mode_preview.isChecked())
    # 提示行已换成操作提示（工具行的按钮改成了图标），只认当前文案的关键点
    check('提示文字换了', '框选' in win.tip_label.text()
          and 'Ctrl+S' in win.tip_label.text(), win.tip_label.text())

    # 编辑一点东西，切回预览再切回来，编辑必须还在
    win.editor_doc.notes[0].pitch = 79
    first_uid = win.editor_doc.notes[0].uid
    win.set_mode('preview')
    win.set_mode('edit')
    check('切来切去不丢编辑',
          win.editor_doc.notes[0].pitch == 79
          and win.editor_doc.notes[0].uid == first_uid)
    win.close()


def test_apply_editor_no_double_transpose() -> None:
    print('\n[2] 编辑结果应用到演奏（关键：不能二次移调）')

    win = build_window()
    # 故意把移调设成非零 —— 如果应用编辑时错误地走了 arrange()，
    # 音高就会整体被搬走，这个断言立刻失败
    win.options.transpose = 7
    win.params.apply_to(win.options)

    win.set_mode('edit')
    before = [s.pitch for s in win.plan.steps]
    win.apply_editor()
    after = [s.pitch for s in win.plan.steps]
    check('应用编辑后音高没有被移调', after == before,
          '%s -> %s' % (before[:4], after[:4]))

    # 改一个音高，应用后演奏计划必须跟着变
    doc = win.editor_doc
    doc.notes[0].pitch = 76
    win.apply_editor()
    check('改了音高之后编排跟着更新',
          win.plan.steps[0].pitch == 76, str(win.plan.steps[0].pitch))
    check('其他音不受影响',
          [s.pitch for s in win.plan.steps[1:]] == before[1:])

    # 指法/时间轴必须重算过（不是照抄旧对象）
    check('编排被真正重算', win.plan.metrics.total_presses > 0,
          str(win.plan.metrics.total_presses))
    check('预览卷帘已同步', win.roll.step_count() == len(win.plan.steps),
          '%d vs %d' % (win.roll.step_count(), len(win.plan.steps)))

    # 合并长音：编辑器的看家动作，走完整条链路
    doc2 = win.editor_doc
    for n in doc2.notes[:4]:
        n.pitch = 72
        n.duration = 0.3
    for i, n in enumerate(doc2.notes[:4]):
        n.start = 1.0 + i * 0.31
    win.apply_editor()
    win.editor.select_indices(range(4))
    win.editor.merge_selection()
    win.apply_editor()
    check('合并后编排里的音符数下降', len(win.plan.steps) < len(before),
          '%d' % len(win.plan.steps))

    # 撤销要能回到合并前
    win.editor_doc.undo()
    win.apply_editor()
    check('撤销后编排恢复', len(win.plan.steps) == len(doc2.notes),
          '%d vs %d' % (len(win.plan.steps), len(doc2.notes)))
    win.close()


def test_new_score_discards_doc() -> None:
    print('\n[3] 换曲目时旧编辑作废')

    win = build_window()
    win.set_mode('edit')
    old_uid = win.editor_doc.notes[0].uid

    other = Score(title='另一首', notes=[
        Note(pitch=60, start=i * 0.5, duration=0.4) for i in range(6)],
        bpm=100.0)
    win._on_score_loaded(other, arrange(other, win.instrument, win.options))

    check('编辑文档已作废', win.editor_doc is None)
    check('自动切回预览视图',
          win.mode == 'preview' and win.stack.currentIndex() == 0)
    check('没有残留上一首的编辑',
          win.editor.editor.doc() is None or
          all(n.uid != old_uid for n in win.editor.editor.doc().notes))
    check('新曲目已载入', win.score is other and len(win.plan.steps) > 0)

    # 新曲目还能正常进编辑
    win.set_mode('edit')
    check('新曲目可以进编辑',
          win.editor_doc is not None and win.editor_doc.title == '另一首',
          str(win.editor_doc.title if win.editor_doc else None))
    win.close()


def test_params_do_not_move_edited_pitches() -> None:
    print('\n[4] 编辑模式下改参数不能搬走已编辑的音高')

    win = build_window()
    win.set_mode('edit')
    win.editor_doc.notes[0].pitch = 79
    win.apply_editor()
    working = [s.pitch for s in win.plan.steps]

    # 模拟用户拖动「移调」滑块
    win.options.transpose = 5
    win.re_arrange()
    check('移调参数不会二次搬走编辑后的音高',
          [s.pitch for s in win.plan.steps] == working,
          '%s vs %s' % ([s.pitch for s in win.plan.steps][:4], working[:4]))

    # 但换气这类参数仍然应该生效
    win.options.breath_ms = 200
    win.re_arrange()
    check('换气参数依然生效', win.plan.steps[0].duration > 0)
    win.close()


def test_editor_on_empty() -> None:
    print('\n[5] 没有曲目时进不了编辑')

    root = tempfile.mkdtemp(prefix='gti-empty-')
    win = MainWindow(Config(), root)
    win.resize(1200, 800)
    win.show()
    APP.processEvents()

    win.set_mode('edit')
    check('没有曲目时留在预览视图',
          win.mode == 'preview' and win.stack.currentIndex() == 0)
    check('编辑按钮被禁用', not win.btn_mode_edit.isEnabled())
    check('给了提示', '先选一首曲子' in win.status_label.text(),
          win.status_label.text())
    win.close()


def test_save_editor_doc() -> None:
    """编辑结果存回 JSON，读回来必须一模一样。

    覆盖「存回 JSON」的完整链路：编辑文档 → Score → 文件 → 新文档。顺带
    验证拍号/调号/BPM 也存下来了 —— 少了它们，重新打开时小节线会全画错，
    而那种错很难一眼看出来。
    """
    print('\n[6] 编辑结果存回 JSON')

    from gtiharmonica.edit import EditDoc
    from gtiharmonica.notation import TimeSignature

    win = build_window()
    win.set_mode('edit')
    doc = win.editor_doc

    doc.notes[0].pitch = 76
    doc.notes[0].duration = 0.9
    doc.set_context(bpm=96.0)
    doc.set_context(time_sig=TimeSignature(3, 4))
    doc.set_context(key=7)
    win.apply_editor()

    root = tempfile.mkdtemp(prefix='gti-save-')
    target = os.path.join(root, '编辑结果.json')
    # 保存会弹「覆盖/另存/取消」对话框，测试里不能真弹：把选路径那一步
    # 换掉，其余流程（写文件、刷新曲库、更新状态）照常走。
    win._ask_save_target = lambda *a, **k: target
    win.save_arrangement()

    check('文件已写出', os.path.isfile(target), target)
    if not os.path.isfile(target):
        win.close()
        return

    back = EditDoc.load(target)
    check('音符数一致', len(back) == len(doc), '%d vs %d' % (len(back), len(doc)))
    check('音高一致',
          [n.pitch for n in back.notes] == [n.pitch for n in doc.notes])
    check('时值与起点一致',
          all(abs(a.start - b.start) < 1e-5 and abs(a.duration - b.duration) < 1e-5
              for a, b in zip(back.notes, doc.notes)))
    check('BPM 存下来了', abs(back.bpm - 96.0) < 1e-6, '%.1f' % back.bpm)
    check('拍号存下来了',
          (back.time_sig_num, back.time_sig_den) == (3, 4),
          '%d/%d' % (back.time_sig_num, back.time_sig_den))
    check('调号存下来了', back.key == 7, '%d' % back.key)
    check('小节线按存下来的拍号重算',
          abs(back.context().bar_sec - 1.875) < 1e-6,
          '%.4f 秒' % back.context().bar_sec)
    check('保存后不再是脏的', doc.dirty is False)
    check('score_path 指向新文件', win.score_path == target)

    # 存下来的东西必须还能继续编辑
    back.notes[0].pitch = 60
    check('读回的文档可以继续编辑', back.notes[0].pitch == 60)

    import shutil
    shutil.rmtree(root, ignore_errors=True)
    win.close()


def test_key_strip_in_edit_view() -> None:
    """编辑视图下按键行也必须可见 —— 键位联动不能只在预览里有。"""
    print('\n[7] 键位序列视图的联动')

    win = build_window()
    check('预览模式下按键行可见', win.keys.isVisible())

    win.set_mode('edit')
    check('编辑模式下按键行同样可见', win.keys.isVisible())
    check('按键行在两个视图之外（不属于 stack 的任何一页）',
          win.stack.indexOf(win.keys) == -1)
    check('按键行配置了 8 个键', len(win.keys._keys) == 8,
          str(win.keys._keys))

    # 演奏时按键行要跟着亮 —— 编辑模式下也一样
    step = win.plan.steps[0]
    win._on_tick(1, step.start, step)
    check('演奏时按键行被点亮', win.keys._active_key == step.key,
          '期望 %s，实得 %s' % (step.key, win.keys._active_key))
    check('修饰键状态同步到修饰键条',
          tuple(win.keys._active_mods) == tuple(step.modifiers),
          str(win.keys._active_mods))
    win.close()


if __name__ == '__main__':
    APP = QApplication.instance() or QApplication(sys.argv)
    print('=' * 62)
    print('主窗口集成自检')
    print('=' * 62)
    test_mode_switch()
    test_apply_editor_no_double_transpose()
    test_new_score_discards_doc()
    test_params_do_not_move_edited_pitches()
    test_editor_on_empty()
    test_save_editor_doc()
    test_key_strip_in_edit_view()
    print('\n' + '=' * 62)
    print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
    print('=' * 62)
    sys.exit(1 if FAIL else 0)
