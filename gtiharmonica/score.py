"""曲谱数据模型与 MIDI 解析。

时间单位统一为**秒**（浮点），音高为 MIDI 编号 0..127。
支持 tempo 变化：从所有轨道收集 set_tempo 建立时间映射表。
"""
from __future__ import annotations

import bisect
import json
import os
from dataclasses import dataclass, field
from functools import reduce
from math import gcd
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass
class Note:
    """一个音符。start / duration 单位为秒。"""

    pitch: int
    start: float
    duration: float
    velocity: int = 80
    track: int = 0
    #: 编曲者标记的乐句结尾 —— 换气位置的主要依据。
    #: MIDI 没有这个信息，由编排阶段按时间间隔推断；
    #: 带格式标记的 JSON 曲谱会显式给出。
    phrase_end: bool = False
    #: 延音线的后半段：这个音是从上一小节「接过来」的，不是重新起音。
    #: 乐谱层把跨小节的音切开时产生（见 notation.split_ties）。演奏上它
    #: 仍然是一个音，只是谱面上必须断开才能画小节线。
    tie: bool = False

    @property
    def end(self) -> float:
        return self.start + self.duration

    def __repr__(self) -> str:
        from .instrument import note_name
        return 'Note(%s, %.3f+%.3f, tr%d)' % (
            note_name(self.pitch), self.start, self.duration, self.track)


@dataclass
class Score:
    """一首曲子的全部音符与元信息。"""

    title: str = 'untitled'
    notes: List[Note] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    source: Optional[str] = None

    # -- 乐谱上下文 -------------------------------------------------------
    #: 没有这些字段，notes 只是一串孤立的时间点：说不出这是第几小节、
    #: 这个音是几分音符、小节线该画在哪。见 notation.NotationContext。
    bpm: float = 120.0
    time_sig_num: int = 4
    time_sig_den: int = 4
    #: 第一条小节线的时间（秒）。节拍网格从这里铺开。
    phase: float = 0.0
    key: int = 0
    scale: str = 'major'

    def notation(self):
        """取出乐谱上下文（延迟导入，避免模块循环依赖）。"""
        from .notation import context_from_score
        return context_from_score(self)

    def __len__(self) -> int:
        return len(self.notes)

    @property
    def duration(self) -> float:
        return max((n.end for n in self.notes), default=0.0)

    def tracks(self) -> List[int]:
        return sorted({n.track for n in self.notes})

    def track_notes(self, track: Optional[int]) -> List[Note]:
        if track is None:
            return list(self.notes)
        return [n for n in self.notes if n.track == track]

    def track_summary(self) -> List[dict]:
        """每个音轨的音符数、音域、时长 —— 用于挑选主旋律。"""
        out = []
        for t in self.tracks():
            ns = self.track_notes(t)
            pitches = [n.pitch for n in ns]
            out.append({
                'track': t,
                'notes': len(ns),
                'pitch_min': min(pitches),
                'pitch_max': max(pitches),
                'pitch_span': max(pitches) - min(pitches),
                'duration': max(n.end for n in ns) - min(n.start for n in ns),
            })
        return out


# ---------------------------------------------------------------------------
# tempo 映射
# ---------------------------------------------------------------------------

class TempoMap:
    """把绝对 tick 换算成秒，支持曲中变速。"""

    def __init__(self, ticks_per_beat: int, points: Sequence[Tuple[int, int]]):
        # points: [(tick, tempo_us_per_beat), ...] 已排序
        self.tpb = ticks_per_beat
        pts = [(0, 500000)]
        for tick, tempo in sorted(points):
            if tick < 0 or tempo <= 0:
                continue
            if pts and pts[-1][0] == tick:
                pts[-1] = (tick, tempo)
            else:
                pts.append((tick, tempo))
        self._ticks = [p[0] for p in pts]
        self._tempos = [p[1] for p in pts]
        # 每段起点的累计秒数
        self._secs = [0.0]
        for i in range(1, len(pts)):
            dt = pts[i][0] - pts[i - 1][0]
            self._secs.append(self._secs[-1] + dt * pts[i - 1][1] / 1e6 / ticks_per_beat)

    def to_seconds(self, tick: int) -> float:
        idx = bisect.bisect_right(self._ticks, tick) - 1
        base_sec = self._secs[idx]
        base_tick = self._ticks[idx]
        return base_sec + (tick - base_tick) * self._tempos[idx] / 1e6 / self.tpb

    @property
    def changes(self) -> int:
        return len(self._ticks)


# ---------------------------------------------------------------------------
# MIDI 解析
# ---------------------------------------------------------------------------

