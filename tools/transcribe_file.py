"""把音频文件转录成曲谱（命令行工具 / 真实音乐验收）。

用法：
    python tools/transcribe_file.py <音频文件> [--out 输出.json] [--bpm N]
                                    [--quantize 1/8] [--scale major]
                                    [--sensitivity 0.5] [--prior 0.8]
                                    [--no-fold] [--dump N]

不带参数时跑一批内置的真实音乐样本做验收。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gtiharmonica import transcribe                          # noqa: E402
from gtiharmonica.instrument import note_name                 # noqa: E402
from gtiharmonica.score import save_json_score                # noqa: E402


def run_one(path, args):
    print('=' * 78)
    print('文件：%s' % path)
    print('=' * 78)
    size_mb = os.path.getsize(path) / 1024 / 1024
    print('大小：%.1f MB' % size_mb)

    opts = transcribe.TranscribeOptions(
        bpm=args.bpm,
        quantize=args.quantize,
        scale=args.scale,
        sensitivity=args.sensitivity,
        melody_prior=args.prior,
        fold_to_instrument=not args.no_fold,
    )

    t0 = time.perf_counter()
    res = transcribe.transcribe_file(path, opts)
    wall = time.perf_counter() - t0

    for line in res.summary_lines():
        print('  ' + line)
    print('  耗时：%.1f 秒（音频的 %.1f%%）'
          % (wall, wall / max(res.duration, 0.01) * 100))
    print('  分步：%s' % ', '.join('%s=%.2fs' % (k, v)
                                   for k, v in res.timer.items()))
    for w in res.warnings:
        print('  ! %s' % w)

    notes = res.score.notes
    if notes:
        pitches = [n.pitch for n in notes]
        from collections import Counter
        pc = Counter(p % 12 for p in pitches)
        names = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')
        dist = ' '.join('%s:%d' % (names[k], v)
                        for k, v in sorted(pc.items(),
                                           key=lambda kv: -kv[1]))
        print('  音级分布：%s' % dist)
        print('  音域：%s .. %s' % (note_name(min(pitches)),
                                    note_name(max(pitches))))
        print('  前 %d 个音符：' % min(args.dump, len(notes)))
        line = []
        for n in notes[:args.dump]:
            line.append('%s@%.2f' % (note_name(n.pitch), n.start))
        for i in range(0, len(line), 8):
            print('    ' + '  '.join(line[i:i + 8]))

    if args.out:
        save_json_score(res.score, args.out)
        print('  已保存：%s' % args.out)
    return res


SAMPLES = [
    r'D:\AI\downloads\bilibili-BV11Sge6AEnM\audio.mp3',
    r'D:\AI\downloads\deepseek4_audio.mp3',
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='*')
    ap.add_argument('--out')
    ap.add_argument('--bpm', type=float, default=0.0)
    ap.add_argument('--quantize', default='1/8')
    ap.add_argument('--scale', default='major')
    ap.add_argument('--sensitivity', type=float, default=0.5)
    ap.add_argument('--prior', type=float, default=0.8)
    ap.add_argument('--no-fold', action='store_true')
    ap.add_argument('--dump', type=int, default=24)
    args = ap.parse_args()

    files = args.files or [p for p in SAMPLES if os.path.isfile(p)]
    if not files:
        print('没有找到可测试的音频文件')
        return 1
    for i, path in enumerate(files):
        if not os.path.isfile(path):
            print('跳过（不存在）：%s' % path)
            continue
        out = None
        if args.out:
            if len(files) > 1:
                base, ext = os.path.splitext(args.out)
                out = '%s_%d%s' % (base, i, ext)
            else:
                out = args.out
        a = argparse.Namespace(**vars(args))
        a.out = out
        run_one(path, a)
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
