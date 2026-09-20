"""MIDI 旋律化 —— 「心似烟火链路」的确定性实现。

多声部编曲 MIDI 直接编排时，最难的问题是「口琴一次一个音，留哪些声部」。
这条链路把取舍前置：整份 MIDI 先钉到十六分音符网格上，每个网格槽只保留
**最高音**，再把连续同音拼成长音 —— 低音与内声部被结构性丢弃，黑键保留
（口琴是半音全音域，不做原琴式黑键剔除），节奏全部规整。

代价是丢失声部与节奏起伏；收益是编排引擎拿到一条干净旋律线，指法规划
可以真正全局最优。《心似烟火》（82 BPM，1497 音 → 652 音）就是这条链。
"""
from __future__ import annotations

from .score import Note, Score


def melodize(score: Score, grid_div: int = 4) -> Score:
    """把多声部 Score 旋律化：选旋律轨 → 网格量化 → 同音合并。

    grid_div = 每拍细分数（4 = 十六分音符，与心似烟火链路一致）。
    旋律轨 = 音符数最多的轨（并列取平均音高高者）；若选中轨的音符
    不足全曲三分之一，说明轨拆分不可靠，退回全线逐槽取最高音。
    返回新 Score：bpm 继承自来源，velocity 统一 85，与「保存编排
    曲谱」的产物同构。
    """
    bpm = float(score.bpm or 120.0)
    grid = 60.0 / bpm / grid_div

    notes = list(score.notes)
    if notes:
        counts: dict[int, list[float]] = {}
        for n in notes:
            counts.setdefault(n.track, []).append(n.pitch)
        track = max(counts, key=lambda t: (len(counts[t]),
                                           sum(counts[t]) / len(counts[t])))
        if len(counts[track]) * 3 >= len(notes):
            notes = [n for n in notes if n.track == track]
        else:
            track = None

    out = []
    if track is not None:
        # 单旋律轨：逐音量化（保留反复音与节奏型），轨内重叠裁剪起点、
        # 完全被盖住的内声部丢弃 —— 同一时刻先处理高音。
        for n in sorted(notes, key=lambda n: (n.start, -n.pitch)):
            k0 = max(0, int(round(n.start / grid)))
            k1 = max(k0 + 1, int(round((n.start + n.duration) / grid)))
            if out and k0 < out[-1][1]:
                k0 = out[-1][1]
            if k1 <= k0:
                continue
            out.append((k0, k1, n.pitch))
        notes = [Note(pitch=p, start=round(k0 * grid, 4),
                      duration=round((k1 - k0) * grid, 4),
                      velocity=85, track=0)
                 for k0, k1, p in out]
    else:
        # 轨拆分不可靠（混成一轨）：退回逐槽取最高音，再拼同音长音
        slots: dict[int, int] = {}
        for n in notes:
            k0 = int(round(n.start / grid))
            k1 = max(k0 + 1, int(round((n.start + n.duration) / grid)))
            for k in range(k0, k1):
                cur = slots.get(k)
                if cur is None or n.pitch > cur:
                    slots[k] = n.pitch
        notes = []
        ks = sorted(slots)
        i = 0
        while i < len(ks):
            k0, p = ks[i], slots[ks[i]]
            j = i + 1
            while j < len(ks) and slots[ks[j]] == p and ks[j] == ks[j - 1] + 1:
                j += 1
            notes.append(Note(pitch=p,
                              start=round(ks[i] * grid, 4),
                              duration=round((ks[j - 1] - ks[i] + 1) * grid, 4),
                              velocity=85, track=0))
            i = j

    return Score(title=score.title, notes=notes, source=score.source,
                 bpm=bpm, time_sig_num=score.time_sig_num,
                 time_sig_den=score.time_sig_den,
                 phase=score.phase, key=score.key, scale=score.scale)
