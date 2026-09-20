# -*- coding: utf-8 -*-
"""验收：用《心似烟火》给「音频 → 曲谱」链路做一次带标准答案的精度测试。

这首歌本地只有 MIDI（没有音频文件），所以流程是：
    MIDI（标准答案）→ 渲染成音频（本程序的合成器）→ 内置转谱 → 逐音符比对

这样能同时验证两件事：
  * 链路本身算得对（音高、时间、音符切分与标准答案的偏差有多大）
  * 整条路能从「一段音频」走到「能进曲库的曲谱」

用法：python tools/_accept_xinsiyanhuo.py [MIDI 路径]
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from gtiharmonica import synth                                  # noqa: E402
from gtiharmonica.audio2score import transcribe_audio           # noqa: E402
from gtiharmonica.instrument import note_name                   # noqa: E402
from gtiharmonica.score import load_score                       # noqa: E402

MIDI = sys.argv[1] if len(sys.argv) > 1 else \
    'C:/Users/23913/Downloads/心似烟火.mid'
RENDERED = os.path.join(ROOT, 'build', '_xsyh_rendered.wav')


def main() -> int:
    print('=' * 66)
    print('《心似烟火》音频转曲谱 · 精度验收')
    print('=' * 66)

    # ---- 1. 标准答案：原始 MIDI ----
    truth = load_score(MIDI)
    tp = [n.pitch for n in truth.notes]
    print('\n[1] 标准答案（原始 MIDI）')
    print('    文件  : %s' % MIDI)
    print('    音符  : %d 个   时长 %.1f 秒   音域 %s..%s（MIDI %d..%d）'
          % (len(truth.notes), truth.duration,
             note_name(min(tp)), note_name(max(tp)), min(tp), max(tp)))

    # ---- 2. 渲染成音频 ----
    print('\n[2] 渲染成音频（用本程序的合成器）')
    os.makedirs(os.path.dirname(RENDERED), exist_ok=True)
    t0 = time.time()
    wav = synth.render([(n.pitch, n.start, n.duration) for n in truth.notes])
    with open(RENDERED, 'wb') as fh:
        fh.write(wav)
    import soundfile as sf
    data, sr = sf.read(RENDERED)
    print('    输出  : %s（%.1f MB，%.1f 秒 @ %d Hz，渲染 %.1f 秒）'
          % (os.path.basename(RENDERED), len(wav) / 1024 / 1024,
             len(data) / sr, sr, time.time() - t0))

    # ---- 3. 内置转谱 ----
    print('\n[3] 内置链路转谱（GAME ONNX）')
    t0 = time.time()
    got = transcribe_audio(RENDERED, title='心似烟火(验收)')
    el = time.time() - t0
    if not len(got):
        print('    没有识别出音符 ✗')
        return 1
    gp = [n.pitch for n in got.notes]
    print('    音符  : %d 个   时长 %.1f 秒   音域 %s..%s（MIDI %d..%d）'
          % (len(got.notes), got.duration,
             note_name(min(gp)), note_name(max(gp)), min(gp), max(gp)))
    print('    耗时  : %.1f 秒（音频 %.1f 秒，约 %.0fx 实时）'
          % (el, len(data) / sr, (len(data) / sr) / max(el, 1e-6)))

    # ---- 4. 逐音符比对 ----
    print('\n[4] 与标准答案逐音符比对')
    ts = np.array([n.start for n in truth.notes])
    tpd = np.array(tp)
    print('    音符数    标准 %d  →  转谱 %d   （%+.1f%%）'
          % (len(truth.notes), len(got.notes),
             100.0 * (len(got.notes) - len(truth.notes)) / max(len(truth.notes), 1)))
    # 以 0.2 秒为容差做匹配（转谱的音符会合并/拆分，允许一对多）
    hit = pitch_ok = edge = 0
    for n in got.notes:
        idx = np.where(np.abs(ts - n.start) <= 0.25)[0]
        if len(idx) == 0:
            edge += 1
            continue
        hit += 1
        if np.min(np.abs(tpd[idx] - n.pitch)) <= 1:      # 允许 ±1 半音
            pitch_ok += 1
    print('    时间对齐  %d / %d 个转谱音符落在标准音符 ±0.25s 内（%.1f%%）'
          % (hit, len(got.notes), 100.0 * hit / max(len(got.notes), 1)))
    print('    音高一致  %d / %d（±1 半音内，%.1f%%）  明显偏离 %d 个'
          % (pitch_ok, hit, 100.0 * pitch_ok / max(hit, 1), hit - pitch_ok))
    # 音域与分布
    print('\n    音域对比：标准 %s..%s   转谱 %s..%s'
          % (note_name(min(tp)), note_name(max(tp)),
             note_name(min(gp)), note_name(max(gp))))
    import collections
    th = collections.Counter(tp)
    gh = collections.Counter(gp)
    keys = sorted(set(th) | set(gh), key=lambda k: -(th.get(k, 0) + gh.get(k, 0)))
    print('    主要音高分布（标准 / 转谱）:')
    for k in keys[:10]:
        print('      MIDI %3d (%s) : %4d / %4d'
              % (k, note_name(k), th.get(k, 0), gh.get(k, 0)))

    ok = (100.0 * hit / max(len(got.notes), 1) >= 80.0
          and 100.0 * pitch_ok / max(hit, 1) >= 80.0)
    print('\n结论：%s' % ('通过 —— 时间对齐与音高一致率都 ≥80%%'
                          if ok else '未达标，见上面的分布差异'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
