"""乐谱层自检：拍号、小节、时值、延音线。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gtiharmonica.notation import (                          # noqa: E402
    NotationContext, TimeSignature, apply_context, bar_lines,
    context_from_score, duration_symbol, infer_time_signature, split_ties,
    summarize,
)
from gtiharmonica.score import Note, Score                   # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=''):
    (OK if cond else BAD).append(name)
    print('  [%s] %s%s' % ('OK  ' if cond else 'FAIL', name,
                           ('  -> %s' % extra) if extra else ''))


def main():
    ctx = NotationContext(bpm=120)
    print('=== 基本换算 ===')
    check('120BPM 四分音符 = 0.5s', abs(ctx.beat_sec - 0.5) < 1e-9,
          '%.3f' % ctx.beat_sec)
    check('4/4 小节 = 2.0s', abs(ctx.bar_sec - 2.0) < 1e-9, '%.3f' % ctx.bar_sec)
    ctx68 = NotationContext(bpm=120, time_signature=TimeSignature(6, 8))
    check('6/8 小节 = 1.5s', abs(ctx68.bar_sec - 1.5) < 1e-9, '%.3f' % ctx68.bar_sec)

    print('\n=== 时值符号 ===')
    for beats, want in ((1.0, '四分音符'), (0.5, '八分音符'), (2.0, '二分音符'),
                        (4.0, '全音符'), (1.5, '附点四分音符'),
                        (0.25, '十六分音符'), (0.75, '附点八分音符')):
        got = duration_symbol(beats)
        check('%.2f 拍 -> %s' % (beats, want), got == want, got)

    print('\n=== 小节定位 ===')
    check('bar_index(0.5) = 0', ctx.bar_index(0.5) == 0)
    check('bar_index(1.9) = 0', ctx.bar_index(1.9) == 0)
    check('bar_index(2.5) = 1', ctx.bar_index(2.5) == 1)
    check('bar_bounds(1) = (2.0, 4.0)',
          ctx.bar_bounds(1) == (2.0, 4.0), str(ctx.bar_bounds(1)))
    check('bar_beat(2.5) 第二小节第 1 拍', ctx.bar_beat(2.5)[:2] == (1, 1),
          str(ctx.bar_beat(2.5)))
    check('bar_beat(3.0) 第二小节第 2 拍', ctx.bar_beat(3.0)[:2] == (1, 2),
          str(ctx.bar_beat(3.0)))
    lines = bar_lines(ctx, 5.0)
    check('小节线从 0 起每 2 秒一根', lines[:4] == [0.0, 2.0, 4.0, 6.0],
          str(lines[:4]))

    print('\n=== 延音线切分 ===')
    # 0.5 -> 5.5，跨过 2.0 和 4.0 两根小节线
    n = Note(60, 0.5, 5.0)
    pieces = split_ties([n], ctx)
    check('跨 2 根小节线切成 3 段', len(pieces) == 3, str(len(pieces)))
    check('时长 1.5 / 2.0 / 1.5',
          [round(p.duration, 6) for p in pieces] == [1.5, 2.0, 1.5],
          str([round(p.duration, 4) for p in pieces]))
    check('首段不是延音、后两段是',
          [p.tie for p in pieces] == [False, True, True],
          str([p.tie for p in pieces]))
    check('总时长守恒',
          abs(sum(p.duration for p in pieces) - 5.0) < 1e-9)
    check('音高全部保留', all(p.pitch == 60 for p in pieces))
    short = Note(62, 0.5, 1.0)             # 0.5 -> 1.5，不跨线
    check('不跨小节的音原样返回', split_ties([short], ctx)[0] is short)
    long_note = Note(64, 0.0, 9.0)
    check('跨 4 根线的音切 5 段', len(split_ties([long_note], ctx)) == 5,
          str(len(split_ties([long_note], ctx))))

    print('\n=== 拍号推断 ===')
    def notes_on(grid_times):
        return [Note(60, t, 0.1) for t in grid_times]
    # 强拍（每 2 秒 = 每小节头）上密，次强拍（每 1 秒）上稀 -> 4/4
    t44 = []
    for bar in range(8):
        base = bar * 2.0
        t44 += [base, base + 0.5, base + 1.0, base + 1.5]
        t44 += [base, base]                     # 小节头重复，加重强拍
    got = infer_time_signature(notes_on(t44), ctx)
    check('4/4 素材推断为 4/4', got.numerator == 4 and got.denominator == 4,
          str(got))
    # 3/4：每 1.5 秒一个小节，强拍上音符密集（3 个）、第二拍单个。
    # 刻意让第三拍空着：1.5 和 2.0 是 3:4 的关系，3/4 的音符在 4/4 网格上
    # 会周期性命中（强拍和中点交替），结构不够分明时**本质上无法区分**。
    # 这正是 infer_time_signature 在证据不足时宁可回退默认 4/4 的原因。
    t34 = []
    for bar in range(12):
        base = bar * 1.5
        t34 += [base, base, base, base + 0.5]
    got34 = infer_time_signature(notes_on(t34), ctx)
    check('3/4 素材推断为 3/4', str(got34) == '3/4', str(got34))
    check('音符太少时保守返回 4/4',
          str(infer_time_signature(notes_on([0.0, 0.5]), ctx)) == '4/4')

    print('\n=== 与 Score 互转 ===')
    sc = Score(title='t', notes=[Note(60, 0.0, 1.0)])
    sc.bpm = 96.0
    sc.time_sig_num = 3
    sc.time_sig_den = 4
    sc.phase = 0.25
    sc.key = 2
    sc.scale = 'minor'
    back = context_from_score(sc)
    check('读回 bpm', abs(back.bpm - 96.0) < 1e-9, '%.1f' % back.bpm)
    check('读回拍号', str(back.time_signature) == '3/4', str(back.time_signature))
    check('读回 phase', abs(back.phase - 0.25) < 1e-9)
    sc2 = Score(title='t2')
    apply_context(sc2, back)
    check('写回一致',
          (sc2.bpm, sc2.time_sig_num, sc2.time_sig_den, sc2.key, sc2.scale)
          == (96.0, 3, 4, 2, 'minor'))
    info = summarize([Note(60, 0.0, 1.0), Note(62, 4.0, 1.0)], ctx)
    check('统计小节数', info['bars'] == 3, str(info))

    print()
    print('-' * 60)
    print('通过 %d 项，失败 %d 项' % (len(OK), len(BAD)))
    for name in BAD:
        print('  FAIL: %s' % name)
    return 1 if BAD else 0


if __name__ == '__main__':
    raise SystemExit(main())
