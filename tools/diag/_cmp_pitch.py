"""对比新旧「重新起音」判据下的音高序列，定位音高是否被改坏。

修复的初衷是别再把长音切碎，但它同时改变了音符的**分段边界**，
而每个音符的音高取的是该段内的中位数 —— 分段一变，音高就可能跟着变。
这个脚本把两版结果按时间对齐，逐音对比。
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from gtiharmonica.instrument import note_name                 # noqa: E402
from gtiharmonica.transcribe import (                         # noqa: E402
    TranscribeOptions, transcribe_file,
)

PATH = os.environ.get('GTI_AUDIO', r'D:\AI\downloads\deepseek4_audio.mp3')


def run(rm):
    res = transcribe_file(PATH, TranscribeOptions(reattack_min=rm))
    return res


def index_at(notes, t):
    """t 时刻正在响的音（没有就取最近的）。"""
    for n in notes:
        if n.start - 0.005 <= t <= n.start + n.duration + 0.005:
            return n
    return None


def main():
    if not os.path.exists(PATH):
        print('找不到音频：%s' % PATH)
        return 1
    old_res = run(0.0)
    new_res = run(TranscribeOptions().reattack_min)
    old, new = old_res.score.notes, new_res.score.notes
    print('OLD %d 音   NEW %d 音   (音频 %s)'
          % (len(old), len(new), os.path.basename(PATH)))

    # 1) 音级分布：如果某个音级整体消失或暴增，是系统性音高错误
    def hist(notes):
        h = np.zeros(12, dtype=int)
        for n in notes:
            h[n.pitch % 12] += 1
        return h
    ho, hn = hist(old), hist(new)
    names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    print('\n音级分布（% 占比）:')
    print('  %-4s %8s %8s' % ('音级', 'OLD', 'NEW'))
    for i in range(12):
        po = ho[i] / max(1, len(old)) * 100
        pn = hn[i] / max(1, len(new)) * 100
        flag = '  <<<' if abs(po - pn) > 4.0 else ''
        print('  %-4s %7.1f%% %7.1f%%%s' % (names[i], po, pn, flag))

    # 2) 整体音高中心
    print('\n音高统计:')
    for label, ns in (('OLD', old), ('NEW', new)):
        ps = np.array([n.pitch for n in ns], dtype=float)
        ds = np.array([n.duration for n in ns], dtype=float)
        print('  %s  平均音高 %.2f  中位 %.1f  音域 %d..%d  平均时值 %.3fs'
              % (label, ps.mean(), np.median(ps), int(ps.min()), int(ps.max()),
                 ds.mean()))

    # 3) 逐音对齐：以 OLD 的音符起点为锚，看 NEW 在同一时刻是什么音
    print('\n同一时刻的音高对比（差 >= 2 半音才算异常）:')
    bad = []
    for n in old:
        t = n.start + n.duration * 0.5
        m = index_at(new, t)
        if m is None:
            continue
        d = int(m.pitch) - int(n.pitch)
        if abs(d) >= 2:
            bad.append((t, n, m, d))
    print('  异常点 %d / %d' % (len(bad), len(old)))
    for t, n, m, d in bad[:15]:
        print('    t=%7.2f  OLD %-4s -> NEW %-4s  (%+d 半音)'
              % (t, note_name(n.pitch), note_name(m.pitch), d))

    # 4) 前 24 音并排
    print('\n前 24 音:')
    for label, ns in (('OLD', old), ('NEW', new)):
        cells = ['%s@%.2f' % (note_name(n.pitch), n.start) for n in ns[:24]]
        print('  %s  %s' % (label, ' '.join(cells)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
