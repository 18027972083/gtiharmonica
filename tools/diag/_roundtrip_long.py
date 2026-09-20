"""Round-trip 验证：曲谱 -> 渲染成音频 -> 转录回来，看长音是否还在。

这是比合成正弦更硬的测试：用的是**曲库里真实的曲子**（用户实际会碰到
的旋律、音域、节奏），而不是几个孤立的正弦音。

核心指标是**最长音符时长**。修复前，曲谱里一个 2 秒的长音会被伴奏/泛音
变化切成一串 0.2 秒的碎片；修复后应该接近原值。
"""
from __future__ import annotations

import glob
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from gtiharmonica.score import load_json_score                 # noqa: E402
from gtiharmonica.synth import render                          # noqa: E402
from gtiharmonica.transcribe import TranscribeOptions, transcribe_file  # noqa: E402

SONGS = r'D:\AI\gti-harmonica\songs'
TOP_N = 5


def longest(notes):
    return max((n.duration for n in notes), default=0.0)


def long_count(notes, thr=0.8):
    return sum(1 for n in notes if n.duration >= thr)


def main():
    paths = sorted(glob.glob(os.path.join(SONGS, '*.json')))
    if not paths:
        print('曲库为空：%s' % SONGS)
        return 1

    songs = []
    for p in paths:
        try:
            sc = load_json_score(p)
        except Exception:
            continue
        if len(sc.notes) < 8:
            continue
        songs.append((longest(sc.notes), p, sc))
    songs.sort(reverse=True, key=lambda t: t[0])

    print('曲库 %d 首；取长音最长的前 %d 首做 round-trip'
          % (len(songs), TOP_N))
    print()

    total_old = total_new = 0
    for _, path, score in songs[:TOP_N]:
        name = os.path.basename(path)
        notes = [(n.pitch, n.start, n.duration) for n in score.notes]
        wav = render(notes)
        tmp = os.path.join(tempfile.gettempdir(), 'gti-rt.wav')
        with open(tmp, 'wb') as f:
            f.write(wav)

        src_max = longest(score.notes)
        src_long = long_count(score.notes)
        print('%-34s 原曲 %3d 音  最长 %.2fs  长音(>=0.8s) %2d'
              % (name[:32], len(score.notes), src_max, src_long))

        for rm, label in ((0.0, 'OLD'), (0.35, 'NEW')):
            res = transcribe_file(tmp, TranscribeOptions(reattack_min=rm))
            got_max = longest(res.score.notes)
            got_long = long_count(res.score.notes)
            keep = (got_max / src_max * 100.0) if src_max > 0 else 0.0
            print('      %s  转录 %3d 音  最长 %.2fs (%3.0f%%)  长音 %2d'
                  % (label, len(res.score.notes), got_max, keep, got_long))
            if label == 'OLD':
                total_old += got_max
            else:
                total_new += got_max
        print()

    print('-' * 72)
    print('最长音符合计：OLD %.2fs  ->  NEW %.2fs' % (total_old, total_new))
    if total_old > 0:
        print('长音保留改善 %.0f%%' % ((total_new / total_old - 1.0) * 100.0))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
