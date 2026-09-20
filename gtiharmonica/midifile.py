"""纯 Python 标准 MIDI 文件解析器 —— 零第三方依赖。

只依赖标准库，因此 GUI 可以双击直接运行，不需要 pip install 任何东西。
如果环境里装了 mido，score.py 会优先用 mido（对边缘格式兼容性更好），
否则回落到这里。

支持：format 0/1、ticks-per-beat 计时、running status、变长量、
      note on/off、set_tempo 变速、多轨。
不支持：SMPTE 计时（会记录 warning 并按 500000 us/beat 处理）、
       RMID 容器、部分冷门 meta 事件（会被安全跳过）。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# 事件类型
NOTE_ON = 'note_on'
NOTE_OFF = 'note_off'
TEMPO = 'tempo'
META = 'meta'
OTHER = 'other'

DEFAULT_TEMPO = 500000       # us per quarter note = 120 BPM


class MidiError(ValueError):
    """MIDI 文件格式错误。"""


@dataclass
class MidiEvent:
    tick: int
    kind: str
    channel: int = 0
    note: int = 0
    velocity: int = 0
    tempo: int = 0
    meta_type: int = 0
    data: bytes = b''


@dataclass
class MidiTrack:
    events: List[MidiEvent] = field(default_factory=list)
    name: str = ''
    closed: bool = False          # 是否见到 end-of-track


@dataclass
class MidiData:
    fmt: int
    ticks_per_beat: int
    tracks: List[MidiTrack] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 原语
# ---------------------------------------------------------------------------

def _read_varint(data: bytes, pos: int) -> Tuple[int, int]:
    """读取 MIDI 变长量（最多 4 字节）。"""
    value = 0
    for _ in range(4):
        if pos >= len(data):
            raise MidiError('文件在变长量中途结束')
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise MidiError('变长量超过 4 字节')


def _read_chunk_header(data: bytes, pos: int) -> Tuple[bytes, int, int]:
    if pos + 8 > len(data):
        raise MidiError('块头不完整')
    tag = data[pos:pos + 4]
    (length,) = struct.unpack('>I', data[pos + 4:pos + 8])
    pos += 8
    if pos + length > len(data):
        raise MidiError('块 %r 声称长度 %d，超出文件末尾' % (tag, length))
    return tag, length, pos


# ---------------------------------------------------------------------------
# 轨道解析
# ---------------------------------------------------------------------------

def _parse_track(data: bytes, start: int, length: int,
                 warnings: List[str], index: int) -> MidiTrack:
    end = start + length
    pos = start
    tick = 0
    running: Optional[int] = None
    track = MidiTrack()

    while pos < end:
        delta, pos = _read_varint(data, pos)
        tick += delta
        if pos >= end:
            break

        status = data[pos]
        if status & 0x80:
            pos += 1
            if status < 0xF0:
                running = status
        else:
            if running is None:
                raise MidiError('轨道 %d 出现 running status 但没有前置状态字节' % index)
            status = running

        high = status & 0xF0

        # --- 通道事件 ---
        if status < 0xF0:
            channel = status & 0x0F
            if high in (0x80, 0x90):            # note off / note on
                if pos + 2 > end:
                    break
                note, velocity = data[pos], data[pos + 1]
                pos += 2
                if high == 0x90 and velocity > 0:
                    track.events.append(MidiEvent(tick, NOTE_ON, channel,
                                                  note, velocity))
                else:
                    track.events.append(MidiEvent(tick, NOTE_OFF, channel,
                                                  note, velocity))
            elif high in (0xA0, 0xB0, 0xE0):    # 2 字节数据
                pos += 2
            elif high in (0xC0, 0xD0):          # 1 字节数据
                pos += 1
            else:
                warnings.append('轨道 %d：未知状态字节 0x%02X，已跳过' % (index, status))
                break

        # --- meta 事件 ---
        elif status == 0xFF:
            if pos >= end:
                break
            meta_type = data[pos]
            pos += 1
            mlen, pos = _read_varint(data, pos)
            if pos + mlen > end:
                warnings.append('轨道 %d：meta 0x%02X 长度越界' % (index, meta_type))
                break
            payload = data[pos:pos + mlen]
            pos += mlen

            if meta_type == 0x51 and mlen == 3:
                tempo = (payload[0] << 16) | (payload[1] << 8) | payload[2]
                track.events.append(MidiEvent(tick, TEMPO, tempo=tempo))
            elif meta_type == 0x03:
                try:
                    track.name = payload.decode('utf8', 'replace').strip()
                except Exception:
                    pass
            elif meta_type == 0x2F:             # end of track
                track.closed = True
                break
            else:
                track.events.append(MidiEvent(tick, META, meta_type=meta_type,
                                              data=payload))

        # --- sysex ---
        elif status in (0xF0, 0xF7):
            slen, pos = _read_varint(data, pos)
            pos += slen

        else:
            warnings.append('轨道 %d：未知状态 0x%02X，已跳过' % (index, status))
            break

    return track


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def parse(data: bytes) -> MidiData:
    """解析完整的 SMF 字节流。"""
    warnings: List[str] = []

    if data[:4] == b'RIFF':
        raise MidiError('这是 RMID（RIFF 封装）文件，请先用工具转成标准 .mid')

    tag, length, pos = _read_chunk_header(data, 0)
    if tag != b'MThd':
        raise MidiError('不是标准 MIDI 文件（缺少 MThd 头）')
    if length < 6:
        raise MidiError('MThd 长度异常：%d' % length)

    fmt, ntrks, division = struct.unpack('>HHH', data[pos:pos + 6])
    pos += length

    if division & 0x8000:
        warnings.append('使用 SMPTE 计时，已按 500000 us/beat 近似处理')
        ticks_per_beat = 480
    else:
        ticks_per_beat = division or 480

    tracks: List[MidiTrack] = []
    for index in range(ntrks):
        if pos >= len(data):
            warnings.append('文件声称有 %d 个轨道，实际只有 %d 个' % (ntrks, len(tracks)))
            break
        try:
            tag, tlen, tpos = _read_chunk_header(data, pos)
        except MidiError as exc:
            warnings.append('轨道 %d 头损坏：%s' % (index, exc))
            break
        if tag != b'MTrk':
            warnings.append('轨道 %d 的标记是 %r，已跳过' % (index, tag))
            pos = tpos + tlen
            continue
        try:
            tracks.append(_parse_track(data, tpos, tlen, warnings, index))
        except MidiError as exc:
            warnings.append('轨道 %d 解析失败：%s' % (index, exc))
        pos = tpos + tlen

    if not tracks:
        raise MidiError('文件里没有可解析的轨道')

    return MidiData(fmt=fmt, ticks_per_beat=ticks_per_beat,
                    tracks=tracks, warnings=warnings)


def load(path: str) -> MidiData:
    with open(path, 'rb') as fh:
        return parse(fh.read())
