"""可编辑曲谱：编辑命令、撤销重做、吸附、碎片合并。

编辑器工作的音高是**已经定好、要弹出来的音高** —— 也就是编排结果里
`PlayStep.pitch`。这一点决定了应用编辑时不能再跑一遍 `arrange()`：
那个函数会做移调与八度折叠，对已经定好的音高再折一次就是错音。所以
编辑结果走 `arrange.arrange_from_notes()` —— 只重算指法、时间轴和换气。

三个东西是这个模块存在的理由：

    撤销重做  编辑必然试错，没有撤销的编辑器没人敢用。所有写操作都先
              压一份浅拷贝快照（Note 字段全是标量，浅拷贝就够，比
              deepcopy 快一个数量级）。
    吸附      手拖不可能拖到精确的 1/16 拍上。节拍网格吸附用乐谱上下文
              算，音阶吸附用调式算。
    碎片合并  转录把长音切成十几个几十毫秒的碎片时，框选 + 一次合并就能
              还原成整音 —— 这是长音识别不准时最实用的一条退路。
"""
from __future__ import annotations

import itertools
import math
import os
from copy import copy
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .notation import (NotationContext, TimeSignature, bar_lines,
                       duration_symbol, split_ties, summarize)
from .score import Note, Score, load_score, save_json_score

#: 短于这个时长的音符在游戏里可能漏读，编辑时不让它出现
MIN_NOTE_SEC = 0.02

#: 撤销栈深度。再深也没人翻得回去，占内存却不小。
UNDO_LIMIT = 64

#: 合并碎片时允许的最大缝隙（秒）。转录切出来的碎片通常只差几十毫秒，
#: 0.35 s 能吃掉这些缝，又不会把真正分开的两个音并起来。
DEFAULT_MERGE_GAP = 0.35

#: 音符标识的发号器（模块级，保证同一进程里不会撞号）
_UID = itertools.count(1)


def ensure_uids(notes: Iterable[Note]) -> None:
    """给还没有 uid 的音符分配一个稳定标识。

    选中状态必须跨「排序 / 撤销 / 重做 / 合并」活下来，而对象身份做不到：

      * 撤销是把整个列表换成快照副本，旧对象直接消失；
      * 合并会删掉对象；
      * 每次编辑后列表都要重排，下标也跟着变。

    所以每个音符挂一个 uid。`copy()` 会连它一起复制，于是撤销回来之后
    选中的还是同一个音 —— 这一点不解决，用户会遇到「选中、撤销，选区空了」。
    """
    for n in notes:
        if getattr(n, 'uid', None) is None:
            n.uid = next(_UID)


# ---------------------------------------------------------------------------
# 调式
# ---------------------------------------------------------------------------

#: 音阶音程表（相对主音的半音数）
SCALES: Dict[str, Tuple[int, ...]] = {
    'major': (0, 2, 4, 5, 7, 9, 11),
    'minor': (0, 2, 3, 5, 7, 8, 10),
    'harmonic_minor': (0, 2, 3, 5, 7, 8, 11),
    'pentatonic': (0, 2, 4, 7, 9),
    'minor_pentatonic': (0, 3, 5, 7, 10),
    'chromatic': tuple(range(12)),
}

SCALE_LABELS = {
    'major': '大调',
    'minor': '自然小调',
    'harmonic_minor': '和声小调',
    'pentatonic': '大调五声',
    'minor_pentatonic': '小调五声',
    'chromatic': '半音（不吸附）',
}


def scale_pitch_classes(key: int, scale: str) -> frozenset:
    """这个调式的 12 个音级里，哪些属于调内。"""
    steps = SCALES.get(scale)
    if steps is None or scale == 'chromatic':
        return frozenset(range(12))
    return frozenset((int(key) + s) % 12 for s in steps)


def nearest_in_scale(pitch: int, key: int, scale: str) -> int:
    """离 pitch 最近的调内音。平局取低音 —— 往下走听感更稳。"""
    pcs = scale_pitch_classes(key, scale)
    if len(pcs) == 12:
        return int(pitch)
    best, best_d = int(pitch), 99
    for cand in range(int(pitch) - 6, int(pitch) + 7):
        if 0 <= cand <= 127 and (cand % 12) in pcs:
            d = abs(cand - pitch)
            if d < best_d:
                best, best_d = cand, d
    return best


