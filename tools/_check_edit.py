"""编辑器数据层自检：编辑命令、撤销、吸附、碎片合并、往返。

重点在两条容易写错、写错了又很难看出来的地方：

  1. 二次移调。编辑器里的音高已经是最终音高，如果应用编辑时又跑一遍
     arrange()（它会做移调 + 八度折叠），音就全错了。所以编辑结果必须走
     arrange_from_notes()。这里用「反向参数」把它钉死：故意把 transpose
     设成非零，然后断言音高不变。

  2. 撤销的完整覆盖。任何一个写操作漏了记快照，用户就会遇到
     「改了但撤不回来」。这里逐个操作检查撤销栈增长。

运行：python tools/_check_edit.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gtiharmonica.arrange import Options, arrange, arrange_from_notes  # noqa: E402
from gtiharmonica.edit import (DEFAULT_MERGE_GAP, EditDoc, fragment_report,  # noqa: E402
                               merge_fragments, nearest_in_scale,
                               scale_pitch_classes)
from gtiharmonica.instrument import Instrument  # noqa: E402
from gtiharmonica.score import Note, Score  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = '') -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print('  ok   %s%s' % (name, ('  [%s]' % detail) if detail else ''))
    else:
        FAIL += 1
        print('  FAIL %s%s' % (name, ('  [%s]' % detail) if detail else ''))
    return ok


def near(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def fragments(pitch: int, count: int, dur: float = 0.25,
              gap: float = 0.01, t0: float = 1.0):
    """造一串首尾相接的碎片 —— 转录把长音切碎的典型形态。"""
    out = []
    t = t0
    for _ in range(count):
        out.append(Note(pitch=pitch, start=round(t, 6), duration=dur))
        t += dur + gap
    return out


def doc_with(notes, **kw) -> EditDoc:
    return EditDoc(notes=notes, title='测试', bpm=120.0, **kw)


# ---------------------------------------------------------------------------

def test_merge_fragments() -> None:
    print('\n[1] 碎片合并 —— 长音问题的兜底手段')

    notes = fragments(72, 12)                       # 12 × 0.25s + 11 × 0.01s
    doc = doc_with(notes)
    span_before = doc.duration
    idx = merge_fragments(doc)

    check('12 个碎片并成 1 个音', len(doc.notes) == 1, 'notes=%d' % len(doc.notes))
    if doc.notes:
        n = doc.notes[0]
        check('合并后音高不变', n.pitch == 72, 'pitch=%d' % n.pitch)
        check('起点取最早', near(n.start, 1.0, 1e-6), 'start=%.4f' % n.start)
        check('总时长吃掉缝隙', near(n.duration, span_before - 1.0, 1e-4),
              'dur=%.4f 期望=%.4f' % (n.duration, span_before - 1.0))
        check('不再是延音片段', n.tie is False)
    check('返回的下标有效', idx == [0], str(idx))

    # 不同音高不能被并起来
    mixed = [Note(pitch=72, start=0.0, duration=0.25),
             Note(pitch=74, start=0.25, duration=0.25),
             Note(pitch=72, start=0.50, duration=0.25)]
    d2 = doc_with(mixed)
    merge_fragments(d2)
    check('不同音高不误并', len(d2.notes) == 3, 'notes=%d' % len(d2.notes))

    # 间隔太大不能并（那是两个真正分开的音）
    apart = [Note(pitch=72, start=0.0, duration=0.2),
             Note(pitch=72, start=1.5, duration=0.2)]
    d3 = doc_with(apart)
    merge_fragments(d3)
    check('间隔超过阈值不并', len(d3.notes) == 2, 'notes=%d' % len(d3.notes))

    # 同音高、两段（中间被别的音隔开）
    two_runs = fragments(72, 3) + fragments(72, 3, t0=5.0)
    d4 = doc_with(two_runs)
    merge_fragments(d4)
    check('两段各自合并', len(d4.notes) == 2, 'notes=%d' % len(d4.notes))

    # by_pitch=False：整段并成一个音（音高取占时间最长者）
    d5 = doc_with([Note(pitch=72, start=0.0, duration=0.1),
                   Note(pitch=74, start=0.1, duration=0.9),
                   Note(pitch=71, start=1.0, duration=0.1)])
    d5.merge(range(3), by_pitch=False)
    check('by_pitch=False 并成一个', len(d5.notes) == 1, 'notes=%d' % len(d5.notes))
    check('音高取占时间最长者',
          d5.notes and d5.notes[0].pitch == 74,
          'pitch=%d' % (d5.notes[0].pitch if d5.notes else -1))

    # 只合并选中范围内的
    d6 = doc_with(fragments(72, 4) + fragments(64, 4, t0=5.0))
    merge_fragments(d6, indices=[0, 1, 2, 3])
    check('只处理选中的部分', len(d6.notes) == 5, 'notes=%d' % len(d6.notes))


def test_fragment_report() -> None:
    print('\n[2] 碎片统计（界面据此提示值不值得点）')

    doc = doc_with(fragments(72, 8) + fragments(76, 3, t0=5.0))
    rep = fragment_report(doc)
    check('报告出 2 组', rep['groups'] == 2, str(rep))
    check('报告可减少 7+2 个音符', rep['extra'] == 9, str(rep))

    clean = doc_with([Note(pitch=60, start=0, duration=1.0),
                      Note(pitch=62, start=1.5, duration=1.0)])
    check('干净的曲子报告 0 组', fragment_report(clean)['groups'] == 0)


def test_undo_redo() -> None:
    print('\n[3] 撤销 / 重做')

    doc = doc_with([Note(pitch=60, start=1.0, duration=0.5)])
    check('初始不能撤销', not doc.can_undo)

    doc.move([0], 0.5, 2)
    check('移动后可以撤销', doc.can_undo)
    check('移动生效', near(doc.notes[0].start, 1.5) and doc.notes[0].pitch == 62)

    doc.undo()
    check('撤销恢复时间', near(doc.notes[0].start, 1.0), '%.3f' % doc.notes[0].start)
    check('撤销恢复音高', doc.notes[0].pitch == 60)
    check('撤销后可以重做', doc.can_redo)

    doc.redo()
    check('重做恢复移动',
          near(doc.notes[0].start, 1.5) and doc.notes[0].pitch == 62)

    # 每一个写操作都要能撤销。有两项需要一个「确实会改动」的起点数据，
    # 否则操作本身是合法的 no-op，测不出快照有没有记。
    fallback = lambda: doc_with(fragments(72, 6)).notes  # noqa: E731
    ops = [
        ('move', lambda d: d.move([0], 0.1), None),
        ('resize', lambda d: d.resize([0], 0.2), None),
        ('set_duration', lambda d: d.set_duration([0], 0.9), None),
        ('set_pitch', lambda d: d.set_pitch([0], 70), None),
        ('transpose', lambda d: d.transpose([0], 1), None),
        ('insert', lambda d: d.insert(64, 3.0), None),
        ('delete', lambda d: d.delete([0]), None),
        ('split', lambda d: d.split(0, d.notes[0].start + 0.2), None),
        ('merge', lambda d: d.merge(range(len(d.notes))), None),
        ('quantize', lambda d: d.quantize(range(len(d.notes)), 0.25), None),
        # 调外音才有得吸（72 是 C5，C 大调里本来就在调内）
        ('snap_to_scale', lambda d: d.snap_to_scale(range(len(d.notes))),
         lambda: [Note(pitch=73, start=0.0, duration=0.3)]),
        # 首尾相接不算重叠，得造真正的重叠
        ('fix_overlaps', lambda d: d.fix_overlaps(),
         lambda: [Note(pitch=72, start=0.0, duration=0.5),
                  Note(pitch=72, start=0.3, duration=0.2)]),
        ('set_context', lambda d: d.set_context(bpm=90), None),
    ]
    for name, fn, maker in ops:
        d = doc_with(maker() if maker else fallback())
        before = [(n.pitch, round(n.start, 6), round(n.duration, 6))
                  for n in d.notes]
        fn(d)
        recorded = d.can_undo
        changed = ([(n.pitch, round(n.start, 6), round(n.duration, 6))
                    for n in d.notes] != before) or d.bpm != 120.0
        d.undo()
        restored = [(n.pitch, round(n.start, 6), round(n.duration, 6))
                    for n in d.notes] == before
        check('%s 记了快照且撤销能还原' % name, recorded and changed and restored,
              'recorded=%s changed=%s restored=%s'
              % (recorded, changed, restored))


def test_move_and_snap() -> None:
    print('\n[4] 平移与节拍吸附')

    # 120 BPM -> 一拍 0.5s，1/4 拍网格 = 0.125s
    doc = doc_with([Note(pitch=60, start=1.03, duration=0.4)])
    doc.move([0], 0.01, 0, snap=0.25)
    check('吸附到 1/16 拍网格', near(doc.notes[0].start, 1.0, 1e-6),
          '%.4f' % doc.notes[0].start)

    # 整组平移不能把音符推到负时间
    doc2 = doc_with([Note(pitch=60, start=0.1, duration=0.2),
                     Note(pitch=64, start=0.5, duration=0.2)])
    doc2.move([0, 1], -1.0)
    check('不会推到负时间', min(n.start for n in doc2.notes) >= 0.0,
          '%.4f' % min(n.start for n in doc2.notes))
    check('相对位置保持不变',
          near(doc2.notes[1].start - doc2.notes[0].start, 0.4, 1e-6))

    # 音高上下限
    doc3 = doc_with([Note(pitch=126, start=0.0, duration=0.2)])
    doc3.transpose([0], 10)
    check('音高上限钳到 127', doc3.notes[0].pitch == 127,
          str(doc3.notes[0].pitch))


def test_resize_edges() -> None:
    print('\n[5] 拖动边缘改时值')

    doc = doc_with([Note(pitch=60, start=1.0, duration=0.5)])
    doc.resize([0], 0.25, edge='right')
    check('拖右边缘只改终点',
          near(doc.notes[0].start, 1.0) and near(doc.notes[0].duration, 0.75),
          '%.3f+%.3f' % (doc.notes[0].start, doc.notes[0].duration))

    doc2 = doc_with([Note(pitch=60, start=1.0, duration=0.5)])
    doc2.resize([0], -0.25, edge='left')
    n = doc2.notes[0]
    check('拖左边缘终点钉死',
          near(n.start, 0.75) and near(n.end, 1.5),
          '%.3f+%.3f -> end=%.3f' % (n.start, n.duration, n.end))

    # 拖到反转也不会变成负时值
    doc3 = doc_with([Note(pitch=60, start=1.0, duration=0.5)])
    doc3.resize([0], -5.0, edge='right')
    check('时值不会塌成负的', doc3.notes[0].duration > 0,
          '%.4f' % doc3.notes[0].duration)


def test_insert_delete_split() -> None:
    print('\n[6] 增 / 删 / 切')

    doc = doc_with([])
    i = doc.insert(64, 2.0, 0.5)
    check('插入成功', len(doc.notes) == 1 and i == 0, 'i=%d' % i)

    idx = doc.split(0, 2.2)
    check('切成两个', len(doc.notes) == 2, 'notes=%d' % len(doc.notes))
    if len(doc.notes) == 2:
        a, b = doc.notes
        check('切点位置正确', near(a.end, 2.2) and near(b.start, 2.2),
              '%.3f|%.3f' % (a.end, b.start))
        check('总时长不变', near(a.duration + b.duration, 0.5, 1e-6))
        check('后半段标了延音', b.tie is True and a.tie is False)

    # 切点太靠边不切
    before = len(doc.notes)
    doc.split(0, doc.notes[0].start + 0.001)
    check('切点太靠边拒绝', len(doc.notes) == before)

    removed = doc.delete([0, 1])
    check('删除返回数量', removed == 2 and len(doc.notes) == 0,
          'removed=%d' % removed)


def test_quantize_and_scale() -> None:
    print('\n[7] 量化与调内吸附')

    notes = [Note(pitch=61, start=1.03, duration=0.27),
             Note(pitch=63, start=1.52, duration=0.51)]
    doc = doc_with(notes, key=0, scale='major')
    doc.quantize([0, 1], grid=0.25, durations=True)
    starts = sorted(round(n.start, 6) for n in doc.notes)
    durs = sorted(round(n.duration, 6) for n in doc.notes)
    check('起点对齐网格', starts == [1.0, 1.5], str(starts))
    check('时值对齐网格', durs == [0.25, 0.5], str(durs))

    doc2 = doc_with([Note(pitch=61, start=0, duration=0.3),
                     Note(pitch=66, start=1.0, duration=0.3)], key=0,
                    scale='major')
    doc2.snap_to_scale([0, 1])
    pitches = sorted(n.pitch for n in doc2.notes)
    check('C#4 -> C4（调内最近）', pitches[0] == 60, str(pitches))
    check('F#4 -> F4 或 G4（等距取低）', pitches[1] == 65, str(pitches))

    doc3 = doc_with([Note(pitch=61, start=0, duration=0.3)], scale='chromatic')
    doc3.snap_to_scale([0])
    check('半音阶模式不吸附', doc3.notes[0].pitch == 61)

    check('大调音级正确',
          scale_pitch_classes(0, 'major') == frozenset({0, 2, 4, 5, 7, 9, 11}))
    check('小调音级正确',
          scale_pitch_classes(9, 'minor') == frozenset({9, 11, 0, 2, 4, 5, 7}))
    check('最近调内音钳在 0..127', 0 <= nearest_in_scale(127, 0, 'major') <= 127)


def test_selection_and_hit() -> None:
    print('\n[8] 框选与命中')

    doc = doc_with([Note(pitch=60, start=0.0, duration=0.5),
                    Note(pitch=64, start=0.6, duration=0.5),
                    Note(pitch=67, start=1.2, duration=0.5)])

    idx = doc.notes_in_rect(0.0, 1.0, 60, 64)
    check('框选相交的音符', idx == [0, 1], str(idx))
    check('框选不含范围外的', 2 not in idx)

    rev = doc.notes_in_rect(1.0, 0.0, 64, 60)      # 反向拖动也要能用
    check('反向框选等价', rev == idx, str(rev))

    check('命中时间与音高', doc.hit(0.25, 60) == 0)
    check('音高不符则不命中', doc.hit(0.25, 61) == -1)
    check('右边缘命中', doc.hit(0.5, 60, edge='right') == 0)
    check('右边缘之外不命中', doc.hit(0.2, 60, edge='right') == -1)


def test_overlaps() -> None:
    print('\n[9] 同音高重叠清理')

    doc = doc_with([Note(pitch=60, start=0.0, duration=0.8),
                    Note(pitch=60, start=0.3, duration=0.2),
                    Note(pitch=64, start=5.0, duration=0.2)])
    touched = doc.fix_overlaps()
    check('检出重叠', touched == 1, 'touched=%d' % touched)
    n60 = sorted((n for n in doc.notes if n.pitch == 60), key=lambda n: n.start)
    # trim 策略是把前一个音裁到后一个音的起点，两个音都留着
    check('前一个音被裁到后一个的起点',
          len(n60) == 2 and near(n60[0].duration, 0.3, 1e-6),
          '%d 个，首个时长 %.3f' % (len(n60), n60[0].duration if n60 else -1))
    check('裁过之后不再重叠',
          len(n60) < 2 or n60[0].end <= n60[1].start + 1e-9)


def test_roundtrip() -> None:
    print('\n[10] 存盘 / 读回往返')

    doc = doc_with(fragments(72, 5), phase=0.25, key=7, scale='minor',
                   time_sig_num=3)
    doc.set_context(bpm=96.0)
    merge_fragments(doc)
    doc.insert(60, 4.0, 0.5)

    root = tempfile.mkdtemp(prefix='gti-edit-')
    try:
        path = os.path.join(root, '测试曲.json')
        doc.save(path)
        check('保存后不再是脏的', doc.dirty is False)

        back = EditDoc.load(path)
        check('音符数一致', len(back) == len(doc),
              '%d vs %d' % (len(back), len(doc)))
        check('音高一致',
              [n.pitch for n in back.notes] == [n.pitch for n in doc.notes])
        check('时间一致',
              all(near(a.start, b.start, 1e-6) and near(a.duration, b.duration, 1e-6)
                  for a, b in zip(back.notes, doc.notes)))
        check('BPM 往返', near(back.bpm, 96.0), '%.1f' % back.bpm)
        check('拍号往返', back.time_sig_num == 3 and back.time_sig_den == 4,
              '%d/%d' % (back.time_sig_num, back.time_sig_den))
        check('调号往返', back.key == 7 and back.scale == 'minor',
              '%d %s' % (back.key, back.scale))
        check('phase 往返', near(back.phase, 0.25, 1e-6), '%.4f' % back.phase)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_arrange_no_double_transpose() -> None:
    print('\n[11] 编辑结果不能二次移调（关键回归点）')

    inst = Instrument()
    lo, hi = inst.range() if hasattr(inst, 'range') else (48, 84)
    table = inst.fingerings()
    playable = sorted(table)[:3]
    check('乐器有可演奏音高', len(playable) >= 3, str(playable))

    notes = [Note(pitch=p, start=i * 0.5, duration=0.4)
             for i, p in enumerate(playable)]

    # 故意把 transpose 设成非零：走 arrange() 会被移调，走
    # arrange_from_notes() 必须原样不动
    opts = Options(transpose=5)
    plan = arrange_from_notes(notes, inst, opts, title='t')

    got = [s.pitch for s in plan.steps]
    check('arrange_from_notes 保持音高不变', got == playable,
          '%s vs %s' % (got, playable))
    check('指法都选出来了', all(s.fingering is not None for s in plan.steps))

    # 反证：同一批音符喂给 arrange() 就会被移调 —— 说明这个测试有效
    score = Score(title='t', notes=[Note(pitch=p, start=i * 0.5, duration=0.4)
                                    for i, p in enumerate(playable)])
    moved = [s.pitch for s in arrange(score, inst, opts).steps]
    check('（反证）arrange() 确实会移调', moved != playable,
          '%s vs %s' % (moved, playable))

    # 音域外的音符被丢弃而不是被折叠
    bad = notes + [Note(pitch=200, start=3.0, duration=0.3)]
    plan2 = arrange_from_notes(bad, inst, Options(), title='t')
    check('音域外音符被丢弃', plan2.stats.get('dropped') == 1,
          str(plan2.stats.get('dropped')))
    check('丢弃后其余音高不变',
          [s.pitch for s in plan2.steps] == playable)

    # 指标 = 实际按键动作数（含修饰键），一定不少于音符数
    check('指标已重算', plan.metrics.total_presses >= len(playable),
          'presses=%d notes=%d' % (plan.metrics.total_presses, len(playable)))


def test_tie_split() -> None:
    print('\n[12] 乐谱层：跨小节切延音')

    # 120 BPM 4/4 -> 一小节 2.0 秒
    doc = doc_with([Note(pitch=72, start=0.5, duration=3.0)])
    pieces = doc.tie_split()
    check('3 秒的音跨小节被切开', len(pieces) >= 2, '%d 段' % len(pieces))
    check('总时长不丢',
          near(sum(p.duration for p in pieces), 3.0, 1e-6),
          '%.4f' % sum(p.duration for p in pieces))
    check('只有第一段不是延音',
          pieces[0].tie is False and all(p.tie for p in pieces[1:]))

    check('小节线按拍号铺开',
          [round(t, 3) for t in doc.bar_lines(4.0)] == [0.0, 2.0, 4.0],
          str(doc.bar_lines(4.0)))

    doc2 = doc_with([Note(pitch=72, start=0.5, duration=3.0)],
                    time_sig_num=3)
    check('3/4 小节长 1.5 秒',
          near(doc2.context().bar_sec, 1.5), '%.3f' % doc2.context().bar_sec)

    check('时值名称正确',
          doc.duration_name(Note(pitch=60, start=0, duration=0.5)) == '四分音符',
          doc.duration_name(Note(pitch=60, start=0, duration=0.5)))


if __name__ == '__main__':
    print('=' * 62)
    print('编辑器数据层自检')
    print('=' * 62)
    test_merge_fragments()
    test_fragment_report()
    test_undo_redo()
    test_move_and_snap()
    test_resize_edges()
    test_insert_delete_split()
    test_quantize_and_scale()
    test_selection_and_hit()
    test_overlaps()
    test_roundtrip()
    test_arrange_no_double_transpose()
    test_tie_split()
    print('\n' + '=' * 62)
    print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
    print('=' * 62)
    sys.exit(1 if FAIL else 0)