def load_midi(path: str, merge_channels: bool = True) -> Score:
    """读取 MIDI 文件。

    优先使用 mido（边缘格式兼容性更好）；没有安装 mido 时自动回落到
    内置的纯标准库解析器，因此本工具可以零依赖运行。
    """
    try:
        import mido  # noqa: F401
    except ImportError:
        return _load_midi_builtin(path)
    try:
        return _load_midi_mido(path)
    except Exception:
        # mido 解析失败时再试内置解析器，避免因个别 meta 事件整首读不出来
        return _load_midi_builtin(path)


def _pair_notes(track_events, tempo: TempoMap, index: int,
                warnings: List[str]) -> List[Note]:
    """把 (tick, kind, note, velocity) 事件流配对成 Note 列表。"""
    active: Dict[Tuple[int, int], Tuple[int, int]] = {}
    notes: List[Note] = []
    for tick, kind, note, velocity, channel in track_events:
        sig = (note, channel)
        if kind == 'on':
            active[sig] = (tick, velocity)
        else:
            if sig in active:
                on_tick, vel = active.pop(sig)
                start = tempo.to_seconds(on_tick)
                end = tempo.to_seconds(tick)
                if end - start > 1e-4:
                    notes.append(Note(pitch=note, start=start,
                                      duration=end - start,
                                      velocity=vel, track=index))
    if active:
        warnings.append('轨道 %d 有 %d 个未闭合的音符，已忽略' % (index, len(active)))
    return notes


def _load_midi_mido(path: str) -> Score:
    import mido

    mid = mido.MidiFile(path)

    tempo_points: List[Tuple[int, int]] = []
    for track in mid.tracks:
        t = 0
        for msg in track:
            t += msg.time
            if msg.type == 'set_tempo':
                tempo_points.append((t, msg.tempo))
    tempo = TempoMap(mid.ticks_per_beat, tempo_points)
    bpm = _bpm_from_tempo_points(tempo_points)

    notes: List[Note] = []
    warnings: List[str] = []
    for index, track in enumerate(mid.tracks):
        tick = 0
        events = []
        for msg in track:
            tick += msg.time
            channel = getattr(msg, 'channel', 0)
            if msg.type == 'note_on' and msg.velocity > 0:
                events.append((tick, 'on', msg.note, msg.velocity, channel))
            elif msg.type == 'note_off' or (msg.type == 'note_on'
                                            and msg.velocity == 0):
                events.append((tick, 'off', msg.note, 0, channel))
        notes.extend(_pair_notes(events, tempo, index, warnings))

    return _finish_score(path, notes, warnings, bpm)


def _load_midi_builtin(path: str) -> Score:
    """纯标准库解析路径（midifile.py）。"""
    from . import midifile

    mid = midifile.load(path)

    tempo_points: List[Tuple[int, int]] = []
    for track in mid.tracks:
        for event in track.events:
            if event.kind == midifile.TEMPO:
                tempo_points.append((event.tick, event.tempo))
    tempo = TempoMap(mid.ticks_per_beat, tempo_points)
    bpm = _bpm_from_tempo_points(tempo_points)

    notes: List[Note] = []
    warnings: List[str] = list(mid.warnings)
    for index, track in enumerate(mid.tracks):
        events = []
        for event in track.events:
            if event.kind == midifile.NOTE_ON:
                events.append((event.tick, 'on', event.note, event.velocity,
                               event.channel))
            elif event.kind == midifile.NOTE_OFF:
                events.append((event.tick, 'off', event.note, 0, event.channel))
        notes.extend(_pair_notes(events, tempo, index, warnings))

    return _finish_score(path, notes, warnings, bpm)


def _bpm_from_tempo_points(tempo_points) -> float:
    """取生效时间最长的速度为全曲 BPM（多段变速时代表「主速度」）。"""
    if not tempo_points:
        return 120.0
    if len(tempo_points) == 1:
        return round(60e6 / tempo_points[0][1], 2)
    spans = []
    for (t0, us0), (t1, _) in zip(tempo_points, tempo_points[1:]):
        spans.append(((t1 - t0) * us0, us0))   # tick 跨度 × 该段速度 = 真实时长
    spans.append((10 ** 12 * tempo_points[-1][1], tempo_points[-1][1]))
    spans.sort(reverse=True)
    return round(60e6 / spans[0][1], 2)


def _finish_score(path: str, notes: List[Note], warnings: List[str],
                  bpm: float = 120.0) -> Score:
    notes.sort(key=lambda n: (n.start, n.pitch, n.track))
    if not notes:
        raise ValueError('MIDI 里没有可用的音符：%s' % path)
    title = os.path.splitext(os.path.basename(path))[0]
    return Score(title=title, notes=notes, warnings=warnings, source=path,
                 bpm=bpm)


# ---------------------------------------------------------------------------
# 自有 JSON 曲谱格式（便于手工编辑 / 定向微调）
# ---------------------------------------------------------------------------

