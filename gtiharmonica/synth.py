"""口琴音色合成与本机试听 —— 纯标准库，零第三方音频依赖。

用途：不开游戏就能听到「编排之后实际会发出什么声音」，
方便选曲、挑音轨、调 gate / 换气参数。

实现要点
--------
1. **波表合成**：把谐波叠加预先算成一个周期的波形表（4096 点），
   渲染时每个采样只做一次查表，比逐采样算 sin 快两个数量级。
2. **分块颤音**：颤音每 64 个采样更新一次，而不是每个采样都算 sin。
3. **WAV 引用持有**：winsound 的 SND_MEMORY 是异步播放，
   缓冲区必须在播放期间保持存活，否则会读到已回收的内存。
"""
from __future__ import annotations

import array
import atexit
import io
import math
import os
import shutil
import struct
import tempfile
import wave
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_SAMPLE_RATE = 22050
MAX_SECONDS = 900          # 试听渲染上限，防内存爆掉
TABLE_SIZE = 4096
TABLE_MASK = TABLE_SIZE - 1
VIBRATO_BLOCK = 64         # 颤音更新块大小（采样数）


# ---------------------------------------------------------------------------
# 音色
# ---------------------------------------------------------------------------

@dataclass
class Timbre:
    """口琴音色参数。

    口琴是自由簧乐器：谐波丰富、起音快、衰减慢、带轻微颤音。
    这里用「奇偶谐波递减 + 快起音 + 慢颤音」来近似。
    """

    harmonics: int = 10             # 叠加的谐波数
    rolloff: float = 1.25           # 谐波幅度衰减指数（越大越柔和）
    odd_boost: float = 1.18         # 奇次谐波增强（簧片音色特征）
    attack: float = 0.006           # 起音时间（秒）
    release: float = 0.040          # 释放时间（秒）
    vibrato_hz: float = 5.2         # 颤音频率
    vibrato_depth: float = 0.0035   # 颤音深度（相对音高）
    vibrato_delay: float = 0.18     # 颤音渐入时间（秒）
    gain: float = 0.78              # 总输出增益

    @staticmethod
    def soft() -> 'Timbre':
        """柔和音色，适合长时间试听。"""
        return Timbre(harmonics=7, rolloff=1.75, odd_boost=1.05,
                      gain=0.68, vibrato_depth=0.0022)

    @staticmethod
    def bright() -> 'Timbre':
        """明亮音色，接近游戏里口琴的穿透感。"""
        return Timbre(harmonics=14, rolloff=0.95, odd_boost=1.32, gain=0.66)

    def key(self) -> tuple:
        return (self.harmonics, round(self.rolloff, 3),
                round(self.odd_boost, 3), round(self.gain, 3))


_TABLE_CACHE: Dict[tuple, array.array] = {}


def build_wavetable(timbre: Timbre) -> array.array:
    """把一个周期的谐波叠加预计算成波形表（带缓存）。"""
    key = timbre.key()
    cached = _TABLE_CACHE.get(key)
    if cached is not None:
        return cached

    table = [0.0] * TABLE_SIZE
    for h in range(1, timbre.harmonics + 1):
        amp = 1.0 / (h ** timbre.rolloff)
        if h % 2 == 1:
            amp *= timbre.odd_boost
        step = 2.0 * math.pi * h / TABLE_SIZE
        for i in range(TABLE_SIZE):
            table[i] += amp * math.sin(step * i)

    peak = max(abs(v) for v in table) or 1.0
    result = array.array('d', (v / peak for v in table))
    _TABLE_CACHE[key] = result
    return result


