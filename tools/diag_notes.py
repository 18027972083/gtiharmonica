"""诊断：小星星的识别结果为什么有「时间点缺失」。"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gtiharmonica import transcribe                          # noqa: E402
from gtiharmonica.instrument import note_name                 # noqa: E402
from eval_transcribe import render_melody                     # noqa: E402

MELODY = [60, 60, 67, 67, 69, 69, 67, 65, 65, 64, 64, 62, 62, 60]


def show(dur_each, label, **opts):
    audio = render_melody(MELODY, dur_each=dur_each)
    res = transcribe.transcribe_audio(
        audio,
        transcribe.TranscribeOptions(scale='off', quantize='off',
                                     fold_to_instrument=False, **opts),
        title=label)
    notes = res.score.notes
    print()
    print('=' * 78)
    print('%s   音符时长 %.2fs   识别 %d 个（期望 %d）'
          % (label, dur_each, len(notes), len(MELODY)))
    print('=' * 78)
    print('  idx  期望(时间)          实得(时间)              跨度           状态')
    for i, want in enumerate(MELODY):
        t0 = i * (dur_each + 0.03)
        t1 = t0 + dur_each
        mid = (t0 + t1) / 2
        cover = [n for n in notes if n.start - 0.02 <= mid <= n.start + n.duration + 0.02]
        got = ('%s(%.3f..%.3f)' % (note_name(cover[0].pitch), cover[0].start,
                                   cover[0].start + cover[0].duration)
               if cover else '—— 缺失 ——')
        same = cover and int(cover[0].pitch) % 12 == want % 12
        mark = 'ok' if same else 'XX'
        print('  %2d   %-4s(%.3f..%.3f)   %-24s %s'
              % (i, note_name(want), t0, t1, got, mark))


if __name__ == '__main__':
    show(0.35, 'A. 时长0.35s，先验=0（无先验）', melody_prior=0.0)
    show(0.35, 'B. 时长0.35s，先验=0.8（默认）', melody_prior=0.8)
