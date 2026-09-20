"""真实音频上对比新旧「重新起音」判据。

重点看长音是否终于被保住：统计音符数、最长时长、长音占比。
修复前 293 秒的真实歌曲会被切成一堆等长短音。
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from gtiharmonica.transcribe import TranscribeOptions, transcribe_file  # noqa: E402

PATH = r'D:\AI\downloads\deepseek4_audio.mp3'


def main():
    if not os.path.exists(PATH):
        print('找不到测试音频：%s' % PATH)
        return 1
    for rm, label in ((0.0, 'OLD 关掉新判据'), (0.35, 'NEW 默认')):
        res = transcribe_file(PATH, TranscribeOptions(reattack_min=rm))
        d = np.array([n.duration for n in res.score.notes], dtype=float)
        pos = np.array([n.start for n in res.score.notes], dtype=float)
        gaps = np.diff(pos) if pos.size > 1 else np.zeros(1)
        print('%s  reattack_min=%.2f' % (label, rm))
        print('   音符 %4d 个   总时长 %.1fs   BPM %.1f   转录耗时 %.2fs'
              % (d.size, res.duration, res.bpm, res.timer.get('total', 0.0)))
        print('   切分出 %d 段 -> 清理后保留 %d 个（丢弃 %d）'
              % (res.notes_detected, res.notes_kept,
                 res.notes_detected - res.notes_kept))
        if d.size:
            print('   时长: 平均 %.3fs  中位 %.3fs  最长 %.3fs'
                  % (d.mean(), np.median(d), d.max()))
            print('   >=0.5s 的音: %d 个   >=1.0s 的音: %d   最长连续同音高段: %d'
                  % (int((d >= 0.5).sum()), int((d >= 1.0).sum()),
                     _longest_run(res.score.notes)))
            print('   平均音间距 %.3fs' % (gaps.mean() if gaps.size else 0.0))
        print()
    return 0


def _longest_run(notes) -> int:
    """最长「连续同音高」链条长度 —— 碎片化会把这个数字顶得很高。"""
    best = run = 1
    for a, b in zip(notes, notes[1:]):
        if a.pitch == b.pitch and abs(b.start - a.end) < 0.05:
            run += 1
            best = max(best, run)
        else:
            run = 1
    return best


if __name__ == '__main__':
    raise SystemExit(main())
