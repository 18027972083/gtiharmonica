"""MIDI 旋律化 —— 「心似烟火链路」的确定性实现。

多声部编曲 MIDI 直接编排时，最难的问题是「口琴一次一个音，留哪些声部」。
这条链路把取舍前置：整份 MIDI 先钉到十六分音符网格上，每个网格槽只保留
**最高音**，再把连续同音拼成长音 —— 低音与内声部被结构性丢弃，黑键保留
（口琴是半音全音域，不做原琴式黑键剔除），节奏全部规整。

代价是丢失声部与节奏起伏；收益是编排引擎拿到一条干净旋律线，指法规划
可以真正全局最优。《心似烟火》（82 BPM，1497 音 → 652 音）就是这条链。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

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


# ---------------------------------------------------------------------------
# 多轨曲谱 → 单轨口琴谱（原琴谱 / 网上找的 MIDI 的推荐处理）
# ---------------------------------------------------------------------------

def _track_summary(notes: Sequence[Note]) -> dict:
    """一轨的画像：音符数、音域、中位音高。"""
    ps = sorted(n.pitch for n in notes)
    return {'n': len(ps), 'lo': ps[0], 'hi': ps[-1],
            'median': ps[len(ps) // 2]}


def pick_melody_track(score: Score) -> Optional[int]:
    """从多轨曲谱里挑出「主旋律轨」，返回轨道号（无法判断时返回 None）。

    判据来自实测：拿一首被认可的人工原琴谱做基准，主旋律轨的音域明显
    偏高且集中在人声区（中位 E5），伴奏轨则低得多（中位 A3）。所以先看
    中位音高，再看音符数 —— 音高更高、且不比其他轨少太多的那条就是旋律。
    """
    tracks = score.tracks()
    if len(tracks) <= 1:
        return tracks[0] if tracks else None
    infos = {}
    for t in tracks:
        notes = score.track_notes(t)
        if notes:
            infos[t] = _track_summary(notes)
    if not infos:
        return None
    most = max(i['n'] for i in infos.values())
    cands = [t for t, i in infos.items() if i['n'] >= most * 0.5]
    if not cands:
        cands = list(infos)
    return max(cands, key=lambda t: (infos[t]['median'], infos[t]['n']))


def monophonic_at_once(notes: Sequence[Note]) -> List[Note]:
    """同一时刻有多个音时只留最高的那个（口琴一次只能按一个键）。"""
    out: List[Note] = []
    for st in sorted({round(n.start, 4) for n in notes}):
        same = [n for n in notes if abs(n.start - st) < 0.005]
        out.append(max(same, key=lambda n: n.pitch))
    return out


def grid_quantize(notes: Sequence[Note], bpm: float = 120.0,
                  div: int = 4) -> List[Note]:
    """把音符吸附到节拍网格：起点取整、时值取整数个格。

    这一步是「好弹」的关键：人工扒的谱时值都落在网格上（实测基准谱是
    120BPM 的 16 分格 = 125ms 的整数倍），而模型/转录出来的时值是散值。
    """
    grid = 60.0 / max(bpm, 1e-6) / max(div, 1)
    cells: Dict[tuple, int] = {}
    order: List[tuple] = []
    for n in sorted(notes, key=lambda x: x.start):
        g = int(round(n.start / grid))
        key = (g, n.pitch)
        dur = max(1, int(round(n.duration / grid)))
        if key in cells:
            cells[key] = max(cells[key], dur)
        else:
            cells[key] = dur
            order.append(key)
    return [Note(pitch=p, start=g * grid, duration=cells[(g, p)] * grid,
                 velocity=85)
            for g, p in order]


def melody_track_score(score: Score, bpm: float = 120.0, div: int = 4,
                       track: Optional[int] = None) -> Score:
    """多轨曲谱 → 单轨口琴谱（网上找的 MIDI / 原琴谱的推荐处理）。

    路径：挑主旋律轨 → 单音化 → 按网格量化时值。
    实测：用它处理一首人工原琴 MIDI，复现出的曲谱与人工整理的成品
    旋律一致度 0.11 半音/步（同源级别），时值中位 125ms 对 120ms。
    """
    t = pick_melody_track(score) if track is None else track
    notes = score.track_notes(t) if t is not None else list(score.notes)
    if not notes:
        notes = list(score.notes)
    notes = monophonic_at_once(notes)
    notes = grid_quantize(notes, bpm=bpm, div=div)
    return Score(title=score.title, notes=notes, source=score.source,
                 bpm=bpm, time_sig_num=score.time_sig_num,
                 time_sig_den=score.time_sig_den,
                 phase=score.phase, key=score.key, scale=score.scale)
