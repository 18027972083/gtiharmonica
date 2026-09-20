# -*- coding: utf-8 -*-
"""音频转曲谱自检：模块级 + 界面级（离屏）。

覆盖：
  1. 内置模型完整性
  2. transcribe_audio 直接调用（30 秒片段 → 音符数量/音域合理性）
  3. 音频加载与重采样（44.1k / 48k 都给同样的帧数）
  4. 静音切片 + 超长强制切分兜底（造一段没有静音的长音频，必须切得出来）
  5. 界面：拖入音频走完整流程 → 曲库多出一首可解析的曲谱
  6. 取消：worker 中途 cancel 不会写出半成品

用法：python tools/_check_audio2score.py
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np                                          # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)
APP.setStyle('Fusion')

from gtiharmonica.audio2score import (SAMPLE_RATE, Slicer,      # noqa: E402
                                      GameOnnx, load_audio,
                                      model_available, transcribe_audio)
from gtiharmonica.gui.theme import apply_theme              # noqa: E402
apply_theme(APP, 'light')

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = '') -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print('  ok   %s%s' % (name, ('  [%s]' % detail) if detail else ''))
    else:
        FAIL += 1
        print('  FAIL %s%s' % (name, ('  [%s]' % detail) if detail else ''))
    return ok


AUDIO = 'D:/AI/tools/uvr/separated/htdemucs/yuzhao/vocals.wav'
CLIP = os.path.join(ROOT, 'build', '_a2s_clip.wav')


def make_clip(seconds: float = 30.0, source: str = AUDIO) -> str:
    """从素材里截一段做测试音频（没有素材就自己合成一段旋律）。"""
    os.makedirs(os.path.dirname(CLIP), exist_ok=True)
    if os.path.isfile(source):
        import soundfile as sf
        data, sr = sf.read(source, dtype='float32', always_2d=True)
        n = int(sr * seconds)
        sf.write(CLIP, data[:n], sr)
        return CLIP
    # 合成：一段带颤音的旋律（440/494/523 Hz）
    sr = 44100
    t = np.arange(int(sr * seconds), dtype=np.float32) / sr
    y = np.zeros_like(t)
    for i, f in enumerate((440.0, 493.88, 523.25, 440.0)):
        seg = (t >= i * seconds / 4) & (t < (i + 1) * seconds / 4)
        y[seg] = 0.3 * np.sin(2 * np.pi * f * t[seg])
    import soundfile as sf
    sf.write(CLIP, y, sr)
    return CLIP


def case_model() -> None:
    print('\n[1] 内置模型')
    check('模型文件齐全', model_available(), '')
    import onnxruntime
    check('onnxruntime 可用', bool(onnxruntime.__version__),
          onnxruntime.__version__)


def case_resample() -> None:
    print('\n[2] 重采样对齐')
    from gtiharmonica.audio2score import resample_fft
    sr_a, sr_b = 48000, 44100
    t = np.arange(sr_a, dtype=np.float32) / sr_a
    x = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    y = resample_fft(x, sr_a, sr_b)
    check('48k → 44.1k 长度正确', abs(len(y) - 44100) <= 2, str(len(y)))
    # 频率保持：过零率应接近 880/秒
    zc = np.sum(np.diff(np.signbit(y)))
    check('音高不变（过零率接近 2×440）', abs(zc - 880) <= 4, str(zc))


def case_slicer_fallback() -> None:
    print('\n[3] 切片：静音切分 + 超长强制兜底')
    sr = SAMPLE_RATE
    slicer = Slicer(sr)
    # 3a) 两段响亮音频夹一段静音 → 应该切成两段
    quiet = np.zeros(int(sr * 1.0), dtype=np.float32)
    loud = (0.2 * np.sin(2 * np.pi * 440 * np.arange(int(sr * 3.0)) / sr)).astype(np.float32)
    wave = np.concatenate([loud, quiet, loud])
    chunks = slicer.slice(wave)
    check('有静音时按静音切分', len(chunks) >= 2, '%d 段' % len(chunks))
    # 3b) 全程响亮（没有静音）→ 必须靠兜底切成 ≤55 秒的段
    long_loud = (0.2 * np.sin(2 * np.pi * 440 * np.arange(int(sr * 130.0)) / sr)).astype(np.float32)
    chunks2 = slicer.slice(long_loud)
    max_sec = max(len(c['waveform']) / sr for c in chunks2)
    check('无静音的长音频也能切出来', len(chunks2) >= 3, '%d 段' % len(chunks2))
    check('每段不超过 55 秒（兜底生效）', max_sec <= 55.0, '%.1f 秒' % max_sec)
    # 3c) 偏移量连续递增，拼接后总长不缩水
    total = max(c['offset'] + len(c['waveform']) / sr for c in chunks2)
    check('切分后总时长覆盖原音频', abs(total - 130.0) < 1.0, '%.1f 秒' % total)


def case_module_end_to_end() -> None:
    print('\n[4] 模块级端到端（30 秒片段）')
    clip = make_clip(30.0)
    wave = load_audio(clip)
    check('音频加载到 44.1k 单声道', wave.ndim == 1 and abs(len(wave) / SAMPLE_RATE - 30) < 0.5,
          '%.1f 秒' % (len(wave) / SAMPLE_RATE))
    stages = []
    t0 = time.time()
    score = transcribe_audio(clip, progress=lambda s, d, t: stages.append(s))
    el = time.time() - t0
    check('识别出音符', len(score.notes) > 0, '%d 个' % len(score.notes))
    if len(score.notes):
        pitches = [n.pitch for n in score.notes]
        check('音高在合理范围（MIDI 30..90）',
              min(pitches) >= 30 and max(pitches) <= 90,
              '%d..%d' % (min(pitches), max(pitches)))
        check('时长覆盖原音频（±20%）',
              abs(score.duration - 30.0) < 6.0, '%.1f 秒' % score.duration)
        check('音符时间递增', all(a.start <= b.start
                                  for a, b in zip(score.notes, score.notes[1:])))
    check('进度回调有「加载音频」与「推理」', '加载音频' in stages and '推理' in stages,
          ','.join(sorted(set(stages))))
    print('       （30 秒音频耗时 %.1f 秒）' % el)


def case_gui_flow() -> None:
    print('\n[5] 界面：拖入音频 → 完整流程 → 入曲库')
    from PySide6.QtWidgets import QMessageBox
    from gtiharmonica.config import Config
    from gtiharmonica.gui.main import MainWindow
    from gtiharmonica.score import load_score

    library = os.path.join(ROOT, 'build', '_a2s_lib')
    os.makedirs(library, exist_ok=True)
    before = set(os.listdir(library))

    win = MainWindow(Config(), library)
    win.show()
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

    clip = make_clip(30.0)
    win.transcribe_audio_file(clip)

    deadline = time.time() + 240
    while time.time() < deadline:
        APP.processEvents()
        worker = getattr(win, '_transcribe_worker', None)
        if worker is not None and worker.isFinished():
            break
        time.sleep(0.05)
    APP.processEvents()
    time.sleep(0.3)
    APP.processEvents()

    after = set(os.listdir(library))
    new = sorted(after - before)
    check('曲库多出一首曲谱', len(new) == 1, ','.join(new))
    if new:
        path = os.path.join(library, new[0])
        score = load_score(path)
        check('新曲谱能解析且音符 > 0', len(score.notes) > 0, '%d 个音符' % len(score.notes))
        check('文件名带来源标注', '(音频转谱)' in new[0], new[0])
        check('标题取自文件名', 'clip' in score.title, score.title)
    win.close()


def case_cancel() -> None:
    print('\n[6] 取消：不会写出半成品')
    from PySide6.QtWidgets import QMessageBox
    from gtiharmonica.config import Config
    from gtiharmonica.gui.main import MainWindow

    library = os.path.join(ROOT, 'build', '_a2s_lib2')
    os.makedirs(library, exist_ok=True)
    for f in os.listdir(library):
        os.remove(os.path.join(library, f))

    win = MainWindow(Config(), library)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    win.transcribe_audio_file(AUDIO)          # 4 分钟的音频，来得及取消
    time.sleep(0.6)
    APP.processEvents()
    worker = getattr(win, '_transcribe_worker', None)
    if worker is not None:
        worker.cancel()
    deadline = time.time() + 120
    while time.time() < deadline:
        APP.processEvents()
        if worker is not None and worker.isFinished():
            break
        time.sleep(0.05)
    APP.processEvents()
    leftover = [f for f in os.listdir(library) if f.endswith('.json')]
    check('取消后曲库里没有残留文件', not leftover, ','.join(leftover))
    win.close()


if __name__ == '__main__':
    print('=' * 64)
    print('音频转曲谱自检')
    print('=' * 64)
    case_model()
    case_resample()
    case_slicer_fallback()
    case_module_end_to_end()
    case_gui_flow()
    case_cancel()
    print('\n' + '=' * 64)
    print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
    print('=' * 64)
    sys.exit(1 if FAIL else 0)
