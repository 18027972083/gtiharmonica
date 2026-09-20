"""乐谱层：把「小节、拍号、时值」这些符号信息从时间轴上立起来。

信号层只知道「某个音高在某时刻响了多久」；乐谱层知道「这是第 3 小节的
第 2 拍，一个附点四分音符」。多这一层带来三样东西：

    乐理约束  同音高连续占满网格又没有间隙，在乐谱上就是一个长音，
              不可能是一串互相独立的碎片。这类约束能反过来修正识别结果。
    可读可改  人看得懂小节线和时值，也就能在编辑器里直接改。
    可导出    不绑定本软件的中间表示，将来导出 MusicXML / 简谱都从这里走。

时间单位沿用整个工程的约定：**秒**（浮点）。拍号只影响「怎么切小节」和
「时值叫什么名字」，不改变音符的绝对时间。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 时值
# ---------------------------------------------------------------------------

#: 时值符号表：(以四分音符为 1 的拍数, 名称)。
#: 从全音符往下排，`duration_symbol` 取最接近的一个。
DURATION_TABLE: Tuple[Tuple[float, str], ...] = (
    (4.0, '全音符'),
    (3.0, '附点二分音符'),
    (2.0, '二分音符'),
    (1.5, '附点四分音符'),
    (1.0, '四分音符'),
    (0.75, '附点八分音符'),
    (0.5, '八分音符'),
    (0.375, '附点十六分音符'),
    (0.25, '十六分音符'),
    (0.125, '三十二分音符'),
)

#: 判定两个时值「是不是同一个符号」时的相对容差。
DURATION_TOL = 0.18


def duration_symbol(beats: float, tol: float = DURATION_TOL) -> str:
    """把「多少个四分音符」翻译成时值名称。

    取对数距离而不是线性距离：0.5 和 1.0 之间的听感/记谱差距，跟 2.0 和
    4.0 之间是一样的，线性比较会系统性偏向长时值。
    """
    if beats <= 0:
        return '—'
    best, best_d = DURATION_TABLE[-1][1], float('inf')
    lb = _log2(max(beats, 1e-6))
    for value, name in DURATION_TABLE:
        d = abs(lb - _log2(value))
        if d < best_d:
            best, best_d = name, d
    # 差得太远（比如三连音）就不硬套名字，直接报拍数
    if best_d > tol * 2.0:
        return '%.3g 拍' % beats
    return best


def _log2(x: float) -> float:
    import math
    return math.log2(x)


# ---------------------------------------------------------------------------
# 拍号与上下文
# ---------------------------------------------------------------------------

@dataclass
class TimeSignature:
    """拍号。默认 4/4 —— 流行乐里绝大多数情况都是它。"""

    numerator: int = 4
    denominator: int = 4

    def __str__(self) -> str:
        return '%d/%d' % (self.numerator, self.denominator)

    def validate(self) -> None:
        if self.numerator < 1:
            raise ValueError('拍号分子必须 >= 1')
        if self.denominator not in (1, 2, 4, 8, 16):
            raise ValueError('拍号分母必须是 1/2/4/8/16')


@dataclass
class NotationContext:
    """一首曲子的节拍上下文。

    没有它，`Score.notes` 只是一串孤立的时间点：无法回答「这是第几小节」、
    「这个音是几分音符」、「哪里该画小节线」。
    """

    bpm: float = 120.0
    time_signature: TimeSignature = field(default_factory=TimeSignature)
    #: 第一条小节线的时间（秒）。节拍网格从这里开始铺。
    phase: float = 0.0
    key: int = 0
    scale: str = 'major'

    # -- 基本换算 ---------------------------------------------------------

    @property
    def beat_sec(self) -> float:
        """一个**四分音符**的秒数。

        注意与「一拍」的区别：6/8 里一拍是八分音符。这里始终返回四分音符
        时长，小节长度由 `bar_sec` 按拍号换算。
        """
        return 60.0 / self.bpm if self.bpm > 0 else 0.5

    @property
    def bar_sec(self) -> float:
        ts = self.time_signature
        return self.beat_sec * 4.0 / ts.denominator * ts.numerator

    def beats(self, seconds: float) -> float:
        """秒 -> 四分音符个数。"""
        return seconds / self.beat_sec if self.beat_sec > 0 else 0.0

    def beat_position(self, t: float) -> float:
        """t 距 phase 有多少个四分音符（可负、可小数）。"""
        return (t - self.phase) / self.beat_sec if self.beat_sec > 0 else 0.0

    # -- 小节 -------------------------------------------------------------

    def bar_index(self, t: float) -> int:
        """t 落在第几小节（从 0 开始，可负）。"""
        bar = self.bar_sec
        if bar <= 0:
            return 0
        import math
        return int(math.floor((t - self.phase) / bar))

    def bar_bounds(self, index: int) -> Tuple[float, float]:
        bar = self.bar_sec
        start = self.phase + index * bar
        return start, start + bar

    def bar_beat(self, t: float) -> Tuple[int, int, float]:
        """t -> (小节序号, 小节内的第几拍, 拍内小数位置)。"""
        bar = self.bar_sec
        if bar <= 0:
            return 0, 0, 0.0
        import math
        rel = (t - self.phase) / bar
        index = int(math.floor(rel))
        within = (rel - index) * self.time_signature.numerator
        beat = int(math.floor(within))
        return index, beat, within - beat

    def beats_per_bar(self) -> int:
        return self.time_signature.numerator

    def snap(self, t: float, grid: float) -> float:
        """把时间吸附到最接近的网格点（grid 以四分音符为单位）。"""
        step = self.beat_sec * grid
        if step <= 0:
            return t
        import math
        return self.phase + round((t - self.phase) / step) * step

    def snap_beats(self, beats: float, grid: float) -> float:
        if grid <= 0:
            return beats
        return round(beats / grid) * grid

    def describe(self) -> str:
        return '%.1f BPM · %s' % (self.bpm, self.time_signature)


# ---------------------------------------------------------------------------
# 拍号推断
# ---------------------------------------------------------------------------

#: 候选拍号。2/4 刻意不在其中：它和 4/4 在起音分布上几乎不可区分（4/4 的
#: 每小节正好是两个 2/4 小节），而猜错的代价是整首曲子的小节线画法全变；
#: 4/4 又是流行乐里的绝大多数情况，所以宁可保守。6/8 因为在强弱结构上
#: 与 4/4 差异明显（两个复合拍 vs 四个简单拍）而保留。
TIME_SIGNATURE_CANDIDATES: Tuple[TimeSignature, ...] = (
    TimeSignature(4, 4),
    TimeSignature(3, 4),
    TimeSignature(6, 8),
)

#: 判定「落在强拍上」的时间容差（**秒**，绝对值）。
#:
#: 必须用绝对时间而不是小节长度的比例。用比例时短小节永远赢：容差窗口的
#: 占比固定，小节越短强拍越密、命中率越高，于是 2/4 会系统性压过 4/4
#: （实测正是如此）。换成 ±55ms 这种绝对容差，各拍号才在同一起跑线上。
_STRONG_TOL_SEC = 0.055

#: 提升度低于这个值就认为没有明显证据，保留默认 4/4。
_MIN_LIFT = 1.15


def infer_time_signature(
    notes: Iterable[object],
    ctx: NotationContext,
    candidates: Sequence[TimeSignature] = TIME_SIGNATURE_CANDIDATES,
) -> TimeSignature:
    """从音符起音在拍网格上的分布推断拍号。

    判据是「强拍命中率相对随机基线的**提升度**」。直接用命中率比较不同
    拍号并不公平，要除以随机情况下的期望命中率再比 —— 否则小节越短的
    拍号越占便宜（见 `_STRONG_TOL_SEC` 的说明）。

    证据不足时返回默认 4/4 —— 猜错拍号比不猜更糟，它会把小节线画得到处
    都是，用户反而更难看谱。
    """
    onsets: List[float] = []
    for n in notes:
        start = getattr(n, 'start', None)
        if start is not None:
            onsets.append(float(start))
    if len(onsets) < 8:
        return TimeSignature()

    import numpy as np
    values = np.asarray(onsets, dtype=np.float64)

    best = TimeSignature()
    best_lift = _MIN_LIFT
    for cand in candidates:
        probe = NotationContext(bpm=ctx.bpm, time_signature=cand,
                                phase=ctx.phase)
        bar = probe.bar_sec
        if bar <= 0:
            continue
        tol = _STRONG_TOL_SEC / bar
        if tol > 0.25:          # 小节短到窗口占了四分之一，没有区分力
            continue
        pos = np.mod(values - ctx.phase, bar) / bar
        strong = float(((pos < tol) | (pos > 1.0 - tol)).sum())
        # 小节中点是次强拍，权重给一半
        mid = float((np.abs(pos - 0.5) < tol).sum())
        hit = strong + 0.5 * mid
        expect = (2.0 * tol + 0.5 * 2.0 * tol) * len(onsets)
        lift = hit / expect if expect > 0 else 0.0
        if lift > best_lift:
            best_lift = lift
            best = cand
    return best


# ---------------------------------------------------------------------------
# 延音线
# ---------------------------------------------------------------------------

def split_ties(notes: Sequence[object], ctx: NotationContext,
               factory=None) -> List[object]:
    """把跨小节的音符在多条小节线上切开，用延音线连起来。

    乐谱上一次只能写满一个小节：一个 5 秒的音在 4/4、120BPM 下横跨两个
    半小节，必须写成「二分音符 + 延音线 + 附点二分音符」。不切开的话，
    小节线会穿过音符，谱面就不可读了。

    factory(pitch, start, duration, source) -> 新音符；默认复制原对象的类型。
    """
    bar = ctx.bar_sec
    if bar <= 0 or not notes:
        return list(notes)

    out: List[object] = []
    for n in notes:
        start = float(n.start)
        dur = float(n.duration)
        if dur <= 0:
            out.append(n)
            continue
        end = start + dur
        # 下一根小节线的位置
        first_bar = ctx.phase + (ctx.bar_index(start) + 1) * bar
        if end <= first_bar + 1e-9:
            out.append(n)
            continue
        cursor = start
        remaining = dur
        first = True
        while remaining > 1e-9:
            next_bar = ctx.phase + (ctx.bar_index(cursor) + 1) * bar
            piece = min(remaining, next_bar - cursor)
            if piece <= 1e-9:              # 浮点兜底：至少推进一小步
                piece = min(remaining, bar)
            out.append(_copy_note(n, cursor, piece, tie=not first, factory=factory))
            cursor += piece
            remaining -= piece
            first = False
    return out


def _copy_note(n, start: float, duration: float, tie: bool, factory=None):
    if factory is not None:
        return factory(n, start, duration, tie)
    from .score import Note
    return Note(
        pitch=n.pitch,
        start=round(start, 6),
        duration=round(duration, 6),
        velocity=getattr(n, 'velocity', 80),
        track=getattr(n, 'track', 0),
        phrase_end=getattr(n, 'phrase_end', False),
        tie=tie,
    )


# ---------------------------------------------------------------------------
# 与 Score 的互转
# ---------------------------------------------------------------------------

def context_from_score(score) -> NotationContext:
    """从 Score 读出乐谱上下文（缺失字段用默认值）。"""
    ts = TimeSignature(
        numerator=int(getattr(score, 'time_sig_num', 4) or 4),
        denominator=int(getattr(score, 'time_sig_den', 4) or 4),
    )
    return NotationContext(
        bpm=float(getattr(score, 'bpm', 120.0) or 120.0),
        time_signature=ts,
        phase=float(getattr(score, 'phase', 0.0) or 0.0),
        key=int(getattr(score, 'key', 0) or 0),
        scale=str(getattr(score, 'scale', 'major') or 'major'),
    )


def apply_context(score, ctx: NotationContext) -> None:
    """把上下文写回 Score。"""
    score.bpm = round(float(ctx.bpm), 4)
    score.time_sig_num = int(ctx.time_signature.numerator)
    score.time_sig_den = int(ctx.time_signature.denominator)
    score.phase = round(float(ctx.phase), 6)
    score.key = int(ctx.key)
    score.scale = str(ctx.scale)


def bar_lines(ctx: NotationContext, until: float) -> List[float]:
    """从 phase 开始到 until 为止的所有小节线时间。"""
    bar = ctx.bar_sec
    if bar <= 0:
        return []
    import math
    n = int(math.floor((until - ctx.phase) / bar)) + 1
    return [ctx.phase + i * bar for i in range(max(0, n) + 1)]


def summarize(notes: Sequence[object], ctx: NotationContext) -> dict:
    """谱面统计 —— 给界面显示，也方便诊断。"""
    if not notes:
        return {'notes': 0, 'bars': 0, 'span': 0.0, 'bar_sec': ctx.bar_sec}
    end = max(float(n.start) + float(n.duration) for n in notes)
    return {
        'notes': len(notes),
        'bars': ctx.bar_index(end) + 1,
        'span': end,
        'bar_sec': ctx.bar_sec,
        'bpm': ctx.bpm,
        'time_signature': str(ctx.time_signature),
    }