def midi_to_hz(pitch: int) -> float:
    """MIDI 音高 -> 频率（Hz），A4 = 440。"""
    return 440.0 * (2.0 ** ((pitch - 69) / 12.0))


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def render(notes: Sequence[Tuple[int, float, float]],
           sample_rate: int = DEFAULT_SAMPLE_RATE,
           timbre: Optional[Timbre] = None,
           tail: float = 0.25) -> bytes:
    """把 [(pitch, start, duration), ...] 渲染成完整 WAV 字节。

    注意：这是**离线渲染**，直接使用编排结果的精确时间轴，
    因此听到的效果与实际发送的按键序列一一对应。
    """
    tb = timbre or Timbre()
    if not notes:
        return _pack_wav(b'\x00\x00' * 32, sample_rate)

    end = min(max(s + d for _, s, d in notes) + tail, MAX_SECONDS)
    total = int(end * sample_rate)
    if total <= 0:
        return _pack_wav(b'\x00\x00' * 32, sample_rate)

    buf = array.array('d', bytes(8 * total))       # 零填充的 float64
    table = build_wavetable(tb)
    two_pi = 2.0 * math.pi

    for pitch, start, duration in notes:
        if duration <= 0 or start >= end:
            continue
        freq = midi_to_hz(pitch)
        if freq <= 20.0 or freq > sample_rate / 2.2:
            continue

        i0 = int(start * sample_rate)
        i1 = min(total, int((start + duration) * sample_rate))
        n = i1 - i0
        if n <= 0:
            continue

        # 包络
        atk_n = max(1, min(int(tb.attack * sample_rate), n // 2, 1))
        rel_n = max(1, min(int(tb.release * sample_rate), n // 2, 1))
        atk_n = min(max(int(tb.attack * sample_rate), 1), max(n // 2, 1))
        rel_n = min(max(int(tb.release * sample_rate), 1), max(n // 2, 1))

        # 颤音渐入
        vib_n = max(1, int(tb.vibrato_delay * sample_rate))
        depth = tb.vibrato_depth
        vib_step = two_pi * tb.vibrato_hz / sample_rate

        base_step = freq / sample_rate * TABLE_SIZE   # 查表步进
        phase = 0.0
        block = VIBRATO_BLOCK
        i = 0
        while i < n:
            # 每块更新一次颤音，避免逐采样调用 sin
            t = i / sample_rate if i < vib_n else None
            d = depth * (i / vib_n) if i < vib_n else depth
            vib = 1.0 + d * math.sin(vib_step * i)
            step = base_step * vib
            upper = min(i + block, n)
            for j in range(i, upper):
                # 包络
                if j < atk_n:
                    env = j / atk_n
                elif j > n - rel_n:
                    env = (n - j) / rel_n
                else:
                    env = 1.0
                if env <= 0.0:
                    phase += step
                    if phase >= TABLE_SIZE * 64:
                        phase -= TABLE_SIZE * 64
                    continue
                buf[i0 + j] += table[int(phase) & TABLE_MASK] * env
                phase += step
                if phase >= TABLE_SIZE * 64:
                    phase -= TABLE_SIZE * 64
            i = upper

    # --- 归一化 + 转 16bit ---
    peak = 0.0
    for v in buf:
        if v > peak:
            peak = v
        elif -v > peak:
            peak = -v
    scale = (tb.gain / peak) if peak > tb.gain else 1.0

    pcm = array.array('h', bytes(2 * total))
    for i in range(total):
        s = int(buf[i] * scale * 32767.0)
        if s > 32767:
            s = 32767
        elif s < -32768:
            s = -32768
        pcm[i] = s

    return _pack_wav(pcm.tobytes(), sample_rate)


def _pack_wav(pcm: bytes, sample_rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return out.getvalue()


def render_arrangement(plan, sample_rate: int = DEFAULT_SAMPLE_RATE,
                       timbre: Optional[Timbre] = None) -> bytes:
    """直接渲染一个 Arrangement。"""
    return render([(s.pitch, s.start, s.duration) for s in plan.steps],
                  sample_rate, timbre)


def estimate_bytes(seconds: float, sample_rate: int = DEFAULT_SAMPLE_RATE) -> int:
    """估算渲染后的 WAV 体积。"""
    return int(seconds * sample_rate * 2) + 64


# ---------------------------------------------------------------------------
# 播放（winsound）
# ---------------------------------------------------------------------------

class Player:
    """播放已渲染的 WAV。

    ★ 为什么用临时文件而不是内存播放
      Python 的 winsound 不允许 SND_MEMORY 与 SND_ASYNC 同时使用，会抛
      RuntimeError("Cannot play asynchronously from memory")。
      所以这里把音频落到临时文件，再用 SND_FILENAME | SND_ASYNC 异步播放。
      临时目录在进程退出时清理。

    winsound 本身没有暂停能力，暂停用「停止 + 记住位置 + 从该位置续播」实现：
    续播时把原始 WAV 从指定位置切片后重新写入同一个临时文件。
    """

    SND_ASYNC = 0x0001
    SND_FILENAME = 0x20000
    SND_PURGE = 0x0040
    SND_NODEFAULT = 0x0002

    def __init__(self, sample_rate: int = DEFAULT_SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._wav: Optional[bytes] = None
        self._start_pos = 0.0
        self._started_at: Optional[float] = None
        self._duration = 0.0
        self._winsound = None
        self._tmpdir: Optional[str] = None
        self._path: Optional[str] = None
        self._error: Optional[str] = None
        try:
            import winsound
            self._winsound = winsound
        except Exception as exc:
            self._error = '系统不支持 winsound：%s' % exc
        if self._winsound is not None:
            try:
                self._tmpdir = tempfile.mkdtemp(prefix='gtiharmonica-')
                self._path = os.path.join(self._tmpdir, 'preview.wav')
                atexit.register(self.cleanup)
            except OSError as exc:
                self._error = '无法创建临时目录：%s' % exc
                self._winsound = None

    @staticmethod
    def is_available() -> bool:
        try:
            import winsound  # noqa: F401
            return True
        except Exception:
            return False

    @property
    def error(self) -> Optional[str]:
        """最近一次失败的原因，供界面提示。"""
        return self._error

    # -- 数据 --

    def load(self, wav: bytes) -> None:
        self.stop()
        self._wav = wav
        self._written = None        # 磁盘上现在对应哪份数据
        self._start_pos = 0.0
        try:
            with wave.open(io.BytesIO(wav), 'rb') as wf:
                self._duration = wf.getnframes() / float(wf.getframerate())
        except Exception:
            self._duration = 0.0

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def playing(self) -> bool:
        if self._started_at is None:
            return False
        return self.position() < self._duration

    @property
    def finished(self) -> bool:
        return self._started_at is not None and self.position() >= self._duration

    # -- 控制 --

    def _purge(self) -> None:
        if self._winsound is not None:
            try:
                self._winsound.PlaySound(None, self.SND_PURGE)
            except Exception:
                pass

    def _write(self, data: bytes) -> bool:
        if not self._path:
            self._error = '没有可用的临时文件路径'
            return False
        try:
            with open(self._path, 'wb') as fh:
                fh.write(data)
                # 必须真正落盘再交给 PlaySound：只 write 的话数据还在系统
                # 缓存里，winmm 立刻去读同一个文件会读到不完整的块，
                # 听感上就是播放中的断续/卡顿。大文件（几 MB）尤其明显。
                fh.flush()
                os.fsync(fh.fileno())
            return True
        except OSError as exc:
            self._error = '写入临时音频失败：%s' % exc
            return False

    def play(self, from_seconds: float = 0.0) -> bool:
        if self._wav is None or self._winsound is None:
            return False
        # 必须先停播，否则临时文件仍被占用、无法覆盖
        self._purge()

        from_seconds = max(0.0, min(from_seconds,
                                    max(self._duration - 0.05, 0.0)))
        if from_seconds <= 0.001:
            data = self._wav
        else:
            try:
                data = _slice_wav(self._wav, from_seconds)
            except Exception:
                data, from_seconds = self._wav, 0.0

        # 同一份数据已经落过盘就不必再写一遍。整段 WAV 有几 MB，
        # 每次擦掉重写只会白白增加「点试听到出声」的延迟。
        if data is not getattr(self, '_written', None):
            if not self._write(data):
                return False
            self._written = data

        flags = self.SND_FILENAME | self.SND_ASYNC | self.SND_NODEFAULT
        try:
            self._winsound.PlaySound(self._path, flags)
        except Exception as exc:
            self._error = '播放失败：%s' % exc
            return False

        self._error = None
        self._start_pos = from_seconds
        self._started_at = _now()
        return True

    def stop(self) -> None:
        self._purge()
        self._started_at = None
        self._start_pos = 0.0

    def pause(self) -> float:
        """暂停并返回当前位置（秒）。"""
        pos = self.position()
        self._purge()
        self._started_at = None
        self._start_pos = min(pos, self._duration)
        return self._start_pos

    def seek(self, seconds: float) -> float:
        self._start_pos = max(0.0, min(seconds, self._duration))
        return self._start_pos

    def position(self) -> float:
        """当前播放位置（秒）。"""
        if self._started_at is None:
            return self._start_pos
        return min(self._start_pos + (_now() - self._started_at), self._duration)

    def cleanup(self) -> None:
        self._purge()
        if self._tmpdir and os.path.isdir(self._tmpdir):
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        self._tmpdir = None
        self._path = None


def _slice_wav(wav: bytes, from_seconds: float) -> bytes:
    """从指定位置截取 WAV 数据段并重新打包头部。"""
    with wave.open(io.BytesIO(wav), 'rb') as wf:
        rate = wf.getframerate()
        width = wf.getsampwidth()
        channels = wf.getnchannels()
        total = wf.getnframes()
        offset = min(int(from_seconds * rate), max(total - 1, 0))
        wf.setpos(offset)
        frames = wf.readframes(total - offset)

    out = io.BytesIO()
    with wave.open(out, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(frames)
    return out.getvalue()


def _now() -> float:
    import time
    return time.perf_counter()


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def selftest() -> None:
    """快速验证合成链路与性能。"""
    import time as _t

    notes = [(60 + (i % 12), i * 0.25, 0.22) for i in range(40)]
    for name, timbre in (('default', Timbre()), ('soft', Timbre.soft()),
                         ('bright', Timbre.bright())):
        t0 = _t.perf_counter()
        wav = render(notes, DEFAULT_SAMPLE_RATE, timbre)
        dt = _t.perf_counter() - t0
        print('  %-8s %8.1f KB  render %.2f s  (%.0fx realtime)'
              % (name, len(wav) / 1024, dt, (40 * 0.25) / max(dt, 1e-6)))
    print('  winsound available:', Player.is_available())


if __name__ == '__main__':
    selftest()
