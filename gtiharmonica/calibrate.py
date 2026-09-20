"""校准工具：把「本机 + 游戏」的真实响应测出来，再反推最优参数。

这是原程序完全没有的部分，也是定向优化的基础：
游戏接受多短的按键？SendInput 一次要多久？哪些键真的被接收？
把这些量测出来，才能有的放矢地调 gate / min_note / spin_window。

全部为**引导式**测量：程序发键，你听游戏里的声音并回答。
"""
from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from .arrange import PlayStep
from .backend import SendInputBackend, load_user32, foreground_window
from .instrument import Fingering, Instrument


@dataclass
class Calibration:
    """一次校准的结论。"""

    min_note: float = 0.02          # 游戏能识别的最短音长
    min_gap: float = 0.012          # 相邻音之间需要的最小空隙
    sendinput_us: float = 0.0       # SendInput 平均耗时（微秒）
    sendinput_p95_us: float = 0.0
    accepted_keys: List[str] = field(default_factory=list)
    rejected_keys: List[str] = field(default_factory=list)
    notes: str = ''

    def recommended_options(self) -> dict:
        """把校准结果翻译成编排参数。"""
        # 音长留 50% 余量，空隙留 100% 余量，避免踩在临界点上
        rec = {
            'min_note': round(max(0.015, self.min_note * 1.5), 4),
            'min_gap': round(max(0.008, self.min_gap * 2.0), 4),
        }
        if self.sendinput_p95_us > 500:
            rec['_hint'] = ('SendInput 延迟偏高（p95 %.0f us），建议提高 spin_window '
                            '或关闭杀软实时扫描' % self.sendinput_p95_us)
        return rec

    def save(self, path: str) -> None:
        with open(path, 'w', encoding='utf8') as fh:
            json.dump(asdict(self), fh, ensure_ascii=False, indent=1)

    @classmethod
    def load(cls, path: str) -> 'Calibration':
        with open(path, encoding='utf8') as fh:
            return cls(**json.load(fh))


# ---------------------------------------------------------------------------
# 1. SendInput 性能基准
# ---------------------------------------------------------------------------

