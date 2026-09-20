# -*- coding: utf-8 -*-
"""音频 → 曲谱：把歌声/哼唱直接转成能演奏的曲谱（内置 GAME ONNX 推理）。

为什么是这套方案：GAME（OpenVPI 的歌声转 MIDI 模型）效果最好，但它原本
只有「命令行 / 打包好的 exe」两种用法，用户得自己装外部工具、还得先做人声
分离、还要手动切段绕开它的 60 秒切片限制。这里把它的 ONNX 模型直接内置，
用 numpy + onnxruntime 复现推理流程（不依赖 torch / pytorch-lightning，
省掉 400MB 依赖），拖一首歌进来就能出曲谱。

复现自 GAME-main（MIT）的推理侧代码：
  * inference/me_infer.py   流程：encoder → D3PM 循环 segmenter → estimator
  * inference/data.py       known_durations 的语义（整段时长作为一个已知区域）
  * inference/slicer2.py    按静音切片（这里补了「超长强制切分」兜底）
  * lib/config/schema.py    D3PM 时间表（t0=0, steps=8）与默认阈值

阈值与 GameInfer.exe 界面默认一致（0.2 / 2 帧 / 0.2 / 8 步）；实测同一段
人声素材，内置结果与外部工具逐音符吻合 94%（±100ms），音高一致率 92%，
而速度更快、且切片失败时不会像外部工具那样直接报错退出。
"""
from __future__ import annotations

import os
import sys
import time
from typing import Callable, List, Optional, Tuple

import numpy as np

from .score import Note, Score

#: 模型固有参数（ONNX 里编译死的帧率与采样率，实测 1 秒音频 → 100 帧）
TIMESTEP = 0.01
SAMPLE_RATE = 44100

#: 推理阈值。分割阈值决定「音符切得多细」：调低 → 更多更短的音符。
#: 实测同一段人声（心似烟火）：0.2 → 中位 340ms/506 音符，0.05 → 240ms/548。
#: 取 0.1 是折中 —— 再细的边界在游戏里也按不出来（口琴有最短可识别音长）。
SEG_THRESHOLD = 0.1       # 分割阈值 --seg-threshold
SEG_RADIUS_SEC = 0.02     # 分割半径 20ms --seg-radius（换算成帧 = 2）
EST_THRESHOLD = 0.2       # 估计阈值 --est-threshold（影响音高判定，不影响分割）
NSTEPS = 8                # D3PM 采样步数 --seg-d3pm-nsteps

#: 单段最长秒数。GAME 靠静音切分，素材里没有足够安静段时它切不出来，
#: 官方 CLI 会直接报 “Slice duration exceeds 60 seconds” 退出。
#: 软件里不能这样，超过就硬切（切点附近可能丢一两个音，但整首总能出结果）。
MAX_CHUNK_SEC = 55.0

#: 支持的音频后缀（soundfile / libsndfile 能直接解码的）
AUDIO_EXTS = ('.wav', '.mp3', '.flac', '.ogg', '.aiff', '.aif', '.m4a', '.aac')

ProgressFn = Callable[[str, int, int], None]
StopFn = Callable[[], bool]


# ---------------------------------------------------------------------------
# 模型与依赖定位
# ---------------------------------------------------------------------------

def _asset_root() -> str:
    """资产根目录（打包后在 _MEIPASS 里，开发时在源码树里）。"""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, 'gtiharmonica', 'assets')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')


MODEL_FILES = ('encoder.onnx', 'segmenter.onnx', 'estimator.onnx',
               'dur2bd.onnx', 'bd2dur.onnx')


def default_model_dir() -> str:
    return os.path.join(_asset_root(), 'game_model')


def model_available(model_dir: Optional[str] = None) -> bool:
    d = model_dir or default_model_dir()
    return all(os.path.isfile(os.path.join(d, f)) for f in MODEL_FILES)


