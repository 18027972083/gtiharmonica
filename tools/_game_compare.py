# -*- coding: utf-8 -*-
"""对照实验：内置 ONNX 推理 vs 外部 GameInfer.exe 的产物（同一段输入音频）。

用途：验证软件内置的推理实现与外部工具行为一致 —— 这是「集成」的正确性依据。
两边都是同一首歌（预兆）的人声轨，外部那份是之前用 GameInfer.exe 跑出来、
已经进曲库的曲谱。

用法：python tools/_game_compare.py [音频] [参照曲谱.json]
"""
from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _game_onnx_proto import GameOnnx, load_audio          # noqa: E402
from gtiharmonica.score import load_score                   # noqa: E402

MODEL = 'D:/AI/tools/GAME/model/GAME-1.0.3-small-onnx'


def main() -> int:
    audio = sys.argv[1] if len(sys.argv) > 1 else \
        'D:/AI/tools/uvr/separated/htdemucs/yuzhao/vocals.wav'
    ref_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.environ['APPDATA'], 'GTIHarmonica', 'songs', '预兆(GAME扒谱·人声分离).json')

    print('音频  :', audio)
    print('参照  :', ref_path)
    print('\n[1] 内置 ONNX 推理 ...')
    import time
    t0 = time.time()
    mine = GameOnnx(MODEL).extract(load_audio(audio))
    print('    完成: %d 音符，用时 %.1f 秒' % (len(mine), time.time() - t0))

    print('\n[2] 读取外部工具产出的曲谱 ...')
    ref = load_score(ref_path)
    theirs = [(float(n.start), float(n.duration), int(n.pitch)) for n in ref.notes]
    print('    完成: %d 音符（%s）' % (len(theirs), os.path.basename(ref_path)))

    # ---- 统计对比 ----
    mp = np.array([round(p) for _, _, p in mine])
    tp = np.array([p for _, _, p in theirs])
    ms = np.array([s for s, _, _ in mine])
    ts = np.array([s for s, _, _ in theirs])
    print('\n[3] 总体')
    print('    音符数     内置 %d   vs  外部 %d   （差 %+d，%.1f%%）'
          % (len(mine), len(theirs), len(mine) - len(theirs),
             100.0 * (len(mine) - len(theirs)) / max(len(theirs), 1)))
    print('    音域       内置 %d..%d  vs  外部 %d..%d'
          % (mp.min(), mp.max(), tp.min(), tp.max()))
    print('    结束时间   内置 %.1fs  vs  外部 %.1fs' % (ms.max(), ts.max()))

    # 逐音符匹配：时间最近的对手，容差内且音高一致算命中
    for tol in (0.03, 0.05, 0.10):
        hit = miss_pitch = miss_time = 0
        used = set()
        for s, _, p in mine:
            idx = int(np.argmin(np.abs(ts - s)))
            if abs(ts[idx] - s) > tol or idx in used:
                miss_time += 1
                continue
            used.add(idx)
            if tp[idx] == round(p):
                hit += 1
            else:
                miss_pitch += 1
        covered = hit + miss_pitch
        print('    容差 ±%.0fms: 时间对上 %d 个（占外部 %.1f%%），其中音高一致 %d 个'
              '（一致率 %.1f%%），音高不一致 %d 个，时间没对上 %d 个'
              % (tol * 1000, covered, 100.0 * covered / max(len(theirs), 1),
                 hit, 100.0 * hit / max(covered, 1), miss_pitch, miss_time))

    # 音高分布的粗略比较
    import collections
    mh = collections.Counter(mp.tolist())
    th = collections.Counter(tp.tolist())
    keys = sorted(set(mh) | set(th), key=lambda k: -(mh.get(k, 0) + th.get(k, 0)))
    print('\n[4] 主要音高分布（内置 / 外部）:')
    for k in keys[:12]:
        print('    MIDI %3d : %4d / %4d' % (k, mh.get(k, 0), th.get(k, 0)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
