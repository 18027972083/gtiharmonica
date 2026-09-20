"""诊断：有伴奏时显著性面板把票投给了谁。

对「旋律 + 低音 + 和弦」的混合信号，逐帧打印 top 候选，
看算法是否把低音线或和弦当成了旋律。
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gtiharmonica import dsp                                    # noqa: E402
from gtiharmonica.instrument import note_name                   # noqa: E402
from gtiharmonica.transcribe import (PitchGrid, _harmonic_salience,  # noqa: E402
                                     _whiten)
from eval_transcribe import render_with_accompaniment            # noqa: E402

MELODY = [60, 60, 67, 67, 69, 69, 67, 65, 65, 64, 64, 62, 62, 60]
SR = 22050
FRAME = 2048
HOP = 256
FRAME_RATE = SR / HOP


def analyse(audio, label, frames_to_show=6):
    x = dsp.resample(audio.samples, audio.samplerate, SR)
    mag = dsp.stft_mag(x, frame=FRAME, hop=HOP)
    white = _whiten(mag)
    grid = PitchGrid(98.0, 1200.0, 0.6)
    sal = _harmonic_salience(white, grid, SR, FRAME, 12)

    print()
    print('=' * 78)
    print(label)
    print('=' * 78)

    # 每个旋律音取它中间那一帧
    dur = 0.35
    shown = 0
    for i, want in enumerate(MELODY):
        t = i * dur + dur * 0.5
        f = int(round(t * FRAME_RATE))
        if f >= sal.shape[0]:
            continue
        row = sal[f]
        order = np.argsort(row)[::-1][:5]
        top = [(float(grid.midi[j]), float(grid.freq[j]), float(row[j])) for j in order]
        want_midi = want
        # 找到期望音高所在格的排名
        wbin = int(round(want_midi * PitchGrid.RESOLUTION)) - grid.lo
        rank = int(np.sum(row > row[wbin])) + 1
        cand = ' | '.join('%s(%.0fHz,%.2f)' % (note_name(int(round(m))), fq, s)
                          for m, fq, s in top)
        print('  期望 %-4s 排名%2d  top5: %s'
              % (note_name(want_midi), rank, cand))
        shown += 1
        if shown >= frames_to_show:
            break

    # 整体：算法选出的音高 vs 期望
    path = np.argmax(sal, axis=1)
    voiced = np.ones(sal.shape[0], dtype=bool)
    chosen = grid.midi[path]
    print('  --- 逐帧 argmax（未经 Viterbi）---')
    for i, want in enumerate(MELODY[:8]):
        t = i * dur + dur * 0.5
        f = int(round(t * FRAME_RATE))
        if f < chosen.size:
            print('    #%d 期望 %-4s -> argmax %-4s'
                  % (i, note_name(want), note_name(int(round(chosen[f])))))


def main():
    analyse(render_with_accompaniment(MELODY, 0.35, accomp=0.0), 'A. 仅旋律')
    analyse(render_with_accompaniment(MELODY, 0.35, accomp=0.45), 'B. 轻伴奏（含低音/和弦/鼓）')


if __name__ == '__main__':
    main()
