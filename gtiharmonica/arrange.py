"""编排：把曲谱 + 乐器 + 策略 组合成可执行的演奏计划。

处理链：
    音轨过滤 → 和弦单音化 → 移调 → 八度折叠 → 指法选择 → 时间/换气修正

每一步都是可配置的，方便针对具体曲目做定向优化。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .fingering import (CostModel, FingeringStrategy, Metrics, build_strategy,
                        measure)
from .instrument import Fingering, Instrument, note_name
from .score import Note, Score

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

#: 和弦取音策略
CHORD_POLICIES = ('highest', 'lowest', 'first', 'loudest')


@dataclass
class Options:
    """演奏与编排参数。全部可调，用于定向优化。"""

    # -- 时间 --
    speed: float = 1.0              # 演奏速度倍率 0.25 .. 2.0
    gate: float = 0.9               # 音符连贯度 0.4 .. 1.0，越小越断
    breath_ms: int = 90             # 句尾换气时长（毫秒）0 .. 300
    min_gap: float = 0.012          # 相邻音之间的最小空隙（秒）
    phrase_gap: float = 0.18        # 自然间隔超过它就当作乐句结尾
    breath_ratio: float = 0.3       # 换气最多占音符时长的比例
    min_note: float = 0.02          # 音符最短时长（秒），低于此游戏可能漏读

    # -- 音高 --
    transpose: int = 0              # 移调半音 -24 .. 24
    fold_octaves: bool = True       # 超音域时折叠八度
    fold_prefer: str = 'nearest'    # nearest | down | up
    chord_policy: str = 'highest'   # 和弦取哪个音
    chord_tolerance: float = 0.001  # 视为「同时发声」的时间容差（秒）
    chord_tail: bool = False        # 和声尾巴补回：被压声部比主音长时续上（多声部直转推荐）
    auto_octave: bool = False       # 自动选八度：整数八度平移取命中率最高的一档

    # -- 指法 --
    strategy: str = 'optimal'
    cost: CostModel = field(default_factory=CostModel)

    # -- 范围 --
    track: Optional[int] = None     # None = 合并全部音轨

    # -- 演奏体验 --
    trim_lead: bool = True          # 去除开头空拍：把首音平移到 0 秒起弹

    def validate(self) -> None:
        if not (math.isfinite(self.speed) and 0.25 <= self.speed <= 2.0):
            raise ValueError('speed 必须在 0.25 .. 2.0，当前 %r' % self.speed)
        if not (0.4 <= self.gate <= 1.0):
            raise ValueError('gate 必须在 0.4 .. 1.0，当前 %r' % self.gate)
        if not (0 <= self.breath_ms <= 300):
            raise ValueError('breath_ms 必须在 0 .. 300，当前 %r' % self.breath_ms)
        if not (isinstance(self.transpose, int) and -24 <= self.transpose <= 24):
            raise ValueError('transpose 必须在 -24 .. 24，当前 %r' % self.transpose)
        if self.chord_policy not in CHORD_POLICIES:
            raise ValueError('chord_policy 只能是 %s' % ', '.join(CHORD_POLICIES))
        if self.fold_prefer not in ('nearest', 'down', 'up'):
            raise ValueError("fold_prefer 只能是 nearest / down / up")


# ---------------------------------------------------------------------------
# 输出结构
# ---------------------------------------------------------------------------

@dataclass
class PlayStep:
    """一个「按键动作」：在 start 时刻按下 key(+modifiers)，持续 duration 秒。"""

    pitch: int                  # 实际发音的 MIDI 音高
    source_pitch: int           # 曲谱里的原始音高（移调/折叠前）
    fingering: Fingering
    start: float
    duration: float
    track: int = 0

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def key(self) -> str:
        return self.fingering.key

    @property
    def modifiers(self) -> Tuple[str, ...]:
        return self.fingering.modifiers

    def describe(self) -> str:
        return '%8.3fs  %-24s %6.3fs  %-4s' % (
            self.start, self.fingering.describe(), self.duration, note_name(self.pitch))


@dataclass
class Arrangement:
    title: str
    steps: List[PlayStep] = field(default_factory=list)
    metrics: Metrics = field(default_factory=Metrics)
    stats: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    strategy: str = ''

    def __len__(self) -> int:
        return len(self.steps)

    @property
    def duration(self) -> float:
        return max((s.end for s in self.steps), default=0.0)

    def summary(self) -> str:
        s = self.stats
        return ('%s | %d 音 / %.1f 秒 | 策略 %s | 折叠 %d 丢弃 %d 单音化 %d' % (
            self.title, len(self.steps), self.duration, self.strategy,
            s.get('folded', 0), s.get('dropped', 0), s.get('reduced', 0)))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _pick_from_chord(chord: Sequence[Note], policy: str) -> Note:
    if policy == 'highest':
        return max(chord, key=lambda n: (n.pitch, -n.track))
    if policy == 'lowest':
        return min(chord, key=lambda n: (n.pitch, n.track))
    if policy == 'loudest':
        return max(chord, key=lambda n: (n.velocity, n.pitch))
    return chord[0]


def monophonic(source: Sequence[Note], policy: str = 'highest',
               tolerance: float = 0.001,
               tail_restore: bool = False) -> Tuple[List[Note], int]:
    """把可能含和弦的序列压成单旋律。

    起始时间相差不超过 tolerance 的音符视为「同时发声」，按 policy 取一个。
    tail_restore 开启时（仅 highest / lowest），被压掉的声部若比主音更长，
    在主音结束后补回它的尾巴 —— 多声部 MIDI 直转时和声线条不断（移植自
    midikey-player 的 MergeVoicesByPriority，MIT）。
    返回 (单音序列, 被丢弃的音符数)。
    """
    if not source:
        return [], 0
    ordered = sorted(source, key=lambda n: (n.start, n.pitch))
    if tail_restore and policy in ('highest', 'lowest'):
        return _monophonic_with_tails(ordered, policy, tolerance)
    melody: List[Note] = []
    reduced = 0
    i = 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and ordered[j].start - ordered[i].start <= tolerance:
            j += 1
        chord = ordered[i:j]
        melody.append(_pick_from_chord(chord, policy))
        reduced += len(chord) - 1
        i = j
    return melody, reduced


def _monophonic_with_tails(ordered: List[Note], policy: str,
                           tolerance: float) -> Tuple[List[Note], int]:
    sign = -1 if policy == 'highest' else 1      # rank 越小优先级越高
    rank = lambda n: sign * n.pitch

    merged: List[Note] = []
    i = 0
    while i < len(ordered):
        j = i
        s0 = ordered[i].start
        while j + 1 < len(ordered) and ordered[j + 1].start - s0 <= tolerance:
            j += 1
        best = min(ordered[i:j + 1], key=rank)
        merged.append(best)
        for k in range(i, j + 1):
            o = ordered[k]
            if o is best:
                continue
            if o.end > best.end + 1e-9:
                merged.append(_slice_note(o, best.end, o.end))
        i = j + 1

    merged.sort(key=lambda n: (n.start, rank(n), n.pitch))
    result: List[Note] = []
    last: Optional[Note] = None
    last_rank: Optional[float] = None
    for cand in merged:
        r = rank(cand)
        if last is not None and cand.start < last.end - 1e-9 and r > last_rank:
            if cand.end > last.end + 1e-9:
                tail = _slice_note(cand, last.end, cand.end)
                result.append(tail)
                last = tail
                last_rank = r
            continue
        result.append(cand)
        last = cand
        last_rank = r
    result.sort(key=lambda n: n.start)
    reduced = max(len(ordered) - len(result), 0)
    return result, reduced


def _slice_note(n: Note, start: float, end: float) -> Note:
    return Note(pitch=n.pitch, start=round(start, 6),
                duration=round(max(end - start, 0.0), 6),
                velocity=n.velocity, track=n.track)


def _auto_octave_shift(pitches: Sequence[int], instrument: Instrument) -> int:
    """整数八度平移里挑「落在口琴自然音域内」的音数最高的一档；
    并列取离平均八度最近的。移植自 midikey-player AutoBaseOctave (MIT)。"""
    if not pitches:
        return 0
    lo, hi = instrument.playable_range()
    octs = [p // 12 - 1 for p in pitches]
    mean_o = sum(octs) / len(octs)
    best_k, best_hit = 0, -1
    for k in range((lo - max(pitches) + 11) // 12, (hi - min(pitches)) // 12 + 1):
        hit = sum(1 for p in pitches if lo <= p + k * 12 <= hi)
        if hit > best_hit or (hit == best_hit
                              and abs(k - mean_o) < abs(best_k - mean_o)):
            best_k, best_hit = k, hit
    return best_k * 12


def arrange(score: Score, instrument: Instrument, options: Optional[Options] = None,
            progress=None) -> Arrangement:
    """生成演奏计划。"""
    opts = options or Options()
    opts.validate()
    instrument.validate()
    table = instrument.fingerings()

    # 1) 音轨过滤
    source = score.track_notes(opts.track)
    if not source:
        raise ValueError('所选音轨没有音符（音轨 %r）' % (opts.track,))

    # 2) 单音化（可选和声尾巴补回）
    melody, reduced = monophonic(source, opts.chord_policy, opts.chord_tolerance,
                                 tail_restore=opts.chord_tail)

    # 2.5) 自动选八度（移植自 midikey-player AutoBaseOctave）：整数八度
    # 平移里挑「原始命中」最高的一档，折叠作为兜底而非主路径
    if opts.auto_octave and melody:
        octave_shift = _auto_octave_shift([n.pitch for n in melody], instrument)
    else:
        octave_shift = 0

    # 3) 移调 + 八度折叠
    stats = {'folded': 0, 'dropped': 0, 'reduced': reduced}
    playable: List[Tuple[Note, int]] = []      # (原音符, 实际音高)
    seen: Dict[int, List[Fingering]] = {}      # 折叠后可能引入新的音高

    for note in melody:
        pitch = note.pitch + octave_shift + opts.transpose
        if pitch not in table:
            if not opts.fold_octaves:
                stats['dropped'] += 1
                continue
            folded = instrument.nearest_playable(pitch, opts.fold_prefer)
            if folded is None:
                stats['dropped'] += 1
                continue
            if folded != pitch:
                stats['folded'] += 1
            pitch = folded
        playable.append((note, pitch))

    if not playable:
        raise ValueError('所有音符都超出音域且无法折叠，请调整 transpose 或 base')

    # 4) 指法选择（策略在这里发挥作用）
    pitches = [p for _, p in playable]
    strategy: FingeringStrategy = build_strategy(opts.strategy, opts.cost)
    fingers = strategy.plan(pitches, table)
    if len(fingers) != len(playable):
        raise RuntimeError('策略返回的指法数量与音符不符：%d vs %d'
                           % (len(fingers), len(playable)))

    # 5) 时间轴与换气
    steps: List[PlayStep] = []
    last = len(playable) - 1
    for index, ((note, pitch), fing) in enumerate(zip(playable, fingers)):
        start = note.start / opts.speed
        dur = note.duration / opts.speed

        if index < last:
            next_start = playable[index + 1][0].start / opts.speed
        else:
            next_start = None

        duration = _fit_duration(start, dur, next_start, opts, note.phrase_end)
        steps.append(PlayStep(pitch=pitch, source_pitch=note.pitch,
                              fingering=fing, start=start, duration=duration,
                              track=note.track))

    # 6) 去除开头空拍：前奏空白不占演奏等待时间（转录谱常见几十秒前奏）
    if opts.trim_lead and steps and steps[0].start > 0.05:
        shift = steps[0].start
        steps = [PlayStep(pitch=s.pitch, source_pitch=s.source_pitch,
                          fingering=s.fingering, start=s.start - shift,
                          duration=s.duration, track=s.track)
                 for s in steps]

    result = Arrangement(title=score.title, steps=steps,
                         metrics=measure(fingers), stats=stats,
                         warnings=list(score.warnings),
                         strategy=strategy.name)
    return result


def arrange_from_notes(notes: Sequence[Note], instrument: Instrument,
                       options: Optional[Options] = None,
                       title: str = '', stats: Optional[Dict[str, int]] = None,
                       warnings: Optional[Sequence[str]] = None) -> Arrangement:
    """从「已经定好音高」的音符生成演奏计划。

    与 `arrange()` 的区别只有一件事：跳过音轨过滤、和弦单音化、移调与
    八度折叠。理由是会用到这个函数的地方 —— 乐谱编辑器 —— 里显示的音高
    就是最终要弹出来的音高，再走一次移调/折叠就是对已经正确的音高做二次
    变换，结果必然错音。

    指法选择、时间轴、换气、指标仍然全部按当前参数重算，所以编辑完之后
    「按键次数」「修饰键切换」这些统计依然可信。

    音符已经保证可演奏时用这个函数；从曲谱原始音符出发仍走 `arrange()`。
    """
    opts = options or Options()
    opts.validate()
    instrument.validate()
    table = instrument.fingerings()

    playable: List[Tuple[Note, int]] = []
    dropped = 0
    for note in notes:
        pitch = int(note.pitch)
        if pitch in table:
            playable.append((note, pitch))
        else:
            # 编辑器可能插入音域外的音高。这里静默丢弃比折叠更诚实：
            # 用户明确写了这个音，悄悄改成别的音只会让人更困惑。
            dropped += 1

    if not playable:
        raise ValueError('没有落在乐器音域内的音符')

    merged_stats = {'folded': 0, 'dropped': dropped, 'reduced': 0}
    if stats:
        merged_stats.update({k: int(v) for k, v in stats.items()})
        merged_stats['dropped'] = dropped

    pitches = [p for _, p in playable]
    strategy: FingeringStrategy = build_strategy(opts.strategy, opts.cost)
    fingers = strategy.plan(pitches, table)
    if len(fingers) != len(playable):
        raise RuntimeError('策略返回的指法数量与音符不符：%d vs %d'
                           % (len(fingers), len(playable)))

    steps: List[PlayStep] = []
    last = len(playable) - 1
    for index, ((note, pitch), fing) in enumerate(zip(playable, fingers)):
        start = note.start / opts.speed
        dur = note.duration / opts.speed
        next_start = (playable[index + 1][0].start / opts.speed
                      if index < last else None)
        duration = _fit_duration(start, dur, next_start, opts, note.phrase_end)
        steps.append(PlayStep(pitch=pitch, source_pitch=note.pitch,
                              fingering=fing, start=start, duration=duration,
                              track=note.track))

    return Arrangement(title=title, steps=steps, metrics=measure(fingers),
                       stats=merged_stats, warnings=list(warnings or []),
                       strategy=strategy.name)


def _fit_duration(start: float, dur: float, next_start: Optional[float],
                  opts: Options, marked_phrase_end: bool = False) -> float:
    """按 gate / 换气规则算出实际发声时长。

    规则（参数全部可调）：
      * 相邻音之间至少留 min_gap
      * gate 决定衰减掉多少时值
      * 乐句结尾追加 breath_ms 换气，但不超过 breath_ratio × 时值
      * 最终时长不低于 min_note

    乐句结尾的判定优先级：曲谱里显式标记的 phrase_end >
    自然间隔超过 phrase_gap > 这是最后一个音。
    带 phrase_end 的曲谱能完整保留编曲者的换气意图。
    """
    if next_start is None:
        gap_after = 1.0
    else:
        gap_after = next_start - (start + dur)

    is_phrase_end = (marked_phrase_end or (next_start is None)
                     or (gap_after >= opts.phrase_gap))

    if next_start is None:
        required_gap = 0.0
    else:
        required_gap = max(0.0, opts.min_gap - max(0.0, gap_after))

    gap = max(required_gap, dur * (1.0 - opts.gate))

    if is_phrase_end:
        gap = max(gap, min(opts.breath_ms / 1000.0, dur * opts.breath_ratio))

    return max(min(opts.min_note, dur), dur - gap)
