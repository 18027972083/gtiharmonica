# -*- coding: utf-8 -*-
"""GAME ONNX 推理原型：不依赖 torch / pytorch-lightning，只用 numpy + onnxruntime。

移植自 GAME-main（OpenVPI）的推理侧代码：
  * inference/me_infer.py      推理流程（encoder → D3PM 循环 segmenter → estimator）
  * inference/data.py          known_durations 的语义（整段时长作为一个已知区域）
  * inference/slicer2.py       按静音切片（这里加了「超长强制切分」兜底）
  * lib/config/schema.py       D3PM 时间表（t0=0, steps=8 → [0, 0.125, ..., 0.875]）
                             与默认阈值（boundary 0.3 / radius 2 帧 / presence 0.2）

用法：python tools/_game_onnx_proto.py <音频> [--model 模型目录] [--no-slice]
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np

TIMESTEP = 0.01          # 每帧 10 ms（encoder 输出帧率，实测 1 秒 → 100 帧）
SR = 44100               # 模型采样率
SEG_THRESHOLD = 0.2      # 与 GameInfer.exe 界面默认一致
SEG_RADIUS_SEC = 0.02    # 同上（换算成帧 = 2）
EST_THRESHOLD = 0.2      # 同上
NSTEPS = 8               # D3PM 采样步数（界面默认 8）

#: 超过这个秒数的一段强制切开：GAME 的静音切片在「没有安静段」的素材上会切不出来，
#: 官方 CLI 直接报 Slice duration exceeds 60 seconds 退出。这里兜底成硬切。
MAX_CHUNK_SEC = 55.0


# ---------------------------------------------------------------------------
# 音频：读取 + 重采样（FFT 法，避免引入 scipy）
# ---------------------------------------------------------------------------

def resample_fft(x: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """带抗混叠的 FFT 重采样（对整段音频一次性处理，长度允许略有出入）。"""
    if src_sr == dst_sr:
        return x
    n_out = int(round(len(x) * dst_sr / src_sr))
    spec = np.fft.rfft(x)
    n_src_bins = len(spec)
    n_dst_bins = n_out // 2 + 1
    keep = min(n_src_bins, n_dst_bins)
    out_spec = np.zeros(n_dst_bins, dtype=complex)
    out_spec[:keep] = spec[:keep]
    # 奈奎斯特附近做线性衰减，减少混叠
    if keep == n_dst_bins < n_src_bins:
        out_spec[-1] *= 0.5
    y = np.fft.irfft(out_spec, n=n_out)
    return y.astype(np.float32)


def load_audio(path: str, target_sr: int = SR) -> np.ndarray:
    """读成单声道 float32 @ target_sr。"""
    import soundfile as sf
    data, sr = sf.read(path, dtype='float32', always_2d=True)
    mono = data.mean(axis=1)
    if sr != target_sr:
        mono = resample_fft(mono, sr, target_sr)
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    if peak > 1.0:                      # 解码后偶尔有轻微过载，归一化避免爆音
        mono = mono / peak
    return np.ascontiguousarray(mono)


# ---------------------------------------------------------------------------
# 切片：移植 GAME 的 Slicer（按静音切），外加「超长强制切」兜底
# ---------------------------------------------------------------------------

def get_rms(y, frame_length=2048, hop_length=512, pad_mode='constant'):
    padding = (int(frame_length // 2), int(frame_length // 2))
    y = np.pad(y, padding, mode=pad_mode)
    axis = -1
    out_strides = y.strides + tuple([y.strides[axis]])
    x_shape_trimmed = list(y.shape)
    x_shape_trimmed[axis] -= frame_length - 1
    out_shape = tuple(x_shape_trimmed) + tuple([frame_length])
    xw = np.lib.stride_tricks.as_strided(y, shape=out_shape, strides=out_strides)
    target_axis = axis - 1 if axis < 0 else axis + 1
    xw = np.moveaxis(xw, -1, target_axis)
    slices = [slice(None)] * xw.ndim
    slices[axis] = slice(0, None, hop_length)
    x = xw[tuple(slices)]
    power = np.mean(np.abs(x) ** 2, axis=-2, keepdims=True)
    return np.sqrt(power)


class Slicer:
    """GAME inference/slicer2.py 的 Slicer（参数同 extract 命令）。"""

    def __init__(self, sr, threshold=-40.0, min_length=1000,
                 min_interval=200, max_sil_kept=100, hop_size=20):
        min_interval = sr * min_interval / 1000
        self.sr = sr
        self.threshold = 10 ** (threshold / 20.0)
        self.hop_size = round(sr * hop_size / 1000)
        self.win_size = min(round(min_interval), 4 * self.hop_size)
        self.min_length = round(sr * min_length / 1000 / self.hop_size)
        self.min_interval = round(min_interval / self.hop_size)
        self.max_sil_kept = round(sr * max_sil_kept / 1000 / self.hop_size)

    def _chunk(self, waveform, begin, end):
        return {'offset': begin * self.hop_size / self.sr,
                'waveform': waveform[begin * self.hop_size:
                                     min(waveform.shape[0], end * self.hop_size)]}

    def slice(self, waveform, max_chunk_sec=MAX_CHUNK_SEC):
        samples = waveform
        if (samples.shape[0] + self.hop_size - 1) // self.hop_size <= self.min_length:
            return [{'offset': 0.0, 'waveform': waveform}]
        rms_list = get_rms(y=samples, frame_length=self.win_size,
                           hop_length=self.hop_size).squeeze(0)
        sil_tags = []
        silence_start = None
        clip_start = 0
        for i, rms in enumerate(rms_list):
            if rms < self.threshold:
                if silence_start is None:
                    silence_start = i
                continue
            if silence_start is None:
                continue
            is_leading = silence_start == 0 and i > self.max_sil_kept
            need_middle = (i - silence_start >= self.min_interval
                           and i - clip_start >= self.min_length)
            if not is_leading and not need_middle:
                silence_start = None
                continue
            if i - silence_start <= self.max_sil_kept:
                pos = rms_list[silence_start:i + 1].argmin() + silence_start
                sil_tags.append((0, pos) if silence_start == 0 else (pos, pos))
                clip_start = pos
            elif i - silence_start <= self.max_sil_kept * 2:
                pos = rms_list[i - self.max_sil_kept:
                               silence_start + self.max_sil_kept + 1].argmin()
                pos += i - self.max_sil_kept
                pos_l = rms_list[silence_start:
                                 silence_start + self.max_sil_kept + 1].argmin() + silence_start
                pos_r = rms_list[i - self.max_sil_kept:i + 1].argmin() + i - self.max_sil_kept
                if silence_start == 0:
                    sil_tags.append((0, pos_r))
                    clip_start = pos_r
                else:
                    sil_tags.append((min(pos_l, pos), max(pos_r, pos)))
                    clip_start = max(pos_r, pos)
            else:
                pos_l = rms_list[silence_start:
                                 silence_start + self.max_sil_kept + 1].argmin() + silence_start
                pos_r = rms_list[i - self.max_sil_kept:i + 1].argmin() + i - self.max_sil_kept
                sil_tags.append((0, pos_r) if silence_start == 0 else (pos_l, pos_r))
                clip_start = pos_r
            silence_start = None
        total_frames = rms_list.shape[0]
        if silence_start is not None and total_frames - silence_start >= self.min_interval:
            silence_end = min(total_frames, silence_start + self.max_sil_kept)
            pos = rms_list[silence_start:silence_end + 1].argmin() + silence_start
            sil_tags.append((pos, total_frames + 1))
        if len(sil_tags) == 0:
            chunks = [{'offset': 0.0, 'waveform': waveform}]
        else:
            chunks = []
            if sil_tags[0][0] > 0:
                chunks.append(self._chunk(waveform, 0, sil_tags[0][0]))
            for i in range(len(sil_tags) - 1):
                chunks.append(self._chunk(waveform, sil_tags[i][1], sil_tags[i + 1][0]))
            if sil_tags[-1][1] < total_frames:
                chunks.append(self._chunk(waveform, sil_tags[-1][1], total_frames))
        return self._enforce_max(chunks, max_chunk_sec)

    @staticmethod
    def _enforce_max(chunks, max_chunk_sec):
        """兜底：任何一段超过 max_chunk_sec 就硬切成等长小段。

        GAME 官方在静音切分失败时直接报错退出（Slice duration exceeds
        60 seconds）；软件里不能这么干，硬切虽然可能在切点附近丢一两个音，
        但整首歌总能出结果。
        """
        out = []
        for ch in chunks:
            w = ch['waveform']
            dur = len(w) / SR
            if dur <= max_chunk_sec:
                out.append(ch)
                continue
            n = int(np.ceil(dur / max_chunk_sec))
            step = int(np.ceil(len(w) / n))
            for k in range(n):
                seg = w[k * step:(k + 1) * step]
                if len(seg) < SR * 0.2:      # 尾巴不足 0.2 秒就并进上一段
                    if out and k > 0:
                        out[-1]['waveform'] = np.concatenate(
                            [out[-1]['waveform'], seg])
                    continue
                out.append({'offset': ch['offset'] + k * step / SR, 'waveform': seg})
        return out


# ---------------------------------------------------------------------------
# GAME 推理
# ---------------------------------------------------------------------------

class GameOnnx:
    """把 GAME 的 5 个 ONNX 模型串成「音频 → 音符」流程。"""

    def __init__(self, model_dir, seg_threshold=SEG_THRESHOLD,
                 seg_radius_sec=SEG_RADIUS_SEC, est_threshold=EST_THRESHOLD,
                 language=0, nsteps=NSTEPS, providers=None):
        import onnxruntime as ort
        providers = providers or ['CPUExecutionProvider']
        join = lambda f: os.path.join(model_dir, f)
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.enc = ort.InferenceSession(join('encoder.onnx'), so, providers=providers)
        self.seg = ort.InferenceSession(join('segmenter.onnx'), so, providers=providers)
        self.est = ort.InferenceSession(join('estimator.onnx'), so, providers=providers)
        self.dur2bd = ort.InferenceSession(join('dur2bd.onnx'), so, providers=providers)
        self.bd2dur = ort.InferenceSession(join('bd2dur.onnx'), so, providers=providers)
        # onnxruntime 只吃 ndarray，numpy 标量会报 “Unable to handle object” —— 一律包成数组
        self.seg_threshold = np.array(seg_threshold, dtype=np.float32)
        self.est_threshold = np.array(est_threshold, dtype=np.float32)
        self.radius = np.array(round(seg_radius_sec / TIMESTEP), dtype=np.int64)
        self.language = np.array([language], dtype=np.int64)
        #: D3PM 时间表：t 从 0（全噪声）到 (steps-1)/steps
        self.ts = [np.array([i * (1.0 / nsteps)], dtype=np.float32)
                   for i in range(nsteps)]

    # -- 单段 --

    def notes_of_chunk(self, wave: np.ndarray):
        """一段音频 → [(start_sec, dur_sec, midi_pitch_float)]（已按 presence 过滤）。"""
        dur = np.array([len(wave) / SR], dtype=np.float32)
        x_seg, x_est, mask_t = self.enc.run(
            None, {'waveform': wave[None, :], 'duration': dur})
        # known_boundaries：整段时长作为一个「已知区域」（见 data.py 的 collate）
        known = self.dur2bd.run(
            None, {'durations': dur[None, :], 'maskT': mask_t})[0]
        prev = known.copy()
        for t in self.ts:
            prev = self.seg.run(None, {
                'x_seg': x_seg, 'language': self.language,
                'known_boundaries': known, 'prev_boundaries': prev,
                't': t, 'maskT': mask_t,
                'threshold': self.seg_threshold, 'radius': self.radius,
            })[0]
        durations, mask_n = self.bd2dur.run(
            None, {'boundaries': prev, 'maskT': mask_t})
        presence, scores = self.est.run(None, {
            'x_est': x_est, 'boundaries': prev, 'maskT': mask_t,
            'maskN': mask_n, 'threshold': self.est_threshold})
        out = []
        t_cursor = 0.0
        for i in range(durations.shape[1]):
            d = float(durations[0, i])
            if d <= 0:
                continue
            if bool(mask_n[0, i]) and bool(presence[0, i]):
                out.append((t_cursor, d, float(scores[0, i])))
            t_cursor += d
        return out

    # -- 整首 --

    def extract(self, wave: np.ndarray, progress=None, should_stop=None):
        """整段音频 → 音符列表。切片后逐段推理，按 offset 拼回时间轴。"""
        chunks = Slicer(SR).slice(wave)
        notes = []
        for i, ch in enumerate(chunks):
            if should_stop is not None and should_stop():
                break
            if progress is not None:
                progress(i, len(chunks), ch['offset'])
            local = self.notes_of_chunk(np.ascontiguousarray(ch['waveform']))
            for st, d, pitch in local:
                notes.append((ch['offset'] + st, d, pitch))
        notes.sort(key=lambda n: n[0])
        return notes


# ---------------------------------------------------------------------------
# 命令行自检
# ---------------------------------------------------------------------------

def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print('用法: python tools/_game_onnx_proto.py <音频文件> [--model 目录] [--no-slice]')
        return 2
    audio = args[0]
    model_dir = 'D:/AI/tools/GAME/model/GAME-1.0.3-small-onnx'
    if '--model' in sys.argv:
        model_dir = sys.argv[sys.argv.index('--model') + 1]

    print('音频:', audio)
    t0 = time.time()
    wave = load_audio(audio)
    print('已加载: %.1f 秒 @ %d Hz（重采样用时 %.1f 秒）'
          % (len(wave) / SR, SR, time.time() - t0))

    engine = GameOnnx(model_dir)
    print('模型: %s  (D3PM %d 步, t 序列 %s)'
          % (os.path.basename(model_dir), len(engine.ts),
             ', '.join('%.3f' % float(t[0]) for t in engine.ts)))

    t0 = time.time()
    notes = engine.extract(wave, progress=lambda i, n, off:
                           print('  切片 %d/%d  offset=%.1fs' % (i + 1, n, off)))
    el = time.time() - t0
    print('推理完成: %d 音符, 用时 %.1f 秒（音频 %.1f 秒，约 %.0fx 实时）'
          % (len(notes), el, len(wave) / SR, (len(wave) / SR) / max(el, 1e-6)))
    if notes:
        import collections
        pitches = [round(p) for _, _, p in notes]
        print('音域: MIDI %d..%d   时长: %.0f:%02.0f'
              % (min(pitches), max(pitches),
                 notes[-1][0] // 60, notes[-1][0] % 60))
        hist = collections.Counter(pitches)
        print('最常见的 8 个音高:', ', '.join('%d(%d)' % (p, c) for p, c in hist.most_common(8)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