def onnxruntime_version() -> str:
    try:
        import onnxruntime as ort
        return ort.__version__
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# 音频读取与重采样
# ---------------------------------------------------------------------------

def resample_fft(x: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """带抗混叠的 FFT 重采样。用 numpy 自己算，避免为了重采样塞进 scipy。"""
    if src_sr == dst_sr:
        return x
    n_out = int(round(len(x) * dst_sr / src_sr))
    spec = np.fft.rfft(x)
    n_dst_bins = n_out // 2 + 1
    keep = min(len(spec), n_dst_bins)
    out_spec = np.zeros(n_dst_bins, dtype=complex)
    out_spec[:keep] = spec[:keep]
    if keep == n_dst_bins < len(spec):
        out_spec[-1] *= 0.5          # 奈奎斯特附近半幅，减轻混叠
    return np.fft.irfft(out_spec, n=n_out).astype(np.float32)


def load_audio(path: str, target_sr: int = SAMPLE_RATE) -> np.ndarray:
    """读成单声道 float32 @ target_sr。

    libsndfile 读不了的格式（部分 m4a/aac）交给系统的 ffmpeg 兜底 ——
    有就用，没有就报一句人话。
    """
    import soundfile as sf
    try:
        data, sr = sf.read(path, dtype='float32', always_2d=True)
    except Exception:
        wav = _decode_with_ffmpeg(path)
        if wav is None:
            raise
        data, sr = wav
    mono = data.mean(axis=1)
    if sr != target_sr:
        mono = resample_fft(mono, sr, target_sr)
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    if peak > 1.0:
        mono = mono / peak
    return np.ascontiguousarray(mono)


def _decode_with_ffmpeg(path: str):
    """用 ffmpeg 解成 wav 再读（可选路径，失败返回 None）。"""
    import shutil
    import subprocess
    import tempfile

    exe = shutil.which('ffmpeg')
    if not exe:
        return None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'decoded.wav')
            subprocess.run([exe, '-y', '-v', 'error', '-i', path,
                            '-ac', '1', '-ar', str(SAMPLE_RATE), out],
                           check=True, timeout=600,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            import soundfile as sf
            data, sr = sf.read(out, dtype='float32', always_2d=True)
            return data, sr
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 切片（移植 GAME 的 slicer2.py，外加超长兜底）
# ---------------------------------------------------------------------------

def _rms(y: np.ndarray, frame_length: int = 2048, hop_length: int = 512
         ) -> np.ndarray:
    padding = (int(frame_length // 2), int(frame_length // 2))
    y = np.pad(y, padding, mode='constant')
    out_strides = y.strides + tuple([y.strides[-1]])
    shape = list(y.shape)
    shape[-1] -= frame_length - 1
    xw = np.lib.stride_tricks.as_strided(
        y, shape=tuple(shape) + (frame_length,), strides=out_strides)
    xw = np.moveaxis(xw, -1, -2)
    xw = xw[..., ::hop_length]
    return np.sqrt(np.mean(np.abs(xw) ** 2, axis=-2, keepdims=True))


class Slicer:
    """按静音切片（参数同 GAME 的 extract 命令）。"""

    def __init__(self, sr: int = SAMPLE_RATE, threshold: float = -40.0,
                 min_length: int = 1000, min_interval: int = 200,
                 max_sil_kept: int = 100, hop_size: int = 20):
        min_interval = sr * min_interval / 1000
        self.sr = sr
        self.threshold = 10 ** (threshold / 20.0)
        self.hop_size = round(sr * hop_size / 1000)
        self.win_size = min(round(min_interval), 4 * self.hop_size)
        self.min_length = round(sr * min_length / 1000 / self.hop_size)
        self.min_interval = round(min_interval / self.hop_size)
        self.max_sil_kept = round(sr * max_sil_kept / 1000 / self.hop_size)

    def _chunk(self, waveform: np.ndarray, begin: int, end: int) -> dict:
        return {'offset': begin * self.hop_size / self.sr,
                'waveform': waveform[begin * self.hop_size:
                                     min(waveform.shape[0], end * self.hop_size)]}

    def slice(self, waveform: np.ndarray,
              max_chunk_sec: float = MAX_CHUNK_SEC) -> List[dict]:
        if (waveform.shape[0] + self.hop_size - 1) // self.hop_size <= self.min_length:
            return [{'offset': 0.0, 'waveform': waveform}]
        rms_list = _rms(waveform, self.win_size, self.hop_size).squeeze(0)
        tags: List[Tuple[int, int]] = []
        silence_start = None
        clip_start = 0
        for i, rms in enumerate(rms_list):
            if rms < self.threshold:
                if silence_start is None:
                    silence_start = i
                continue
            if silence_start is None:
                continue
            leading = silence_start == 0 and i > self.max_sil_kept
            middle = (i - silence_start >= self.min_interval
                      and i - clip_start >= self.min_length)
            if not leading and not middle:
                silence_start = None
                continue
            if i - silence_start <= self.max_sil_kept:
                pos = int(rms_list[silence_start:i + 1].argmin()) + silence_start
                tags.append((0, pos) if silence_start == 0 else (pos, pos))
                clip_start = pos
            elif i - silence_start <= self.max_sil_kept * 2:
                pos = int(rms_list[i - self.max_sil_kept:
                                   silence_start + self.max_sil_kept + 1].argmin())
                pos += i - self.max_sil_kept
                pos_l = int(rms_list[silence_start:
                                     silence_start + self.max_sil_kept + 1].argmin()) + silence_start
                pos_r = int(rms_list[i - self.max_sil_kept:i + 1].argmin()) + i - self.max_sil_kept
                if silence_start == 0:
                    tags.append((0, pos_r))
                    clip_start = pos_r
                else:
                    tags.append((min(pos_l, pos), max(pos_r, pos)))
                    clip_start = max(pos_r, pos)
            else:
                pos_l = int(rms_list[silence_start:
                                     silence_start + self.max_sil_kept + 1].argmin()) + silence_start
                pos_r = int(rms_list[i - self.max_sil_kept:i + 1].argmin()) + i - self.max_sil_kept
                tags.append((0, pos_r) if silence_start == 0 else (pos_l, pos_r))
                clip_start = pos_r
            silence_start = None
        total = rms_list.shape[0]
        if silence_start is not None and total - silence_start >= self.min_interval:
            end = min(total, silence_start + self.max_sil_kept)
            pos = int(rms_list[silence_start:end + 1].argmin()) + silence_start
            tags.append((pos, total + 1))
        if not tags:
            chunks = [{'offset': 0.0, 'waveform': waveform}]
        else:
            chunks = []
            if tags[0][0] > 0:
                chunks.append(self._chunk(waveform, 0, tags[0][0]))
            for i in range(len(tags) - 1):
                chunks.append(self._chunk(waveform, tags[i][1], tags[i + 1][0]))
            if tags[-1][1] < total:
                chunks.append(self._chunk(waveform, tags[-1][1], total))
        return self._enforce_max(chunks, max_chunk_sec)

    @staticmethod
    def _enforce_max(chunks: List[dict], max_chunk_sec: float) -> List[dict]:
        out: List[dict] = []
        for ch in chunks:
            w = ch['waveform']
            dur = len(w) / SAMPLE_RATE
            if dur <= max_chunk_sec:
                out.append(ch)
                continue
            n = int(np.ceil(dur / max_chunk_sec))
            step = int(np.ceil(len(w) / n))
            for k in range(n):
                seg = w[k * step:(k + 1) * step]
                if len(seg) < SAMPLE_RATE * 0.2 and out:
                    out[-1]['waveform'] = np.concatenate([out[-1]['waveform'], seg])
                    continue
                if len(seg) < SAMPLE_RATE * 0.2:
                    continue
                out.append({'offset': ch['offset'] + k * step / SAMPLE_RATE,
                            'waveform': seg})
        return out


# ---------------------------------------------------------------------------
# ONNX 推理
# ---------------------------------------------------------------------------

class GameOnnx:
    """把 GAME 的 5 个 ONNX 模型串成「音频 → 音符」流程。"""

    def __init__(self, model_dir: Optional[str] = None,
                 seg_threshold: float = SEG_THRESHOLD,
                 seg_radius_sec: float = SEG_RADIUS_SEC,
                 est_threshold: float = EST_THRESHOLD,
                 language: int = 0, nsteps: int = NSTEPS):
        import onnxruntime as ort
        d = model_dir or default_model_dir()
        if not model_available(d):
            raise FileNotFoundError('内置模型不完整：%s' % d)
        opts = ort.SessionOptions()
        opts.log_severity_level = 3          # 只留错误，别往控制台刷警告
        join = lambda f: os.path.join(d, f)
        prov = ['CPUExecutionProvider']
        self.enc = ort.InferenceSession(join('encoder.onnx'), opts, providers=prov)
        self.seg = ort.InferenceSession(join('segmenter.onnx'), opts, providers=prov)
        self.est = ort.InferenceSession(join('estimator.onnx'), opts, providers=prov)
        self.d2b = ort.InferenceSession(join('dur2bd.onnx'), opts, providers=prov)
        self.b2d = ort.InferenceSession(join('bd2dur.onnx'), opts, providers=prov)
        # onnxruntime 只吃 ndarray，numpy 标量会报 “Unable to handle object”
        self.seg_threshold = np.array(seg_threshold, dtype=np.float32)
        self.est_threshold = np.array(est_threshold, dtype=np.float32)
        self.radius = np.array(round(seg_radius_sec / TIMESTEP), dtype=np.int64)
        self.language = np.array([language], dtype=np.int64)
        self.ts = [np.array([i * (1.0 / nsteps)], dtype=np.float32)
                   for i in range(nsteps)]

    def notes_of_chunk(self, wave: np.ndarray
                       ) -> List[Tuple[float, float, float]]:
        """一段音频 → [(start, dur, midi_float)]（已按 presence 过滤）。"""
        dur = np.array([len(wave) / SAMPLE_RATE], dtype=np.float32)
        x_seg, x_est, mask_t = self.enc.run(
            None, {'waveform': wave[None, :], 'duration': dur})
        # 整段时长作为唯一「已知区域」，D3PM 再把它细化成一个个音符
        known = self.d2b.run(None, {'durations': dur[None, :], 'maskT': mask_t})[0]
        prev = known.copy()
        for t in self.ts:
            prev = self.seg.run(None, {
                'x_seg': x_seg, 'language': self.language,
                'known_boundaries': known, 'prev_boundaries': prev,
                't': t, 'maskT': mask_t,
                'threshold': self.seg_threshold, 'radius': self.radius,
            })[0]
        durations, mask_n = self.b2d.run(
            None, {'boundaries': prev, 'maskT': mask_t})
        presence, scores = self.est.run(None, {
            'x_est': x_est, 'boundaries': prev, 'maskT': mask_t,
            'maskN': mask_n, 'threshold': self.est_threshold})
        out: List[Tuple[float, float, float]] = []
        cursor = 0.0
        for i in range(durations.shape[1]):
            d = float(durations[0, i])
            if d <= 0:
                continue
            if bool(mask_n[0, i]) and bool(presence[0, i]):
                out.append((cursor, d, float(scores[0, i])))
            cursor += d
        return out

    def extract(self, wave: np.ndarray, progress: Optional[ProgressFn] = None,
                should_stop: Optional[StopFn] = None
                ) -> List[Tuple[float, float, float]]:
        chunks = Slicer(SAMPLE_RATE).slice(wave)
        notes: List[Tuple[float, float, float]] = []
        for i, ch in enumerate(chunks):
            if should_stop is not None and should_stop():
                break
            if progress is not None:
                progress('推理', i, len(chunks))
            for st, d, pitch in self.notes_of_chunk(
                    np.ascontiguousarray(ch['waveform'])):
                notes.append((ch['offset'] + st, d, pitch))
        notes.sort(key=lambda n: n[0])
        return notes


# ---------------------------------------------------------------------------
# 转谱后处理：把「能听」整理成「能弹」
# ---------------------------------------------------------------------------

#: 大 / 小调音阶（半音程）
_MAJOR = (0, 2, 4, 5, 7, 9, 11)
_MINOR = (0, 2, 3, 5, 7, 8, 10)

#: 音区规整的目标中位音高。C5 一带是「唱歌」的听感区：游戏口琴在这一带
#: 最亮、最像人声；低音区（C4 往下）明显发闷 —— 转谱结果经常整体低一个
#: 八度，听感差距主要来自这里。
TARGET_MEDIAN = 72


def playable_range() -> Tuple[int, int]:
    """游戏里可演奏的音高范围（音区规整不能把旋律推出这个范围）。"""
    try:
        from .instrument import Instrument
        lo, hi = Instrument().playable_range()
        return int(lo), int(hi)
    except Exception:
        return 48, 85


def infer_key(notes: Sequence[Note]) -> Tuple[int, str]:
    """从音高分布推调性与调式（Krumhansl 模板的简化版）。

    转谱结果是绝对音高，没有调号信息；调内吸附需要一个 key 才能用。
    """
    hist = [0.0] * 12
    for n in notes:
        hist[n.pitch % 12] += 1.0
    total = sum(hist) or 1.0
    hist = [h / total for h in hist]
    best = (None, 0, 'major')
    for root in range(12):
        for scale, steps in (('major', _MAJOR), ('minor', _MINOR)):
            s = sum(hist[(root + k) % 12] for k in steps)
            s += 0.5 * hist[root] + 0.25 * hist[(root + 7) % 12]   # 主音/属音加权
            if best[0] is None or s > best[0]:
                best = (s, root, scale)
    return best[1], best[2]


def grid_quantize(notes: Sequence[Note], bpm: float = 120.0, div: int = 4
                  ) -> List[Note]:
    """把音符吸附到节拍网格：起点取整、时值取整数个格。

    为什么必须有这一步：音频转谱的时值是模型估出来的散值（实测中位
    300ms，还不是任何网格的倍数），弹起来节奏是散的。人工扒的谱之所以
    好弹，正是因为它按网格对齐 —— 实测那份被认可的《心似烟火》就是
    「主旋律轨 → 单音化 → 120BPM 的 16 分网格（125ms）」的产物。
    """
    grid = 60.0 / max(bpm, 1e-6) / max(div, 1)
    cells: dict = {}
    order: List = []
    for n in sorted(notes, key=lambda x: x.start):
        g = int(round(n.start / grid))
        key = (g, n.pitch)
        dur = max(1, int(round(n.duration / grid)))
        if key in cells:
            cells[key] = max(cells[key], dur)      # 同一格同一个音，并成一个
        else:
            cells[key] = dur
            order.append(key)
    return [Note(pitch=p, start=g * grid, duration=cells[(g, p)] * grid)
            for g, p in order]


def polish_score(score: Score, target_median: int = TARGET_MEDIAN,
                 octave: bool = True, merge_fragments: bool = True,
                 drop_tiny: float = 0.06, snap_scale: bool = True,
                 quantize: bool = True, bpm: float = 120.0,
                 grid_div: int = 4) -> Score:
    """把转谱原样结果整理成「好弹」的曲谱。

    音频转谱与人工扒谱的主要差距都在这几步里：
      1. 音区不对 —— 模型给绝对音高，可能整体比主唱低/高一个八度；
         在游戏口琴上就是低音区（闷）而不是中音区（亮）。
      2. 节奏散 —— 时值不是拍子上的整数值（见 grid_quantize）。
      3. 碎片 —— 一个音被切成几个同音高的短音，弹起来像抖。
      4. 滑音/颤音被识别成极短装饰音。
      5. ±1 半音漂移 —— 浮点音高四舍五入的产物，调内吸附能吃掉。
    """
    notes = sorted(score.notes, key=lambda n: n.start)

    # 1) 音区规整：整条旋律平移到「最接近目标中位、且不超出游戏音域」的八度
    if octave and notes:
        pitches = sorted(n.pitch for n in notes)
        median = pitches[len(pitches) // 2]
        lo, hi = pitches[0], pitches[-1]
        limit_lo, limit_hi = playable_range()
        best_k, best_cost = 0, None
        for k in (-2, -1, 0, 1, 2):
            if lo + 12 * k < limit_lo or hi + 12 * k > limit_hi:
                continue                      # 挪出去就弹不了了，跳过
            cost = abs(median + 12 * k - target_median)
            if best_cost is None or cost < best_cost:
                best_k, best_cost = k, cost
        if best_k:
            for n in notes:
                n.pitch += 12 * best_k

    # 2) 去极短装饰音（先删，免得它们把后面的合并带偏）
    if drop_tiny > 0 and len(notes) > 2:
        keep = []
        for i, n in enumerate(notes):
            if n.duration < drop_tiny and 0 < i < len(notes) - 1:
                continue
            keep.append(n)
        notes = keep

    # 3) 合并碎片：同音高、几乎相接的相邻音并成一个长音
    if merge_fragments and len(notes) > 1:
        merged: List[Note] = [notes[0]]
        for n in notes[1:]:
            prev = merged[-1]
            if n.pitch == prev.pitch and n.start - (prev.start + prev.duration) < 0.04:
                prev.duration = max(prev.duration, n.start + n.duration - prev.start)
                continue
            merged.append(n)
        notes = merged

    # 4) 调内吸附：只动偏离调式的音，且最多挪 1 个半音
    if snap_scale and notes:
        key, scale = infer_key(notes)
        from .edit import nearest_in_scale, scale_pitch_classes
        allowed = scale_pitch_classes(key, scale)
        for n in notes:
            if n.pitch % 12 in allowed:
                continue
            target = nearest_in_scale(n.pitch, key, scale)
            if abs(target - n.pitch) <= 1:
                n.pitch = target

    # 5) 节拍网格量化（最后做：前面的合并已经把碎片收干净了）
    if quantize and notes:
        notes = grid_quantize(notes, bpm=bpm, div=grid_div)

    out = Score(title=score.title, notes=notes, bpm=score.bpm,
                warnings=list(score.warnings))
    out.source = score.source
    return out


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def transcribe_audio(path: str, title: Optional[str] = None,
                     progress: Optional[ProgressFn] = None,
                     should_stop: Optional[StopFn] = None,
                     model_dir: Optional[str] = None,
                     polish: bool = True) -> Score:
    """音频文件 → 曲谱。

    :param progress: progress(阶段, 已完成, 总数)，阶段如「加载音频」「切片」「推理」
    :param should_stop: 返回 True 时中止（已算出的音符照常返回）
    :param polish: 是否做音区规整 / 合并碎片 / 调内吸附（默认做，见 polish_score）
    """
    if progress is not None:
        progress('加载音频', 0, 1)
    wave = load_audio(path)
    if progress is not None:
        progress('加载音频', 1, 1)

    engine = GameOnnx(model_dir)
    if progress is not None:
        progress('切片', 0, 1)
    notes = engine.extract(wave, progress=progress, should_stop=should_stop)

    name = title or os.path.splitext(os.path.basename(path))[0]
    out: List[Note] = []
    for start, dur, pitch in notes:
        out.append(Note(pitch=int(round(pitch)), start=float(start),
                        duration=float(dur), velocity=80))
    score = Score(title=name, notes=out, bpm=120.0)
    if polish:
        if progress is not None:
            progress('整理', 0, 1)
        score = polish_score(score)
        if progress is not None:
            progress('整理', 1, 1)
    return score