def bench_sendinput(backend: SendInputBackend, rounds: int = 300) -> dict:
    """空跑 rounds 次按下/抬起，统计单次 SendInput 的耗时分布。"""
    key = Fingering('z')
    samples: List[float] = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        backend._emit('z', True)
        backend._emit('z', False)
        samples.append(time.perf_counter() - t0)

    samples.sort()
    n = len(samples)
    return {
        'rounds': n,
        'mean_us': statistics.mean(samples) * 1e6,
        'p50_us': samples[n // 2] * 1e6,
        'p95_us': samples[min(n - 1, int(n * 0.95))] * 1e6,
        'max_us': samples[-1] * 1e6,
    }


# ---------------------------------------------------------------------------
# 2. 最短可识别音长
# ---------------------------------------------------------------------------

def _tap(backend: SendInputBackend, fingering: Fingering, duration: float) -> None:
    step = PlayStep(pitch=60, source_pitch=60, fingering=fingering,
                    start=0.0, duration=duration)
    backend.play(step)
    time.sleep(duration)
    backend.silence()


def probe_min_note(backend: SendInputBackend, key: str = 'z',
                   high: float = 0.12, low: float = 0.005,
                   ask=input) -> Optional[float]:
    """二分搜索游戏能稳定识别的最短音长。

    每轮发同一个音 3 次（取「3 次都听到」才算通过），你回答 y / n。
    返回测得的最短音长（秒）；中途输入 q 放弃则返回 None。
    """
    fingering = Fingering(key)
    print('\n--- 测量最短可识别音长 ---')
    print('程序会连发 3 个相同的音；只要 3 个都听见就回答 y，否则 n。')
    print('（先在游戏里取出口琴并进入演奏界面）\n')

    best: Optional[float] = None
    while high - low > 0.003:
        mid = (low + high) / 2
        print('  试探音长 %.1f ms ...' % (mid * 1000), end='', flush=True)
        for _ in range(3):
            _tap(backend, fingering, mid)
            time.sleep(0.35)
        answer = ask('  3 个音都听到了吗? [y/n/q] ').strip().lower()
        if answer.startswith('q'):
            return None
        if answer.startswith('y'):
            best = mid
            high = mid
        else:
            low = mid

    if best is None:
        print('  × 即便 %.0f ms 也听不到，请检查窗口焦点与键位设置'
              % (high * 1000))
    else:
        print('  √ 最短可识别音长约 %.1f ms' % (best * 1000))
    return best


# ---------------------------------------------------------------------------
# 3. 按键通过率
# ---------------------------------------------------------------------------

def check_keymap(backend: SendInputBackend, instrument: Instrument,
                 dwell: float = 0.09, ask=input) -> tuple:
    """逐个测试音阶键与修饰键是否被游戏接收。

    返回 (通过的键, 失败的键)。
    """
    table = instrument.fingerings()
    accepted, rejected = [], []

    print('\n--- 按键通过率检查 ---')
    print('程序会逐个发出「音阶键」与「修饰键组合」，请对照游戏里的实际音高。\n')

    if instrument.base in table:
        candidates = [(k, ()) for k in instrument.keys]
        candidates += [(instrument.keys[0], (instrument.lower,)),
                       (instrument.keys[0], (instrument.semitone,)),
                       (instrument.keys[0], (instrument.upper,))]
    else:
        candidates = [(k, ()) for k in instrument.keys]

    for key, mods in candidates:
        label = '+'.join(mods + (key,))
        print('  发送 %-28s ...' % label, end='', flush=True)
        try:
            _tap(backend, Fingering(key, mods), dwell)
        except OSError as exc:
            print('发送失败：%s' % exc)
            rejected.append(label)
            continue
        answer = ask('听到了吗? [y/n/q] ').strip().lower()
        if answer.startswith('q'):
            break
        (accepted if answer.startswith('y') else rejected).append(label)

    print('\n  通过 %d 项，失败 %d 项' % (len(accepted), len(rejected)))
    if rejected:
        print('  失败项：%s' % ', '.join(rejected))
        print('  → 若整组失败，多半是目标窗口未获得焦点，或键位设置与 instrument 不一致')
    return accepted, rejected


# ---------------------------------------------------------------------------
# 4. 一键流程
# ---------------------------------------------------------------------------

def full_calibration(hwnd: Optional[int] = None, instrument: Optional[Instrument] = None,
                     interactive: bool = True, ask=input) -> Calibration:
    """依次跑完基准测试与引导式测量。"""
    api = load_user32()
    if hwnd is None:
        hwnd, title, _ = foreground_window(api)
        if not title:
            raise RuntimeError('当前没有有效的前台窗口，请先切到游戏')
        print('目标窗口：%r' % title)

    backend = SendInputBackend(hwnd, keep_focus=False)
    cal = Calibration()

    print('\n=== 1/3  SendInput 性能 ===')
    stats = bench_sendinput(backend)
    cal.sendinput_us = stats['mean_us']
    cal.sendinput_p95_us = stats['p95_us']
    print('  平均 %.0f us，p95 %.0f us，最大 %.0f us'
          % (stats['mean_us'], stats['p95_us'], stats['max_us']))
    if stats['mean_us'] > 400:
        print('  ! 单次注入偏慢，可能是杀软拦截或进程优先级过低')

    if not interactive:
        return cal

    print('\n=== 2/3  最短可识别音长 ===')
    got = probe_min_note(backend, key=(instrument or Instrument()).keys[0], ask=ask)
    if got:
        cal.min_note = got

    print('\n=== 3/3  按键通过率 ===')
    accepted, rejected = check_keymap(backend, instrument or Instrument(), ask=ask)
    cal.accepted_keys, cal.rejected_keys = accepted, rejected

    cal.notes = 'mean %.0f us / min_note %.1f ms' % (cal.sendinput_us,
                                                     cal.min_note * 1000)
    return cal