# ---------------------------------------------------------------------------
# 编辑文档
# ---------------------------------------------------------------------------

class EditDoc:
    """一份可编辑的曲谱。

    与 `Score` 的区别只有一个：这里的音符是**最终音高**，不再经过移调 /
    八度折叠。其余（小节、拍号、时值、延音线）都跟 Score 一致。
    """

    def __init__(self, notes: Optional[Iterable[Note]] = None,
                 title: str = 'untitled',
                 bpm: float = 120.0, time_sig_num: int = 4,
                 time_sig_den: int = 4, phase: float = 0.0,
                 key: int = 0, scale: str = 'major',
                 source: Optional[str] = None):
        self.notes: List[Note] = [copy(n) for n in (notes or [])]
        ensure_uids(self.notes)
        self.title = title
        self.bpm = float(bpm) or 120.0
        self.time_sig_num = int(time_sig_num) or 4
        self.time_sig_den = int(time_sig_den) or 4
        self.phase = float(phase)
        self.key = int(key)
        self.scale = str(scale or 'major')
        self.source = source
        #: 有没有未保存的改动
        self.dirty = False

        self._undo: List[list] = []
        self._redo: List[list] = []
        self._sort()

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------

    @classmethod
    def from_score(cls, score: Score) -> 'EditDoc':
        return cls(notes=score.notes, title=score.title,
                   bpm=score.bpm, time_sig_num=score.time_sig_num,
                   time_sig_den=score.time_sig_den, phase=score.phase,
                   key=score.key, scale=score.scale, source=score.source)

    @classmethod
    def from_steps(cls, steps: Sequence[object], title: str = 'untitled',
                   ctx: Optional[NotationContext] = None,
                   source: Optional[str] = None) -> 'EditDoc':
        """从编排结果建立编辑文档 —— 界面上看到什么就编辑什么。"""
        notes = [Note(pitch=int(s.pitch), start=float(s.start),
                      duration=float(s.duration),
                      velocity=int(getattr(s, 'velocity', 85)),
                      track=int(getattr(s, 'track', 0)))
                 for s in steps]
        c = ctx or NotationContext()
        return cls(notes=notes, title=title, bpm=c.bpm,
                   time_sig_num=c.time_signature.numerator,
                   time_sig_den=c.time_signature.denominator,
                   phase=c.phase, key=c.key, scale=c.scale, source=source)

    @classmethod
    def load(cls, path: str) -> 'EditDoc':
        return cls.from_score(load_score(path))

    def to_score(self) -> Score:
        return Score(title=self.title, notes=[copy(n) for n in self.notes],
                     source=self.source, bpm=self.bpm,
                     time_sig_num=self.time_sig_num,
                     time_sig_den=self.time_sig_den, phase=self.phase,
                     key=self.key, scale=self.scale)

    def save(self, path: str) -> str:
        """保存到 path，返回真正写入的路径（非 .json 后缀会被补上）。"""
        path = save_json_score(self.to_score(), path)
        self.source = path
        self.dirty = False
        return path

    def context(self) -> NotationContext:
        return NotationContext(
            bpm=self.bpm,
            time_signature=TimeSignature(self.time_sig_num, self.time_sig_den),
            phase=self.phase, key=self.key, scale=self.scale)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.notes)

    @property
    def duration(self) -> float:
        return max((n.end for n in self.notes), default=0.0)

    def pitch_range(self) -> Tuple[int, int]:
        if not self.notes:
            return 60, 72
        pitches = [n.pitch for n in self.notes]
        return min(pitches), max(pitches)

    def bounds(self) -> Tuple[float, float, int, int]:
        """(最早起点, 最晚终点, 最低音, 最高音) —— 用于自动取景。"""
        if not self.notes:
            return 0.0, 1.0, 60, 72
        lo, hi = self.pitch_range()
        start = min(n.start for n in self.notes)
        return start, max(n.end for n in self.notes), lo, hi

    def bar_lines(self, until: Optional[float] = None) -> List[float]:
        """到 until 为止的小节线（含两端，含第 0 小节线）。

        notation.bar_lines 会多给一根（它服务于「把可见区域铺满」的绘图
        场景）。编辑器要的是「到某个时间点为止有哪些线」，所以在这里裁掉
        超出 until 的那根，免得界面上凭空多画一条。
        """
        end = self.duration if until is None else float(until)
        return [t for t in bar_lines(self.context(), end) if t <= end + 1e-9]

    def summarize(self) -> dict:
        return summarize(self.notes, self.context())

    def duration_name(self, note: Note) -> str:
        return duration_symbol(self.context().beats(note.duration))

    def tie_split(self) -> List[Note]:
        """按延音线规则展开（跨小节的音切成带 tie 的多段）。"""
        return split_ties(self.notes, self.context())

    def notes_in_rect(self, t0: float, t1: float,
                      pitch_lo: int, pitch_hi: int) -> List[int]:
        """框选：与矩形相交的音符（相交而不是完全包含，选起来才不费劲）。"""
        if t1 < t0:
            t0, t1 = t1, t0
        if pitch_hi < pitch_lo:
            pitch_lo, pitch_hi = pitch_hi, pitch_lo
        out = []
        for i, n in enumerate(self.notes):
            if n.end < t0 or n.start > t1:
                continue
            if n.pitch < pitch_lo or n.pitch > pitch_hi:
                continue
            out.append(i)
        return out

    def hit(self, t: float, pitch: int, tol_t: float = 0.05,
            edge: Optional[str] = None, tol_edge_t: float = 0.06) -> int:
        """命中最靠上的那个音符，找不到返回 -1。

        edge='left'/'right' 时只认对应的边缘附近 —— 拖动改时值时用，
        否则一按就把音符拖走了。
        """
        best = -1
        best_d = 1e9
        for i, n in enumerate(self.notes):
            if n.pitch != pitch:
                continue
            if edge == 'left':
                if abs(t - n.start) > tol_edge_t:
                    continue
            elif edge == 'right':
                if abs(t - n.end) > tol_edge_t:
                    continue
            elif not (n.start - tol_t <= t <= n.end + tol_t):
                continue
            d = abs(t - (n.start + n.end) / 2.0)
            if d < best_d:
                best, best_d = i, d
        return best

    # ------------------------------------------------------------------
    # 撤销 / 重做
    # ------------------------------------------------------------------

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def _begin(self) -> None:
        """写操作前落一份快照。

        Note 的字段全是标量，逐字段浅拷贝足够；用 deepcopy 在几百个音符
        上是没必要的开销。
        """
        self._undo.append([copy(n) for n in self.notes])
        if len(self._undo) > UNDO_LIMIT:
            self._undo.pop(0)
        self._redo.clear()
        self.dirty = True

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append([copy(n) for n in self.notes])
        self.notes = self._undo.pop()
        self._sort()
        self.dirty = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append([copy(n) for n in self.notes])
        self.notes = self._redo.pop()
        self._sort()
        self.dirty = True
        return True

    def clear_history(self) -> None:
        self._undo.clear()
        self._redo.clear()

    # -- 拖动事务 ---------------------------------------------------------
    # 拖动是连续的：鼠标每移动一像素就改一次音符。如果每次改动都压一份
    # 快照，拖一个音就能把撤销栈塞满，用户按一次撤销只退回一个像素。
    # 所以拖动的模式是：按下时 begin_drag() 存一份快照；移动过程中直接
    # 改音符对象（不入栈）；松开时 commit_drag() 把那一份快照整体入栈 ——
    # 整次拖动算作一步。

    def begin_drag(self) -> None:
        self._pending = [copy(n) for n in self.notes]

    def commit_drag(self) -> bool:
        """结束拖动并把这一步写入撤销栈。没有实际变化就不入栈。"""
        pending = getattr(self, '_pending', None)
        self._pending = None
        if pending is None:
            return False
        same = (len(pending) == len(self.notes) and all(
            a.pitch == b.pitch and abs(a.start - b.start) < 1e-9
            and abs(a.duration - b.duration) < 1e-9
            for a, b in zip(pending, self.notes)))
        if same:
            return False
        self._undo.append(pending)
        if len(self._undo) > UNDO_LIMIT:
            self._undo.pop(0)
        self._redo.clear()
        self.dirty = True
        self._sort()
        return True

    def cancel_drag(self) -> None:
        """放弃这次拖动，恢复按下时的状态。"""
        pending = getattr(self, '_pending', None)
        self._pending = None
        if pending is not None:
            self.notes = pending
            self._sort()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _sort(self) -> None:
        self.notes.sort(key=lambda n: (n.start, n.pitch, n.duration))

    def _indices_of(self, targets: Sequence[Note]) -> List[int]:
        """排序后返回这些音符的新下标。编辑会重排列表，下标必须重算。"""
        self._sort()
        pos = {n.uid: i for i, n in enumerate(self.notes)}
        return sorted(pos[t.uid] for t in targets if t.uid in pos)

    def _pick(self, indices: Iterable[int]) -> List[Note]:
        out = []
        for i in indices:
            if 0 <= i < len(self.notes):
                out.append(self.notes[i])
        # 去重：调用方可能传来重复下标
        seen = set()
        uniq = []
        for n in out:
            if n.uid in seen:
                continue
            seen.add(n.uid)
            uniq.append(n)
        return uniq

    # ------------------------------------------------------------------
    # 编辑操作
    # ------------------------------------------------------------------

    def move(self, indices: Iterable[int], dtime: float = 0.0,
             dpitch: int = 0, snap: Optional[float] = None,
             min_start: float = 0.0) -> List[int]:
        """平移：时间与音高。snap 是吸附网格（以四分音符为单位）。"""
        picked = self._pick(indices)
        if not picked or (abs(dtime) < 1e-9 and dpitch == 0):
            return self._indices_of(picked)
        self._begin()
        ctx = self.context()

        # 先算出整组的最小起点，整体平移不能把任何一个音推到负时间
        earliest = min(n.start for n in picked)
        if earliest + dtime < min_start:
            dtime = min_start - earliest

        for n in picked:
            target = n.start + dtime
            if snap:
                target = ctx.snap(target, snap)
            n.start = max(round(target, 6), min_start)
            if dpitch:
                n.pitch = max(0, min(127, n.pitch + int(dpitch)))
        return self._indices_of(picked)

    def resize(self, indices: Iterable[int], dtime: float,
               edge: str = 'right', snap: Optional[float] = None) -> List[int]:
        """改时值。

        edge='right' 只动终点（起点不动），'left' 只动起点（终点不动）。
        拖左边缘时终点必须钉死，否则用户想把一个音往前拉长，结果整个音
        往后跑，听感全变了。
        """
        picked = self._pick(indices)
        if not picked or abs(dtime) < 1e-9:
            return self._indices_of(picked)
        self._begin()
        ctx = self.context()

        for n in picked:
            if edge == 'left':
                end = n.end
                target = n.start + dtime
                if snap:
                    target = ctx.snap(target, snap)
                target = min(target, end - MIN_NOTE_SEC)
                n.start = max(round(target, 6), 0.0)
                n.duration = round(end - n.start, 6)
            else:
                target = n.end + dtime
                if snap:
                    target = ctx.snap(target, snap)
                n.duration = max(round(target - n.start, 6), MIN_NOTE_SEC)
        return self._indices_of(picked)

    def set_duration(self, indices: Iterable[int], duration: float,
                     snap: Optional[float] = None) -> List[int]:
        picked = self._pick(indices)
        if not picked:
            return []
        self._begin()
        ctx = self.context()
        for n in picked:
            d = max(float(duration), MIN_NOTE_SEC)
            if snap:
                d = max(ctx.snap_beats(ctx.beats(d), snap), snap) * ctx.beat_sec
            n.duration = round(max(d, MIN_NOTE_SEC), 6)
        return self._indices_of(picked)

    def set_pitch(self, indices: Iterable[int], pitch: int) -> List[int]:
        picked = self._pick(indices)
        if not picked:
            return []
        self._begin()
        for n in picked:
            n.pitch = max(0, min(127, int(pitch)))
        return self._indices_of(picked)

    def transpose(self, indices: Iterable[int], semitones: int) -> List[int]:
        return self.move(indices, 0.0, semitones)

    def insert(self, pitch: int, start: float, duration: float = 0.25) -> int:
        """插入一个音符，返回它的下标。"""
        self._begin()
        note = Note(pitch=max(0, min(127, int(pitch))),
                    start=max(round(float(start), 6), 0.0),
                    duration=max(round(float(duration), 6), MIN_NOTE_SEC),
                    velocity=85, track=0)
        note.uid = next(_UID)
        self.notes.append(note)
        idx = self._indices_of([note])
        return idx[0] if idx else -1

    def delete(self, indices: Iterable[int]) -> int:
        picked = self._pick(indices)
        if not picked:
            return 0
        self._begin()
        drop = {n.uid for n in picked}
        self.notes = [n for n in self.notes if n.uid not in drop]
        return len(picked)

    def cut_time(self, a: float, b: float) -> int:
        """剪掉 [a, b] 这段时间：段内音符删除，后面的音符整体前移补位。

        和 delete() 的区别：delete 只删音符、时间轴不变，留下空白；
        这里把时间本身剪掉，时间轴缩短（剪辑软件的 ripple delete）。
        返回删除的音符数。可整体撤销。
        """
        a, b = min(a, b), max(a, b)
        if b - a < 1e-9:
            return 0
        drop = {n.uid for n in self.notes if n.start < b and n.end > a}
        if not drop and not any(n.start >= b for n in self.notes):
            return 0
        self._begin()
        span = b - a
        notes = []
        for n in self.notes:
            if n.uid in drop:
                continue
            if n.start >= b - 1e-9:
                n.start = round(n.start - span, 6)
            notes.append(n)
        self.notes = notes
        return len(drop)

    def insert_time(self, at: float, gap: float) -> int:
        """在 at 处插入 gap 秒空白：起点在 at 之后的音符整体右移。

        cut_time 的镜像操作 —— 「这里想多空一拍再进唱」「把后面整段
        往后挪一点」时用。返回被移动的音符数。可整体撤销。
        """
        gap = float(gap)
        if gap < 0.05:
            return 0
        moved = sum(1 for n in self.notes if n.start >= at - 1e-9)
        if not moved:
            return 0
        self._begin()
        for n in self.notes:
            if n.start >= at - 1e-9:
                n.start = round(n.start + gap, 6)
        return moved

    def insert_scale(self, at: float, direction: str = 'updown',
                     note_beats: float = 0.5, octaves: int = 1,
                     base_pitch: Optional[int] = None) -> int:
        """在 at 处插入一段调内音阶（上行 / 下行 / 上下行）。

        音级取自 doc.key + doc.scale；起始音默认取**不高于全曲中位
        音高的调内主音** —— 插出来的音阶和曲子在同一音区。返回插入
        的音符数。整段一次快照，可整体撤销。
        """
        note_beats = max(float(note_beats), 0.125)
        step = self.context().beat_sec * note_beats
        if base_pitch is None:
            pitches = sorted(n.pitch for n in self.notes) or [72]
            median = pitches[len(pitches) // 2]
            tonic_pc = int(self.key) % 12
            # 起始音必须是**主音**（音阶步进的基准），八度取不高于中位音
            base = tonic_pc + 12 * max((median - tonic_pc) // 12, 0)
        else:
            base = max(0, min(127, int(base_pitch)))
        steps = SCALES.get(self.scale) or SCALES['major']
        seq = []
        for o in range(max(1, int(octaves))):
            for s in steps:
                seq.append(base + 12 * o + s)
        seq.append(base + 12 * int(octaves))          # 顶音收束
        if direction == 'down':
            seq = list(reversed(seq))
        elif direction == 'updown':
            seq = seq + list(reversed(seq[1:]))       # 顶音不重复
        if not seq:
            return 0
        self._begin()
        t = at
        for p in seq:
            n = Note(pitch=max(0, min(127, int(p))),
                     start=round(t, 6), duration=round(step, 6),
                     velocity=85, track=0)
            n.uid = next(_UID)
            self.notes.append(n)
            t += step
        return len(seq)

    def split(self, index: int, at: float) -> List[int]:
        """在 at（秒）处把一个音切成两半，后半段标成延音。"""
        if not (0 <= index < len(self.notes)):
            return []
        n = self.notes[index]
        if not (n.start + MIN_NOTE_SEC <= at <= n.end - MIN_NOTE_SEC):
            return [index]
        self._begin()
        right = copy(n)
        right.uid = next(_UID)          # 切出来的是新音符，不能继承 uid
        right.start = round(float(at), 6)
        right.duration = round(n.end - at, 6)
        right.tie = True
        n.duration = round(at - n.start, 6)
        n.tie = False
        self.notes.append(right)
        return self._indices_of([n, right])

    def merge(self, indices: Iterable[int],
              max_gap: float = DEFAULT_MERGE_GAP,
              by_pitch: bool = True,
              pitch_tol: int = 0) -> List[int]:
        """把碎片音符合并成长音 —— 编辑器里最常用的一步。

        转录把一 sustaining 的音切成十几段时，这些碎片有两个特征：起止首尾
        相接（缝隙通常几十毫秒），音高相同。所以默认按「时间相邻**且**
        音高相同」分组，每组并成一个音：起点取组内最早，终点取组内最晚，
        中间那些缝一并吃掉。

        by_pitch=False 时忽略音高，整段选中的音符并成一个音 —— 适合
        「这段就是一整句长音」的场合。

        音高取组内**占时间最长**的那个，而不是按数量投票：碎片里偶尔混进
        一两个错音时，按时长投票不会被它们带偏。
        """
        picked = self._pick(indices)
        if len(picked) < 2:
            return self._indices_of(picked)
        self._begin()

        ordered = sorted(picked, key=lambda n: (n.start, n.duration))
        groups: List[List[Note]] = [[ordered[0]]]
        for n in ordered[1:]:
            prev = groups[-1][-1]
            gap = n.start - prev.end
            same = (not by_pitch) or (abs(n.pitch - prev.pitch) <= pitch_tol)
            if gap <= max_gap and same:
                groups[-1].append(n)
            else:
                groups.append([n])

        kept: List[Note] = []
        for group in groups:
            if len(group) == 1:
                kept.append(group[0])
                continue
            start = min(n.start for n in group)
            end = max(n.end for n in group)
            best = max(group, key=lambda n: (n.duration, -n.pitch))
            for n in group:
                if n is not best:
                    self.notes.remove(n)
            best.start = round(start, 6)
            best.duration = round(max(end - start, MIN_NOTE_SEC), 6)
            best.tie = False            # 并成一整音，不再是延音片段
            best.phrase_end = False
            kept.append(best)
        return self._indices_of(kept)

    def quantize(self, indices: Iterable[int], grid: float = 0.25,
                 durations: bool = True) -> List[int]:
        """把起点和时值吸附到节拍网格（grid 以四分音符为单位）。"""
        picked = self._pick(indices)
        if not picked or grid <= 0:
            return self._indices_of(picked)
        self._begin()
        ctx = self.context()
        step = ctx.beat_sec * grid
        for n in picked:
            n.start = round(max(ctx.snap(n.start, grid), 0.0), 6)
            if durations:
                beats = max(ctx.snap_beats(ctx.beats(n.duration), grid), grid)
                n.duration = round(max(beats * ctx.beat_sec, MIN_NOTE_SEC), 6)
        # 吸附后可能有音符重到同一位置，保持稳定排序即可
        return self._indices_of(picked)

    def snap_to_scale(self, indices: Iterable[int]) -> List[int]:
        """把音高吸到调内 —— 转录结果常见的 ±1 半音漂移。"""
        picked = self._pick(indices)
        if not picked or self.scale == 'chromatic':
            return self._indices_of(picked)
        self._begin()
        for n in picked:
            n.pitch = nearest_in_scale(n.pitch, self.key, self.scale)
        return self._indices_of(picked)

    def fix_overlaps(self, indices: Optional[Iterable[int]] = None,
                     policy: str = 'trim') -> int:
        """同一音高上时间重叠的音符：游戏一次只能按一个键。

        policy='trim' 把前一个音裁到后一个音的起点；'shortest' 直接删掉
        被完全盖住的那个。返回处理的音符数。
        """
        items = (self._pick(indices) if indices is not None else list(self.notes))
        if not items:
            return 0
        targets = set(n.uid for n in items)
        self._begin()
        touched = 0
        by_pitch: Dict[int, List[Note]] = {}
        for n in self.notes:
            if n.uid in targets:
                by_pitch.setdefault(n.pitch, []).append(n)
        for pitch, group in by_pitch.items():
            group.sort(key=lambda n: (n.start, -n.duration))
            for a, b in zip(group, group[1:]):
                if b.start >= a.end - 1e-9:
                    continue
                touched += 1
                if b.start - a.start < MIN_NOTE_SEC:
                    # 起点几乎重合：留长的那个
                    victim = a if a.duration <= b.duration else b
                    if victim in self.notes:
                        self.notes.remove(victim)
                    continue
                if policy == 'shortest' and b.end >= a.end:
                    if a in self.notes:
                        self.notes.remove(a)
                    continue
                a.duration = round(max(b.start - a.start, MIN_NOTE_SEC), 6)
        return touched

    def set_context(self, bpm: Optional[float] = None,
                    time_sig: Optional[TimeSignature] = None,
                    phase: Optional[float] = None,
                    key: Optional[int] = None,
                    scale: Optional[str] = None) -> None:
        """改乐谱上下文。会影响小节线、时值名称和吸附网格。

        这里也要记快照：改拍号会让整首曲子的小节线重排，是实打实的
        编辑动作，不该变成撤销不了的一步。
        """
        self._begin()
        if bpm is not None:
            self.bpm = max(float(bpm), 1.0)
        if time_sig is not None:
            time_sig.validate()
            self.time_sig_num = int(time_sig.numerator)
            self.time_sig_den = int(time_sig.denominator)
        if phase is not None:
            self.phase = float(phase)
        if key is not None:
            self.key = int(key) % 12
        if scale is not None and scale in SCALES:
            self.scale = str(scale)

    def realign_phase(self) -> float:
        """把第一条小节线对齐到第一个音 —— 转录出的 phase 常常偏一点。

        返回新的 phase。这是把「小节线穿在音符中间」这种难看谱面救回来的
        最简单办法。
        """
        if not self.notes:
            return self.phase
        first = min(n.start for n in self.notes)
        self._begin()
        self.phase = round(first, 6)
        return self.phase


# ---------------------------------------------------------------------------
# 便捷入口
# ---------------------------------------------------------------------------

def merge_fragments(doc: EditDoc, indices: Optional[Iterable[int]] = None,
                    max_gap: float = DEFAULT_MERGE_GAP) -> List[int]:
    """把整首（或选中）的碎片音符合并成长音。

    这是「长音被识别成一串短音」最直接的补救：转录结果全选后调一次，
    同一个音高上首尾相接的碎片自动归并成长音。返回改动后的音符下标。
    """
    idx = list(range(len(doc.notes))) if indices is None else list(indices)
    return doc.merge(idx, max_gap=max_gap, by_pitch=True)


def fragment_report(doc: EditDoc, max_gap: float = DEFAULT_MERGE_GAP) -> dict:
    """统计有多少碎片可以被合并 —— 界面上一句话告诉用户值不值得点。"""
    groups = 0
    extra = 0
    ordered = sorted(doc.notes, key=lambda n: (n.pitch, n.start))
    run = 1
    for prev, n in zip(ordered, ordered[1:]):
        if n.pitch == prev.pitch and (n.start - prev.end) <= max_gap:
            run += 1
        else:
            if run >= 2:
                groups += 1
                extra += run - 1
            run = 1
    if run >= 2:
        groups += 1
        extra += run - 1
    return {'groups': groups, 'extra': extra, 'notes': len(doc.notes)}
