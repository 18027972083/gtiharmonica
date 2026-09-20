"""转录精度验收：用已知 ground-truth 的合成音频测量识别准确率。

这是整个转录功能的可信度基础 —— 只有先证明「已知旋律能被正确还原」，
真实音乐上的结果才有意义。

两个指标：
    精确音高 —— 完全一致
    音级命中 —— 忽略八度差异。口琴只有 8 个按键，同一音级的不同八度
                折叠后落到同一个按键，所以这才是对口琴真正关键的指标。
                低音线干扰最容易造成八度级误判，这个指标能把它和
                「真的听错了音」区分开。

用法：python tools/eval_transcribe.py
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gtiharmonica import dsp, transcribe                      # noqa: E402
from gtiharmonica.decode import Audio                          # noqa: E402


# ---------------------------------------------------------------------------
# 合成测试信号
# ---------------------------------------------------------------------------

def synth_tone(freqs, dur, sr=22050, harmonics=6, amp=0.5):
    """生成带谐波的乐音（模拟真实乐器而非纯正弦）。"""
    n = int(dur * sr)
    t = np.arange(n) / sr
    out = np.zeros(n, dtype=np.float64)
    for h in range(1, harmonics + 1):
        w = 1.0 / h
        out += w * np.sin(2 * np.pi * freqs * h * t)
    env = np.ones(n)
    a = min(int(0.01 * sr), n // 4)
    r = min(int(0.03 * sr), n // 4)
    if a > 0:
        env[:a] = np.linspace(0, 1, a)
    if r > 0:
        env[-r:] = np.linspace(1, 0, r)
    return (out * env * amp / harmonics).astype(np.float32)


def render_melody(pitches, dur_each=0.35, sr=22050, gap=0.03):
    """把一串 MIDI 音高渲染成单声部旋律。"""
    chunks = []
    for p in pitches:
        chunks.append(synth_tone(dsp.midi_to_hz(p), dur_each, sr=sr))
        if gap > 0:
            chunks.append(np.zeros(int(gap * sr), dtype=np.float32))
    return Audio(samples=np.concatenate(chunks), samplerate=sr,
                 backend='synth', source='synth')


def add_noise(x, snr_db=25.0, seed=0):
    rng = np.random.default_rng(seed)
    power = float(np.mean(x ** 2))
    noise_power = power / (10 ** (snr_db / 10.0))
    noise = rng.normal(0, math.sqrt(noise_power), x.size).astype(np.float32)
    return (x + noise).astype(np.float32)


def render_with_accompaniment(melody, dur_each=0.35, sr=22050, accomp=0.8):
    """旋律 + 低音线 + 中音区和弦 + 鼓点的复音混合。

    这比白噪声更接近真实音乐：伴奏是有谐波结构的乐音，
    而且和弦的音区会和旋律重叠，正好考验 F0 估计的选择能力。
    """
    step = int(dur_each * sr)
    n = step * len(melody)
    mel = np.zeros(n, dtype=np.float64)
    bass = np.zeros(n, dtype=np.float64)
    chord = np.zeros(n, dtype=np.float64)
    drums = np.zeros(n, dtype=np.float64)

    def add_into(buf, start, seg, gain=1.0):
        e = min(start + seg.size, n)
        if e > start:
            buf[start:e] += seg[:e - start] * gain

    for i, p in enumerate(melody):
        add_into(mel, i * step, synth_tone(dsp.midi_to_hz(p), dur_each, sr=sr))

    if accomp > 0:
        bass_seq = [36, 41, 43, 38]
        for i in range(0, len(melody), 2):
            p = bass_seq[(i // 2) % len(bass_seq)]
            add_into(bass, i * step,
                     synth_tone(dsp.midi_to_hz(p), dur_each * 2, sr=sr, harmonics=8),
                     accomp * 0.75)

        chords = [(48, 52, 55), (53, 57, 60), (55, 59, 62), (50, 53, 57)]
        for i in range(0, len(melody), 4):
            for iv in chords[(i // 4) % len(chords)]:
                add_into(chord, i * step,
                         synth_tone(dsp.midi_to_hz(iv), dur_each * 4, sr=sr, harmonics=5),
                         accomp * 0.35)

        rng = np.random.default_rng(7)
        for i in range(0, len(melody), 2):
            start = i * step
            hit = int(0.045 * sr)
            burst = rng.normal(0, 1, hit).astype(np.float64)
            fade = np.exp(-np.linspace(0, 6, hit))
            add_into(drums, start, burst * fade, accomp * 0.10)

    mix = mel + bass + chord + drums
    peak = float(np.max(np.abs(mix)))
    if peak > 0:
        mix = mix / peak * 0.92
    return Audio(samples=mix.astype(np.float32), samplerate=sr,
                 backend='synth', source='mix')


# ---------------------------------------------------------------------------
# 评测
# ---------------------------------------------------------------------------

def compare(expected, got, dur_each=0.35):
    """按下标对齐比较，返回 (精确命中, 音级命中, 总数, 明细)。"""
    n = min(len(expected), len(got))
    exact = pc = 0
    detail = []
    for i in range(n):
        e = int(round(expected[i]))
        g = int(round(got[i]))
        if e == g:
            exact += 1
            pc += 1
        else:
            if e % 12 == g % 12:
                pc += 1
            detail.append((i, e, g))
    return exact, pc, max(len(expected), len(got)), detail


def match_timed(expected, notes, dur_each=0.35, gap=0.03):
    """按时间中点匹配，返回 (精确命中, 音级命中, 明细)。

    比「按下标对齐」鲁棒：音符数不同（多了碎音/少了漏音）也不会
    让后面全部错位。

    gap 必须与生成测试信号时的音符间隔一致，否则期望时间会逐音符
    累积偏移（漏掉间隔会让最后一个音整整差出 gap*N 秒）。
    """
    exact = pc = 0
    detail = []
    stride = dur_each + gap
    for i, e in enumerate(expected):
        t = i * stride + dur_each * 0.5
        hit = None
        for nt in notes:
            if nt.start - 0.02 <= t <= nt.start + nt.duration + 0.02:
                hit = int(nt.pitch)
                break
        e = int(round(e))
        if hit is None:
            detail.append((i, e, None))
            continue
        if hit == e:
            exact += 1
            pc += 1
        elif hit % 12 == e % 12:
            pc += 1
            detail.append((i, e, hit))
        else:
            detail.append((i, e, hit))
    return exact, pc, detail


CASES = [
    ('C 大调音阶（上行）', [60, 62, 64, 65, 67, 69, 71, 72]),
    ('C 大调音阶（下行）', [72, 71, 69, 67, 65, 64, 62, 60]),
    ('小星星前两句', [60, 60, 67, 67, 69, 69, 67, 65, 65, 64, 64, 62, 62, 60]),
    ('大跳（八度）', [60, 72, 62, 74, 64, 76, 60, 72]),
    ('五度圈片段', [60, 67, 62, 69, 64, 71, 65, 72]),
    ('半音密集（难点）', [60, 61, 62, 63, 64, 65, 66, 67]),
    ('低音区', [48, 50, 52, 53, 55, 57, 59, 60]),
    ('高音区', [72, 74, 76, 77, 79, 81, 83, 84]),
]

MELODY = [60, 60, 67, 67, 69, 69, 67, 65, 65, 64, 64, 62, 62, 60]


def run():
    from gtiharmonica.instrument import note_name

    bar = '=' * 76
    print(bar)
    print('转录精度验收（合成 ground-truth）')
    print(bar)
    tot_exact = tot_pc = tot = 0

    for name, pitches in CASES:
        audio = render_melody(pitches, dur_each=0.35)
        res = transcribe.transcribe_audio(
            audio,
            transcribe.TranscribeOptions(scale='off', quantize='off',
                                         fold_to_instrument=False),
            title=name,
        )
        exact, pc, detail = match_timed(pitches, res.score.notes)
        n = len(pitches)
        tot_exact += exact
        tot_pc += pc
        tot += n
        flag = 'OK  ' if pc >= n * 0.85 else ('WARN' if pc >= n * 0.6 else 'FAIL')
        print('%s %-22s 音级 %2d/%2d (%5.1f%%)  精确 %2d/%2d  识别 %2d 个音符'
              % (flag, name, pc, n, pc / n * 100, exact, n, len(res.score.notes)))
        for i, e, g in detail[:3]:
            gs = note_name(g) if g is not None else '缺失'
            print('        #%d 期望 %s(%d) 实得 %s' % (i, note_name(e), e, gs))

    print('-' * 76)
    print('总体：音级命中 %.1f%% (%d/%d)   精确命中 %.1f%% (%d/%d)'
          % (tot_pc / tot * 100, tot_pc, tot,
             tot_exact / tot * 100, tot_exact, tot))

    print()
    print('-' * 76)
    print('复音伴奏鲁棒性（旋律 + 低音线 + 和弦 + 鼓点）')
    for level, label in ((0.0, '仅旋律'), (0.45, '轻伴奏'),
                         (0.8, '中等伴奏'), (1.2, '重伴奏')):
        audio = render_with_accompaniment(MELODY, 0.35, accomp=level)
        res = transcribe.transcribe_audio(
            audio, transcribe.TranscribeOptions(scale='off', quantize='off',
                                                fold_to_instrument=False))
        # 伴奏信号的音符是紧邻的（无 gap），期望时间按 stride=dur_each 计算
        exact, pc, _ = match_timed(MELODY, res.score.notes, 0.35, 0.0)
        n = len(MELODY)
        print('  %-8s 音级 %2d/%d (%5.1f%%)  精确 %2d/%d  识别 %2d 个音符'
              % (label, pc, n, pc / n * 100, exact, n, len(res.score.notes)))

    print()
    print('-' * 76)
    print('噪声鲁棒性（小星星 + 高斯噪声）')
    for snr in (40, 30, 20, 10):
        base = render_melody(MELODY, dur_each=0.35).samples
        audio = Audio(samples=add_noise(base, snr), samplerate=22050,
                      backend='synth', source='noisy')
        res = transcribe.transcribe_audio(
            audio, transcribe.TranscribeOptions(scale='off', quantize='off',
                                                fold_to_instrument=False))
        exact, pc, _ = match_timed(MELODY, res.score.notes)
        n = len(MELODY)
        print('  SNR %2ddB 音级 %2d/%d (%5.1f%%)  精确 %2d/%d'
              % (snr, pc, n, pc / n * 100, exact, n))

    return tot_pc / tot * 100


if __name__ == '__main__':
    acc = run()
    print()
    print('结论：%s' % ('通过（>=85%）' if acc >= 85 else '需要调参'))
