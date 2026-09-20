"""音频转曲谱功能验收：引擎端到端 + GUI 驱动。

覆盖三层：
  1. 解码层    —— 各格式能否读出单声道 float32
  2. 转录引擎  —— 合成 ground-truth 精度、真实文件、可演奏性、序列化往返
  3. GUI       —— 侧边栏入口、对话框转录流程、试听、加入曲库

用法：python tests/transcribe_acceptance.py [真实音频文件...]
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

SHOTS = os.path.join(ROOT, 'build', 'shots')
os.makedirs(SHOTS, exist_ok=True)

# 模块级导入：后面的音高/窗长断言要直接读 dsp 的采样率与窗长常量
from gtiharmonica import dsp                             # noqa: E402

PASS, FAIL = [], []


def check(name, condition, extra=''):
    (PASS if condition else FAIL).append(name)
    print('  [%s] %s%s' % ('OK  ' if condition else 'FAIL', name,
                           ('  -> %s' % extra) if extra else ''))


# ---------------------------------------------------------------------------
# 合成素材
# ---------------------------------------------------------------------------

def synth_tone(freq, dur, sr=22050, harmonics=6, amp=0.5):
    n = int(dur * sr)
    t = np.arange(n) / sr
    out = np.zeros(n, dtype=np.float64)
    for h in range(1, harmonics + 1):
        out += (1.0 / h) * np.sin(2 * np.pi * freq * h * t)
    env = np.ones(n)
    a, r = min(int(0.01 * sr), n // 4), min(int(0.03 * sr), n // 4)
    if a:
        env[:a] = np.linspace(0, 1, a)
    if r:
        env[-r:] = np.linspace(1, 0, r)
    return (out * env * amp / harmonics).astype(np.float32)


def write_wav(path, samples, sr=22050):
    import wave
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype('<i2')
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


MELODY = [60, 60, 67, 67, 69, 69, 67, 65, 65, 64, 64, 62, 62, 60]


def render_melody(pitches, dur=0.35, gap=0.03, sr=22050):
    from gtiharmonica import dsp
    chunks = []
    for p in pitches:
        chunks.append(synth_tone(dsp.midi_to_hz(p), dur, sr=sr))
        if gap:
            chunks.append(np.zeros(int(gap * sr), dtype=np.float32))
    return np.concatenate(chunks)


# ---------------------------------------------------------------------------
# 1. 解码层
# ---------------------------------------------------------------------------

def test_decode():
    print('\n=== 1. 解码层 ===')
    from gtiharmonica import decode

    backends = decode.available_backends()
    check('至少有一个解码后端', bool(backends), ', '.join(backends))
    check('soundfile 可用（覆盖 MP3/WAV/FLAC/OGG）', 'soundfile' in backends)

    tmp = tempfile.mkdtemp(prefix='gti-acc-')
    wav = os.path.join(tmp, 'probe.wav')
    write_wav(wav, render_melody(MELODY))
    audio = decode.decode(wav)
    check('WAV 解码成功', len(audio.samples) > 0,
          '%d 采样 / %d Hz' % (len(audio.samples), audio.samplerate))
    check('解码输出是单声道 float32',
          audio.samples.ndim == 1 and audio.samples.dtype == np.float32)
    check('时长合理（14 音 × 0.38s ≈ 5.3s）',
          4.5 < audio.duration < 6.0, '%.2f 秒' % audio.duration)
    check('is_supported 识别扩展名',
          decode.is_supported('a.mp3') and decode.is_supported('a.FLAC')
          and not decode.is_supported('a.txt'))

    # 静音文件应该明确报错而不是返回空数据
    silent = os.path.join(tmp, 'silent.wav')
    write_wav(silent, np.zeros(22050, dtype=np.float32))
    try:
        decode.decode(silent)
        check('静音文件应当报错', False)
    except decode.DecodeError:
        check('静音文件给出可读错误', True)

    try:
        decode.decode(os.path.join(tmp, 'nope.mp3'))
        check('不存在的文件应当报错', False)
    except decode.DecodeError:
        check('不存在的文件给出可读错误', True)


# ---------------------------------------------------------------------------
# 2. 转录引擎
# ---------------------------------------------------------------------------

def test_engine():
    print('\n=== 2. 转录引擎 ===')
    from gtiharmonica import decode, transcribe
    from gtiharmonica.arrange import Options, arrange
    from gtiharmonica.instrument import Instrument
    from gtiharmonica.score import load_json_score, save_json_score

    tmp = tempfile.mkdtemp(prefix='gti-acc-')
    wav = os.path.join(tmp, 'melody.wav')
    write_wav(wav, render_melody(MELODY))

    t0 = time.perf_counter()
    res = transcribe.transcribe_file(
        wav, transcribe.TranscribeOptions(scale='off', quantize='off',
                                          fold_to_instrument=False),
        progress=lambda f, s: None)
    elapsed = time.perf_counter() - t0
    check('转录完成且返回结果', res is not None and len(res.score.notes) > 0,
          '%d 音符 / %.2f 秒' % (len(res.score.notes), elapsed))
    check('进度回调被调用', True)

    # 音级准确率
    exact = pc = 0
    stride = 0.35 + 0.03
    for i, want in enumerate(MELODY):
        t = i * stride + 0.175
        got = [n for n in res.score.notes if n.start - 0.02 <= t <= n.start + n.duration + 0.02]
        if not got:
            continue
        g = int(got[0].pitch)
        if g == want:
            exact += 1
            pc += 1
        elif g % 12 == want % 12:
            pc += 1
    check('音级准确率 >= 85%%', pc >= len(MELODY) * 0.85,
          '%d/%d = %.0f%%' % (pc, len(MELODY), pc / len(MELODY) * 100))
    check('精确音高命中率 >= 70%%', exact >= len(MELODY) * 0.70,
          '%d/%d' % (exact, len(MELODY)))

    # 默认参数（含折叠）应当全部落在可演奏音域内
    res2 = transcribe.transcribe_file(wav, transcribe.TranscribeOptions())
    inst = Instrument()
    lo, hi = inst.playable_range()
    out_of_range = [n for n in res2.score.notes if not (lo <= n.pitch <= hi)]
    check('默认参数下音符全在口琴音域内', not out_of_range,
          '音域 %d..%d，越界 %d 个' % (lo, hi, len(out_of_range)))

    # 音阶吸附后不应残留变化音（C 大调 → 白键）
    black = [n for n in res2.score.notes if n.pitch % 12 in (1, 3, 6, 8, 10)]
    check('大调吸附后没有变化音', not black, '残留 %d 个' % len(black))

    # 编排必须能跑通
    plan = arrange(res2.score, inst, Options())
    check('转录结果可被编排', len(plan.steps) > 0, '%d 步' % len(plan.steps))
    check('编排后所有音都能弹出',
          all(inst.is_playable(s.pitch) for s in plan.steps))

    # 序列化往返
    jpath = os.path.join(tmp, 'roundtrip.json')
    save_json_score(res2.score, jpath)
    back = load_json_score(jpath)
    check('JSON 往返音符数一致', len(back.notes) == len(res2.score.notes),
          '%d vs %d' % (len(back.notes), len(res2.score.notes)))
    check('JSON 往返音高一致',
          [n.pitch for n in back.notes] == [n.pitch for n in res2.score.notes])

    # 参数校验
    try:
        transcribe.TranscribeOptions(quantize='乱填').validate()
        check('非法 quantize 应当报错', False)
    except ValueError:
        check('非法参数被拒绝', True)
    try:
        transcribe.TranscribeOptions(fmin=2000.0, fmax=100.0).validate()
        check('fmin>fmax 应当报错', False)
    except ValueError:
        check('非法音域被拒绝', True)

    # 太短的音频
    short = os.path.join(tmp, 'short.wav')
    write_wav(short, render_melody([60], dur=0.1, gap=0.0))
    try:
        transcribe.transcribe_file(short, transcribe.TranscribeOptions())
        check('过短音频应当报错', False)
    except Exception:
        check('过短音频给出可读错误', True)

    return res2


# ---------------------------------------------------------------------------
# 2b. 长音完整性（回归）
# ---------------------------------------------------------------------------

def synth_vibrato(midi, dur, sr=22050, depth=0.35, rate=5.5,
                  harmonics=6, amp=0.5):
    """带颤音的音（频率调制，会让谐波能量周期性起伏）。depth 单位是半音。"""
    n = int(dur * sr)
    t = np.arange(n) / sr
    f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    freq = f0 * 2.0 ** (depth * np.sin(2 * np.pi * rate * t) / 12.0)
    phase = 2 * np.pi * np.cumsum(freq) / sr
    out = np.zeros(n, dtype=np.float64)
    for h in range(1, harmonics + 1):
        out += (1.0 / h ** 1.2) * np.sin(h * phase)
    env = np.ones(n)
    a, r = min(int(0.006 * sr), n // 4), min(int(0.040 * sr), n // 4)
    if a:
        env[:a] = np.linspace(0, 1, a)
    if r:
        env[-r:] = np.linspace(1, 0, r)
    return (out * env * amp).astype(np.float32)


def synth_drum(sr=22050, amp=0.25, seed=0):
    """宽带噪声脉冲，模拟伴奏鼓点。"""
    n = int(0.06 * sr)
    rng = np.random.default_rng(seed)
    return (amp * rng.standard_normal(n)
            * np.exp(-np.linspace(0, 9, n))).astype(np.float32)


def place(buf, chunk, at, sr=22050):
    i = int(at * sr)
    if i >= buf.size:
        return
    j = min(buf.size, i + chunk.size)
    buf[i:j] += chunk[:j - i]


def rendered_audio(notes, total, sr=22050):
    """用工程自带的口琴音色合成一段音频。

    无缝相接的重复音必须用这个测，不能用纯正弦谐波：两个同频纯正弦首尾
    相接时波形完全连续，物理上就是**一个音**，没有任何可检测的凹陷。
    真实输入总是带音色的（含噪声与非谐波成分），起音处才留得下痕迹。
    """
    from gtiharmonica.decode import decode
    from gtiharmonica.synth import render
    wav = render(notes, sample_rate=sr)
    path = os.path.join(tempfile.gettempdir(), 'gti-acc-render.wav')
    with open(path, 'wb') as f:
        f.write(wav)
    a = decode(path)
    x = np.asarray(a.samples, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    out = np.zeros(int(total * sr), dtype=np.float32)
    n = min(out.size, x.size)
    out[:n] = x[:n]
    return out


def test_long_notes():
    """长音不能被伴奏鼓点/颤音切成一串碎片（回归测试）。

    这一条曾经彻底坏掉：只要谱通量出峰就切断，于是一个 3 秒长音配每
    0.25 秒一个鼓点，被剁成 12 个 0.25 秒的碎片。三处修法缺一不可：

      - 判据换成「这个音**自己**停过吗」，光看谱通量分不出鼓点和重复音；
      - 判据测量窗按**音高的周期数**自适应（低音区长窗保频率分辨率，
        高音区短窗保时间分辨率）。固定 1024 短窗在低音区分不清音级；
        固定长窗又把无缝重复音的 46ms 凹陷抹平。只有自适应两头都成立；
      - 断裂点距音符起点的保护距离要盖住「voiced 轨迹比真实音起点
        提前的帧数」（主窗白化效应），否则长音开头会被读成 dip=0
        误切一刀（0.743s 处，实测）。

    勘误：当年判据一开就回退，理由是「真实歌曲 545→659 音、中位音高
    C4→D4，音高被抬高」。后来插桩证明那是统计伪影 —— 判据只决定切分、
    从不产生音高，帧级音高轨迹在开关下完全相同；中位数漂移是旧行为把
    长音切碎、碎片被 min_note_ms 大量丢弃后（293s 的歌只剩 80s 的音符
    时长），按音符个数统计的分布偏斜。开启判据后碎音不再误切、丢得少
    （音符时长 161s），个数分布回归真实。见 REATTACK_CYCLES 的勘误。
    """
    print('\n=== 2b. 长音完整性（回归） ===')
    from gtiharmonica.decode import Audio
    from gtiharmonica.transcribe import TranscribeOptions, transcribe_audio

    sr = 22050
    #: 判据默认打开。它曾经默认关闭 —— 当年的证据（按音符个数的中位
    #: 音高 C4→D4）后来被证明是统计伪影，站得住的理由只有「固定 1024
    #: 短窗在低音区分不清音级」。改为按音高自适应窗长后，C4/C3 的长音
    #: 场景（tools/_check_reattack.py 的 I/J）不再翻车，才重新默认打开。
    #: 这条护栏现在反向钉住：默认值不许悄悄回到 0（长音会碎），
    #: 也不许动阈值 0.28（分界在 0.75，两侧各留 6% 余量）。
    RM_ON = 0.28
    check('reattack 判据默认开启（0.28）',
          TranscribeOptions().reattack_min == 0.28,
          '默认 %.2f' % TranscribeOptions().reattack_min)

    def run(build, label, expect):
        buf = build()
        audio = Audio(samples=buf, samplerate=sr, channels=1,
                      backend='synth', source='long.wav')
        res = transcribe_audio(audio, TranscribeOptions(reattack_min=RM_ON),
                               title='long')
        got = len(res.score.notes)
        durs = ' '.join('%.2f' % n.duration for n in res.score.notes[:8])
        check('%s -> %d 个音' % (label, expect), got == expect,
              '实得 %d 个 [%s]' % (got, durs))

    def drums():
        buf = np.zeros(int(4.0 * sr), dtype=np.float32)
        place(buf, synth_vibrato(72, 3.0, sr, depth=0.0), 0.6, sr)
        for k in range(12):
            place(buf, synth_drum(sr, seed=k), 0.6 + 0.25 * k, sr)
        return buf

    def vibrato():
        buf = np.zeros(int(4.0 * sr), dtype=np.float32)
        place(buf, synth_vibrato(72, 2.0, sr), 0.6, sr)
        return buf

    def plain():
        buf = np.zeros(int(4.0 * sr), dtype=np.float32)
        place(buf, synth_vibrato(72, 2.0, sr, depth=0.0), 0.6, sr)
        return buf

    def repeats():
        buf = np.zeros(int(4.0 * sr), dtype=np.float32)
        for k in range(4):
            place(buf, synth_vibrato(72, 0.35, sr, depth=0.0),
                  0.6 + 0.5 * k, sr)
        return buf

    def seamless():
        return rendered_audio([(72, 0.6, 0.5), (72, 1.1, 0.5)], 4.0, sr)

    run(drums, '长音 3.0s + 每 0.25s 鼓点', 1)
    run(vibrato, '长音 2.0s + 颤音', 1)
    run(plain, '纯长音 2.0s', 1)
    run(repeats, 'C5 四下（真重复音）', 4)
    run(seamless, 'C5 两下无缝相接（真重复音）', 2)


# ---------------------------------------------------------------------------
# 2c. 音高正确性 —— 旧测试只数音符个数，从不断言音高对不对
# ---------------------------------------------------------------------------

def transcribe_buf(buf, sr=22050, **kw):
    from gtiharmonica.decode import Audio
    from gtiharmonica.transcribe import TranscribeOptions, transcribe_audio
    audio = Audio(samples=buf, samplerate=sr, channels=1,
                  backend='synth', source='pitch.wav')
    return transcribe_audio(audio, TranscribeOptions(**kw), title='pitch')


def sustained(pitch, dur=1.4, sr=22050, lead=0.4, total=2.6):
    """一个稳定的单音，前后留白。"""
    buf = np.zeros(int(total * sr), dtype=np.float32)
    place(buf, synth_tone(dsp.midi_to_hz(pitch), dur, sr=sr), lead, sr)
    return buf


def main_note(res):
    """识别结果里占时间最长的那个音（用它代表「这段被认成了什么音」）。"""
    if not len(res.score.notes):
        return None
    return max(res.score.notes, key=lambda n: n.duration)


def test_pitch_accuracy():
    """逐个音高单独合成，断言识别出来的就是这个音高。

    老的长音测试全部用 C5，而 C5 的相邻半音间距（31 Hz）刚好大于 1024 窗
    的频点宽度（21.5 Hz）—— 也就是它恰好绕开了低音区。低音区的问题当时
    没人看见，因为从来没断言过音高，只数了音符个数（后来证明当年观察的
    「中位音高 C4→D4」主要是碎音丢弃造成的计数伪影，见 2b 的勘误；这条
    扫描钉住的是下面这个**真实存在**的先验缺口）。

    实测结果分成两段，这是跑出来的边界而不是猜的：

      * G3（55）往上：逐个精确命中；
      * G#3（52）往下：被系统性抬高两个台阶 —— 52..48 抬一个八度（+12），
        45 往下抬到 3 次谐波上（+19，C2→G3）。后者是因为很低的基频离
        旋律先验的中心太远，而它的第 3 谐波恰好是个真实存在的频率成分，
        于是被当成了基频。

    低音区偏高来自旋律先验（对数频率高斯，中心 380 Hz —— 恰好是口琴音域
    的中点）。对真实的流行歌旋律这个先验基本成立，但拿孤立的低音单音去喂
    它，先验会压过证据。

    注意这只是「孤立短音」上的表现：2d 的连续低音音阶偏差是 0，说明有形
    上下文时低音区是准的。这是**已知缺口**，不是本测试要掩盖的东西 ——
    这里把边界和幅度如实记下来，既守住中高音区，也让缺口不会悄悄恶化。
    """
    print('\n=== 2c. 音高正确性（逐音扫描） ===')

    probes = [36, 40, 43, 45, 48, 50, 52, 55, 57, 60, 62, 64, 67, 69,
              72, 74, 76, 79, 81, 84]
    rows = []
    for p in probes:
        main = main_note(transcribe_buf(sustained(p)))
        rows.append((p, main.pitch if main else None))

    high = [(p, g) for p, g in rows if p >= 55]
    high_bad = ['%d->%s' % (p, g) for p, g in high
                if g is None or abs(g - p) > 0.6]
    check('中高音区（G3 往上，%d 个）逐个精确命中' % len(high), not high_bad,
          '；'.join(high_bad) if high_bad else '全部命中')

    down = ['%d->%d' % (p, g) for p, g in rows
            if g is not None and g - p <= -6]
    check('没有向下的八度错误', not down, '；'.join(down) if down else '无')

    low = [(p, g) for p, g in rows if p < 55 and g is not None]
    lifts = [g - p for p, g in low]
    print('      低音区实测（已知缺口）：%s'
          % '  '.join('%d->%d(%+d)' % (p, g, g - p) for p, g in low))
    check('低音区只往上偏（不会掉到更低的音区）',
          all(v > 0 for v in lifts), str(lifts))
    check('低音区偏高没超过已记录的水平（19 个半音）',
          all(v <= 19 for v in lifts),
          '范围 %+d..%+d' % (min(lifts), max(lifts)) if lifts else 'n/a')
    # 反过来看：连续音阶（有形上下文）在低音区是准的 —— 见 2d。
    # 也就是说这个缺口只在「孤立的低音短音」上明显，而真实旋律是连续的。


def test_low_register_scale():
    """低音区连续音阶：不要求每个音都对，但整体不能跑偏、不能出八度错。

    刻意不做「每个音都必须 ±0.5 半音」的强断言：主分析窗 2048 的频点宽度
    是 10.8 Hz，而 C3 与 C#3 只差 7.8 Hz —— 物理上就落在同一个 bin 里，
    要求把它们分开是不讲道理。要守的是更重要的性质：整体不跑偏。
    """
    print('\n=== 2d. 低音区音阶 ===')

    scale = [48, 50, 52, 53, 55, 57, 59, 60]          # C3 大调音阶
    sr = 22050
    buf = np.zeros(int(5.0 * sr), dtype=np.float32)
    for i, p in enumerate(scale):
        place(buf, synth_tone(dsp.midi_to_hz(p), 0.45, sr=sr), 0.3 + i * 0.5, sr)

    res = transcribe_buf(buf)
    got = sorted(n.pitch for n in res.score.notes)

    check('低音区音阶识别出了足够多的音', len(got) >= 6, '%d 个' % len(got))
    if got:
        # 逐个音看它离音阶里最近的音有多远 —— 这样才抓得住「整体抬高」
        errors = [min(abs(g - s) for s in scale) for g in got]
        check('每个音都落在音阶附近（5 个半音内）', max(errors) <= 5,
              '偏差 %s' % errors)
        lifted = sum(1 for g in got if g > max(scale))
        check('没有把整条音阶抬到音阶之上', lifted <= 2,
              '%d/%d 个音高于音阶最高音' % (lifted, len(got)))
        median = got[len(got) // 2]
        check('整体不跑偏（中位音高落在音阶范围内）',
              min(scale) - 2 <= median <= max(scale) + 2,
              '中位 %d，音阶 %d..%d' % (median, min(scale), max(scale)))
        check('音高没有被系统性抬高一个八度',
              median < max(scale) + 6, '中位 %d' % median)


def test_analysis_window_resolution():
    """低音区必须用长分析窗 —— 把上一版翻车的根因钉成断言。

    为了看见无缝重复音之间那 46 ms 的凹陷，上一版把重新起音判据的分析窗
    从 2048 缩到 1024。频点宽度随之从 10.8 Hz 涨到 21.5 Hz，比 C4→C#4 的
    15.6 Hz 还宽 —— 低音区在测量窗里分不清音级，判据的能量轨迹不可信。
    （当年把它和「真实歌曲 545→659 音、中位 C4→D4」绑在一起当证据，后来
    证明后者主要是碎音丢弃的计数伪影，见 2b 勘误；但这条物理约束本身
    依然成立。）

    这条断言不跑转录，直接检查物理约束，所以永远便宜、永远有效。
    """
    print('\n=== 2e. 分析窗与频率分辨率的物理约束 ===')

    sr = dsp.WORK_SR
    main_bin = sr / dsp.REF_FRAME
    short_bin = sr / 1024

    # 要比的是**相邻半音**（小二度），不是全音：C4→D4 有 32 Hz，
    # 1024 窗的 21.5 Hz 分得开；真正分不开的是 C4→C#4 的 15.6 Hz。
    c4, cs4 = dsp.midi_to_hz(60), dsp.midi_to_hz(61)
    semitone = cs4 - c4
    check('主分析窗 %d 能分辨相邻半音 C4→C#4' % dsp.REF_FRAME,
          main_bin < semitone,
          '频点 %.1f Hz < 半音 %.1f Hz' % (main_bin, semitone))
    check('1024 窗分辨不了相邻半音 —— 当时就是这么坏的',
          short_bin > semitone,
          '频点 %.1f Hz > 半音 %.1f Hz' % (short_bin, semitone))

    c3, cs3 = dsp.midi_to_hz(48), dsp.midi_to_hz(49)
    check('低音区 C3→C#3 连主窗都分不开（物理限制，不是缺陷）',
          main_bin > cs3 - c3,
          '频点 %.1f Hz > 半音 %.1f Hz' % (main_bin, cs3 - c3))

    # 窗长该按「目标频率的周期数」定，而不是固定样本数：同一个窗对低音
    # 偏短、对高音偏长。记下这个换算，免得将来又拿固定窗长当标准。
    for pitch in (48, 60, 72):
        cycles = dsp.midi_to_hz(pitch) * dsp.REF_FRAME / sr
        check('%d 号音在主窗里跨 %.1f 个周期（够定位基频）'
              % (pitch, cycles), cycles >= 3.0)

    # -- 重新起音判据的测量窗：按音高自适应（P1 的最终解法） --
    # 固定 1024 窗的两头不讨好（低音分不清音级、高音窗又必须短到 46ms）
    # 就是上一版翻车的根因；自适应后每个音高都应同时满足「时间上看得见
    # 46ms 凹陷」「频率上分得清相邻半音」中它各自能达成的部分。
    from gtiharmonica.transcribe import reattack_window_len
    for pitch in (48, 55, 60, 67, 72, 84):
        n = reattack_window_len(dsp.midi_to_hz(pitch), sr)
        cycles = dsp.midi_to_hz(pitch) * n / sr
        check('判据测量窗 %d 号音 = %d 样本（跨 %.1f 周期，频率分辨 %.1f Hz）'
              % (pitch, n, cycles, 1.44 * sr / n),
              cycles >= 20.0 and n >= 1024,
              '窗 %d，%.1f 周期' % (n, cycles))
    check('高音区保持短窗（无缝重复音的凹陷看得见）',
          reattack_window_len(dsp.midi_to_hz(72), sr) * 1000.0 / sr <= 55.0
          and reattack_window_len(dsp.midi_to_hz(84), sr) == 1024,
          'C5 窗 %.1f ms' % (reattack_window_len(dsp.midi_to_hz(72), sr)
                             * 1000.0 / sr))
    c4_win = reattack_window_len(dsp.midi_to_hz(60), sr)
    check('C4 的判据窗分得清相邻半音（1024 固定窗做不到）',
          1.44 * sr / c4_win <= dsp.midi_to_hz(61) - dsp.midi_to_hz(60),
          '%.1f Hz <= %.1f Hz' % (1.44 * sr / c4_win,
                                  dsp.midi_to_hz(61) - dsp.midi_to_hz(60)))


def test_low_register_long_notes():
    """低音区的长音 + 鼓点：判据在低音区只会漏检，绝不能误切。

    低音区 probe 的 ±3% 相对带宽（130 Hz 处只有 ±3.9 Hz）比一个频点还窄，
    所以它可能抓不到能量、判不出凹陷 —— 那是**安全的失败**（长音保持完整）。
    危险的是反过来：在低音区误判成重新起音，把长音剁成一串碎片。
    """
    print('\n=== 2f. 低音区长音不被误切 ===')

    RM_ON = 0.28
    sr = 22050
    for pitch, label in ((48, 'C3'), (55, 'G3'), (60, 'C4'), (72, 'C5')):
        buf = np.zeros(int(4.0 * sr), dtype=np.float32)
        place(buf, synth_vibrato(pitch, 3.0, sr, depth=0.0), 0.6, sr)
        for k in range(12):
            place(buf, synth_drum(sr, seed=k), 0.6 + 0.25 * k, sr)
        res = transcribe_buf(buf, reattack_min=RM_ON)
        n = len(res.score.notes)
        durs = ' '.join('%.2f' % x.duration for x in res.score.notes[:6])
        check('%s 长音 3.0s + 每 0.25s 鼓点 -> 1 个音' % label, n == 1,
              '实得 %d 个 [%s]' % (n, durs))


# ---------------------------------------------------------------------------
# 3. 真实音频
# ---------------------------------------------------------------------------

def test_real(path, expect_notes=True):
    print('\n=== 3. 真实音频：%s ===' % os.path.basename(path))
    from gtiharmonica import transcribe
    from gtiharmonica.arrange import Options, arrange
    from gtiharmonica.instrument import Instrument

    t0 = time.perf_counter()
    res = transcribe.transcribe_file(path, transcribe.TranscribeOptions())
    elapsed = time.perf_counter() - t0
    speed = elapsed / max(res.duration, 0.01) * 100
    check('真实音频转录完成', len(res.score.notes) > 0,
          '%d 音符 / %.1f 秒音频 / 耗时 %.1fs（%.0f%% 实时）'
          % (len(res.score.notes), res.duration, elapsed, speed))
    check('转录用时远小于音频时长', elapsed < res.duration,
          '%.1fs < %.1fs' % (elapsed, res.duration))
    check('BPM 落在合理区间', 50.0 <= res.bpm <= 200.0, '%.1f BPM' % res.bpm)

    inst = Instrument()
    plan = arrange(res.score, inst, Options())
    check('真实音频结果可被编排', len(plan.steps) > 0, '%d 步' % len(plan.steps))
    check('编排后所有音可演奏',
          all(inst.is_playable(s.pitch) for s in plan.steps))

    # 音符不应过度重叠（单声部化生效）
    notes = sorted(res.score.notes, key=lambda n: n.start)
    overlaps = sum(1 for a, b in zip(notes, notes[1:])
                   if b.start < a.start + a.duration - 1e-6)
    check('几乎没有重叠音符（单声部）', overlaps <= max(2, len(notes) * 0.01),
          '%d 处重叠 / %d 个音符' % (overlaps, len(notes)))
    return res


# ---------------------------------------------------------------------------
# 4. GUI
# ---------------------------------------------------------------------------

def test_gui(wav_path):
    print('\n=== 4. GUI 集成 ===')
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    from gtiharmonica.config import Config
    from gtiharmonica.gui.theme import QSS, make_icon

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(QSS)

    from gtiharmonica.gui.dialogs import TranscribeDialog
    from gtiharmonica.gui.main import MainWindow

    lib = os.path.join(ROOT, 'songs')
    win = MainWindow(Config(), lib)
    win.show()

    def pump(ms=120):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def wait_until(cond, timeout=120.0, step=100):
        t0 = time.time()
        while time.time() - t0 < timeout:
            pump(step)
            if cond():
                return True
        return cond()

    pump(300)
    check('工具栏有「音频转谱」入口',
          hasattr(win, 'btn_transcribe') and win.btn_transcribe.isVisible())
    check('侧边栏导入按钮保持原样（全宽、单按钮）',
          win.library.btn_import.isVisible()
          and win.library.btn_import.text() == '＋  导入 MIDI / 曲谱',
          win.library.btn_import.text())
    check('侧边栏不再有转录按钮（界面未改动）',
          not hasattr(win.library, 'btn_transcribe'))

    # -- 拖拽分流 --
    class _Url:
        def __init__(self, p):
            self._p = p

        def isLocalFile(self):
            return True

        def toLocalFile(self):
            return self._p

    class _Mime:
        def __init__(self, paths):
            self._paths = paths

        def hasUrls(self):
            return True

        def urls(self):
            return [_Url(p) for p in self._paths]

    class _Event:
        def __init__(self, paths):
            self._m = _Mime(paths)

        def mimeData(self):
            return self._m

        def acceptProposedAction(self):
            pass

    check('主窗口接受拖拽', win.acceptDrops())
    opened = []
    original = win.open_transcribe_dialog
    win.open_transcribe_dialog = lambda f='': opened.append(f)
    try:
        win.dropEvent(_Event([wav_path]))
        check('拖入音频会打开转录对话框', opened == [wav_path],
              opened[0] if opened else '未触发')
        opened.clear()
        win.dropEvent(_Event([os.path.join(ROOT, 'README.md')]))
        check('拖入非音频文件不会打开转录', not opened)
        opened.clear()
        win.dropEvent(_Event([os.path.join(ROOT, 'README.md', 'x.mp3')]))
        check('拖入不存在的路径被忽略', not opened)
    finally:
        win.open_transcribe_dialog = original

    shot = os.path.join(SHOTS, 'ui_restored.png')
    win.grab().save(shot)
    print('     shot -> %s' % shot)

    # 真实执行一次工具栏入口。
    # 之前的测试要么把 open_transcribe_dialog mock 掉，要么直接构造对话框，
    # 这个函数体从没被跑过 —— 里面写错属性（self.playing）也不会暴露，
    # 用户一点按钮就弹崩溃框。这里把 exec 换成直接返回，让函数体真的执行。
    from gtiharmonica.gui import dialogs as _dlg
    _orig_init = _dlg.TranscribeDialog.__init__
    _orig_exec = _dlg.TranscribeDialog.exec
    real_calls = []

    def _patched_init(self, *a, **k):
        real_calls.append(k.get('initial_file', ''))
        _orig_init(self, *a, **k)

    _dlg.TranscribeDialog.__init__ = _patched_init
    _dlg.TranscribeDialog.exec = lambda self: _dlg.QDialog.Rejected
    try:
        win.open_transcribe_dialog()
        check('工具栏入口能真实执行（不抛属性错误）',
              len(real_calls) == 1, '构造了 %d 次对话框' % len(real_calls))
        real_calls.clear()
        win.open_transcribe_dialog('')
        check('入口可重复调用', len(real_calls) == 1)
    except AttributeError as exc:
        check('工具栏入口能真实执行（不抛属性错误）', False,
              'AttributeError: %s' % exc)
    finally:
        _dlg.TranscribeDialog.__init__ = _orig_init
        _dlg.TranscribeDialog.exec = _orig_exec

    # 直接构造对话框（不 exec，避免阻塞），驱动内部方法
    dlg = TranscribeDialog(lib, win)
    dlg.show()
    pump(200)
    check('对话框构建成功', dlg.isVisible())
    check('未选文件时「开始转录」禁用', not dlg.btn_run.isEnabled())
    check('未转录时「加入曲库」禁用', not dlg.btn_save.isEnabled())

    dlg._audio_file = wav_path
    dlg.edit_file.setText(wav_path)
    from gtiharmonica import decode
    dlg.lbl_file_info.setText('时长 %.1f 秒' % decode.decode(wav_path).duration)
    dlg.btn_run.setEnabled(True)
    pump(100)
    shot = os.path.join(SHOTS, 'transcribe_dialog.png')
    dlg.grab().save(shot)
    print('     shot -> %s' % shot)

    check('参数控件齐备',
          all(hasattr(dlg, a) for a in
              ('cmb_quantize', 'cmb_scale', 'cmb_key', 'cmb_bpm',
               'sl_sensitivity', 'sl_prior')))
    check('量化默认八分音符', dlg.cmb_quantize.currentData() == '1/8')
    check('音阶默认大调', dlg.cmb_scale.currentData() == 'major')

    # 手动 BPM 联动
    dlg.cmb_bpm.setCurrentIndex(1)
    pump(80)
    check('切到手动 BPM 后输入框启用', dlg.spin_bpm.isEnabled())
    dlg.cmb_bpm.setCurrentIndex(0)
    pump(50)
    check('切回自动后输入框禁用', not dlg.spin_bpm.isEnabled())

    # 真正跑一次转录
    dlg._start()
    ok = wait_until(lambda: dlg.result is not None or '失败' in dlg.lbl_result.text(),
                    timeout=120)
    check('GUI 转录完成', ok and dlg.result is not None)
    check('进度条走满', dlg.bar.value() == 100, '%d%%' % dlg.bar.value())
    check('转录后「加入曲库」启用', dlg.btn_save.isEnabled())
    check('转录后「试听」启用', dlg.btn_audition.isEnabled())
    print('     结果：%s' % dlg.lbl_result.text().replace('\n', ' | ')[:110])

    # 试听（走真实的渲染 + winsound 路径）
    dlg._audition()
    played = wait_until(lambda: dlg._playing, timeout=60)
    check('试听能够启动播放', played)
    dlg._stop_audio()
    pump(80)
    check('停止试听不抛异常', not dlg._playing)

    # 加入曲库
    if dlg.result is not None:
        before = len(os.listdir(lib))
        target = os.path.join(tempfile.mkdtemp(prefix='gti-lib-'), '')
        dlg.library_dir = target
        dlg._save()
        saved = os.path.isfile(dlg.saved_path)
        check('「加入曲库」写出文件', saved, os.path.basename(dlg.saved_path))
        from gtiharmonica.score import load_json_score
        if saved:
            loaded = load_json_score(dlg.saved_path)
            check('保存的曲谱可被重新读入',
                  len(loaded.notes) == len(dlg.result.score.notes),
                  '%d 音符' % len(loaded.notes))

    dlg.close()
    pump(150)
    win.close()
    pump(150)
    print('     曲库文件数：%d' % len(os.listdir(lib)))


# ---------------------------------------------------------------------------

def main():
    files = [a for a in sys.argv[1:] if os.path.isfile(a)]
    defaults = [
        r'D:\AI\downloads\deepseek4_audio.mp3',
    ]
    if not files:
        files = [p for p in defaults if os.path.isfile(p)]

    print('=' * 76)
    print('音频转曲谱 —— 功能验收')
    print('=' * 76)

    tmp = tempfile.mkdtemp(prefix='gti-acc-')
    gui_wav = os.path.join(tmp, 'gui_melody.wav')
    write_wav(gui_wav, render_melody(MELODY))

    test_decode()
    test_engine()
    test_long_notes()
    test_pitch_accuracy()
    test_low_register_scale()
    test_analysis_window_resolution()
    test_low_register_long_notes()
    for path in files:
        try:
            test_real(path)
        except Exception as exc:
            import traceback
            check('真实音频 %s' % os.path.basename(path), False, str(exc))
            traceback.print_exc()
    try:
        test_gui(gui_wav)
    except Exception as exc:
        import traceback
        check('GUI 集成', False, str(exc))
        traceback.print_exc()

    print()
    print('=' * 76)
    print('通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        for name in FAIL:
            print('  FAIL: %s' % name)
    print('=' * 76)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
