"""诊断：round-trip 后音符数减少，是正常单声部化还是过度合并。

两个检查：
1. 原曲复音占比（口琴是单声部，复音必然被压掉，这部分减少是正常的）
2. **过度合并**：转录出来的某个音，内部是否跨越了原曲的音符间隙
   —— 那意味着两个本该分开的音被粘成了一个长音
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from gtiharmonica.instrument import note_name                  # noqa: E402
from gtiharmonica.score import load_json_score                 # noqa: E402
from gtiharmonica.synth import render                          # noqa: E402
from gtiharmonica.transcribe import TranscribeOptions, transcribe_file  # noqa: E402

SONGS = r'D:\AI\gti-harmonica\songs'


def fmt(notes, n=20):
    return ' '.join('%s@%.2f(%.2f)' % (note_name(x.pitch), x.start, x.duration)
                    for x in notes[:n])


def over_merge(src, got, label, gap_thr=0.10):
    """转录音内部跨越了原曲间隙 = 把两个音粘住了。"""
    hits = []
    for g in got:
        inner = sorted((s for s in src
                        if s.start >= g.start - 0.03 and s.end <= g.end + 0.03),
                       key=lambda s: s.start)
        for a, b in zip(inner, inner[1:]):
            if b.start - a.end > gap_thr:
                hits.append((g, a, b, b.start - a.end))
                break
    print('   [%s] 内部跨越原曲间隙的转录音：%d / %d'
          % (label, len(hits), len(got)))
    for g, a, b, gap in hits[:6]:
        print('        %s@%.2f(%.2f) 跨越了 %s@%.2f->%s@%.2f，间隙 %.2fs'
              % (note_name(g.pitch), g.start, g.duration,
                 note_name(a.pitch), a.start, note_name(b.pitch), b.start, gap))
    return len(hits)


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else 'Scarborough Fair.json'
    path = os.path.join(SONGS, name)
    score = load_json_score(path)
    src = list(score.notes)

    poly = sum(1 for n in src if any(
        m is not n and abs(m.start - n.start) < 0.01 for m in src))
    print('%s' % name)
    print('  原曲 %d 音（同起点复音 %d 个 -> 单声部化必然减少）' % (len(src), poly))
    print('  原曲前 16：%s' % fmt(src, 16))

    wav = render([(n.pitch, n.start, n.duration) for n in src])
    tmp = os.path.join(tempfile.gettempdir(), 'gti-diag.wav')
    with open(tmp, 'wb') as f:
        f.write(wav)

    for rm, label in ((0.0, 'OLD'), (0.35, 'NEW')):
        res = transcribe_file(tmp, TranscribeOptions(reattack_min=rm))
        got = res.score.notes
        print('  [%s] 转录 %d 音' % (label, len(got)))
        print('       前 16：%s' % fmt(got, 16))
        print('       分裂点：%s'
              % ' '.join('%.2f' % t for t in res.split_times[:18]))
        over_merge(src, got, label)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