def load_json_score(path: str) -> Score:
    """读取 JSON 曲谱。

    同时兼容两种写法：
      * 本工具的原生格式：{"title":.., "notes":[{...}]}
      * gtiartist-score-v1：{"format","title","tracks","notes":[...]}
        （带 phrase_end 与 tracks 乐器名，可直接沿用旧曲库）
    """
    with open(path, encoding='utf8') as fh:
        data = json.load(fh)
    notes = []
    for i, raw in enumerate(data.get('notes', [])):
        if isinstance(raw, dict):
            notes.append(Note(pitch=int(raw['pitch']),
                              start=float(raw['start']),
                              duration=float(raw.get('duration', 0.25)),
                              velocity=int(raw.get('velocity', 80)),
                              track=int(raw.get('track', 0)),
                              phrase_end=bool(raw.get('phrase_end', False)),
                              tie=bool(raw.get('tie', False))))
        else:                                # 简写：[pitch, start, duration]
            notes.append(Note(pitch=int(raw[0]), start=float(raw[1]),
                              duration=float(raw[2])))
    notes.sort(key=lambda n: (n.start, n.pitch))

    warnings = []
    tracks = data.get('tracks')
    if isinstance(tracks, dict) and tracks:
        named = ', '.join('%s=%s' % (k, v) for k, v in sorted(tracks.items()))
        warnings.append('音轨乐器：%s' % named)

    # 乐谱上下文。旧曲谱没有这些字段，一律用默认值兜住 —— 曲库里几十首
    # 老曲子都得继续能读，不能因为缺字段就报错。
    tempo = data.get('tempo') if isinstance(data.get('tempo'), dict) else {}
    ts = (data.get('time_signature')
          if isinstance(data.get('time_signature'), dict) else {})
    return Score(
        title=data.get('title', os.path.basename(path)),
        notes=notes, warnings=warnings, source=path,
        bpm=float(tempo.get('bpm', data.get('bpm', 120.0)) or 120.0),
        phase=float(tempo.get('beat_phase', data.get('phase', 0.0)) or 0.0),
        time_sig_num=int(ts.get('numerator', data.get('time_sig_num', 4)) or 4),
        time_sig_den=int(ts.get('denominator', data.get('time_sig_den', 4)) or 4),
        key=int(data.get('key', 0) or 0),
        scale=str(data.get('scale', 'major') or 'major'),
    )


def save_json_score(score: Score, path: str) -> None:
    data = {
        'format': 'gtiharmonica-score-v1',
        'title': score.title,
        'tempo': {'bpm': round(float(score.bpm), 4),
                  'beat_phase': round(float(score.phase), 6)},
        'time_signature': {'numerator': int(score.time_sig_num),
                           'denominator': int(score.time_sig_den)},
        'key': int(score.key),
        'scale': str(score.scale),
        'notes': [{'pitch': n.pitch, 'start': round(n.start, 6),
                   'duration': round(n.duration, 6),
                   'velocity': n.velocity, 'track': n.track,
                   'phrase_end': bool(n.phrase_end),
                   'tie': bool(n.tie)}
                  for n in score.notes],
    }
    with open(path, 'w', encoding='utf8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)


def load_score(path: str) -> Score:
    """按扩展名自动选择解析器。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.mid', '.midi'):
        return load_midi(path)
    if ext == '.json':
        return load_json_score(path)
    raise ValueError('不支持的曲谱格式：%s' % ext)


def grid_quality(notes: Sequence[Note]) -> str:
    """按音符起点的网格精度给曲谱定级：MIDI 级 / 量化 / 松散。

    MIDI 转谱的音符起点全部落在极细的公共网格上（1/480 拍刻度下的
    最大公约极小）；人工听记或重建节奏的谱，起点散乱、公共网格粗。
    这是「这份谱的节奏可信度」的快速提示，不评判音高对错 —— 《戒烟》
    的教训是错谱也可以网格很整齐，网格标注只回答「节奏是否机器级精确」。

    分级阈值按公共网格（拍）划分：
        <= 1/24 拍   MIDI 级（机器转谱，节奏逐音精确）
        <= 1/6  拍   量化（对过格子，细节可能被吸掉）
        其他         松散（听记/重建节奏，试听互证后再用）
    """
    if not notes:
        return '空谱'
    ticks = [round(n.start * 480) for n in notes]
    g = reduce(gcd, ticks) if len(ticks) > 1 else ticks[0]
    if g <= 0:
        return '松散'
    grid_beat = g / 480.0
    if grid_beat <= 1.0 / 24:
        return 'MIDI 级'
    if grid_beat <= 1.0 / 6:
        return '量化'
    return '松散'
