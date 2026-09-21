"""播放调度与状态机。

要点：
  * 用 time.perf_counter() 建立**绝对时间轴**，不累加 sleep，因此不会漂移
  * 暂停时把暂停时长累加进偏移量，恢复后时间轴整体平移
  * 紧凑调度（tight）：如果音符结束到下一个音开始之间没有空隙，
    就省略一次「等到结束再松开」的往返，由下一个音按下时的 silence 承担，
    减少 sleep 唤醒次数 → 节奏更稳
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional

from .arrange import Arrangement, PlayStep
from .backend import (Backend, FocusLost, FocusOnSelf, foreground_is_self,
                      load_user32, raise_timer_resolution,
                      restore_timer_resolution)

# 虚拟键码
VK_F5 = 0x74
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
VK_ESCAPE = 0x1B


class State(Enum):
    IDLE = 'idle'
    READY = 'ready'
    PLAYING = 'playing'
    PAUSED = 'paused'
    FINISHED = 'finished'
    STOPPED = 'stopped'
    ERROR = 'error'


@dataclass
class SchedulerConfig:
    """调度精度配置 —— 在「时序准确度」与「CPU 占用」之间取舍。"""

    spin_window: float = 0.002      # 进入自旋等待的最后窗口（秒）
    poll_interval: float = 0.02     # 暂停/热键轮询间隔
    tight: bool = True              # 启用紧凑调度
    epsilon: float = 0.0005         # 判定「无空隙」的容差
    #: 输入兼容档位（移植自 midikey-player）：目标游戏按帧采样按键状态，
    #: 修饰键与音键同帧到达会读不全组合键。档位决定修饰键提前量等物理预算。
    timing: str = 'standard'


#: 输入兼容三档的物理预算（秒）。标准 = 60fps；稳健 = 30fps/卡顿机器；
#: 极限 = 高帧率。gap_guard 为 True 时同键重触发间隔不足会顺延后音。
INPUT_TIMINGS = {
    'safe': dict(frame=0.0333, mod_lead=0.070, retrig=0.080,
                 min_hold=0.080, gap_guard=True),
    'standard': dict(frame=0.0167, mod_lead=0.040, retrig=0.045,
                     min_hold=0.045, gap_guard=False),
    'aggressive': dict(frame=0.008, mod_lead=0.020, retrig=0.022,
                       min_hold=0.022, gap_guard=False),
}


def sleep_until(target: float, spin_window: float = 0.002) -> None:
    """睡到目标时刻。先用 sleep 逼近，最后 spin_window 内自旋。"""
    while True:
        delta = target - time.perf_counter()
        if delta <= 0:
            return
        if delta > spin_window:
            time.sleep(delta - spin_window * 0.5)


#: 等待期间的热键轮询间隔。一次按键通常持续 30–120 ms，8 ms 的采样粒度
#: 基本不会漏。只在音符边界检查（旧做法）的话，长音和长间隙里按 F8/F9
#: 会一直没反应 —— 用户报的「快捷键不生效」就出在这里。
#:
#: 注意 Windows 默认的计时器精度是 15.6 ms，time.sleep(0.008) 会睡成
#: 15.6 ms，12 ms 的轻点会被整段跳过。演奏期间用 timeBeginPeriod(1)
#: 把精度提到 1 ms（见 backend.raise_timer_resolution）。
HOTKEY_SLICE = 0.008


class PlayAborted(Exception):
    """内部信号：等待期间状态变成了停止/错误，立即回主循环收尾。

    不能只是「提前返回」——等待结束后紧接着就是发按键，状态已经停了
    还继续发的话，停止后就漏出去一个音。
    """


class HotkeyWatcher:
    """用 GetAsyncKeyState 轮询全局热键。

    轮询（而不是 RegisterHotKey）的原因：F8/F9 在游戏里可能另有用途，
    轮询不独占按键，也不会给游戏插一个键盘钩子。
    """

    def __init__(self, pause_key: int = VK_F8, stop_key: int = VK_F9,
                 seek_back_key: int = VK_F5, seek_fwd_key: int = VK_F7):
        self.api = load_user32()
        self.pause_key = pause_key
        self.stop_key = stop_key
        self.seek_back_key = seek_back_key
        self.seek_fwd_key = seek_fwd_key

    def down(self, vk: int) -> bool:
        return bool(self.api.GetAsyncKeyState(vk) & 0x8000)

    def self_foreground(self) -> bool:
        """本程序自己是不是当前的前台窗口。"""
        return foreground_is_self(self.api)

    def wait_release(self, vk: int, timeout: float = 0.5) -> None:
        deadline = time.perf_counter() + timeout
        while self.down(vk) and time.perf_counter() < deadline:
            time.sleep(0.01)


@dataclass
class Progress:
    index: int = 0
    total: int = 0
    position: float = 0.0
    duration: float = 0.0
    state: State = State.IDLE
    step: Optional[PlayStep] = None

    @property
    def percent(self) -> float:
        return 100.0 * self.position / self.duration if self.duration else 0.0


class Player:
    """把 Arrangement 播出来。"""

    def __init__(self, arrangement: Arrangement, backend: Backend,
                 config: Optional[SchedulerConfig] = None,
                 hotkeys=True, keep_focus: bool = True):
        self.arrangement = arrangement
        self.backend = backend
        self.config = config or SchedulerConfig()
        # hotkeys：True 自己建一个，False 不要，也可以直接传一个 watcher
        # 实例（自检里注入替身用）
        if hotkeys is True:
            self.hotkeys = HotkeyWatcher()
        elif hotkeys in (False, None):
            self.hotkeys = None
        else:
            self.hotkeys = hotkeys
        self.keep_focus = keep_focus
        self.state = State.READY
        self.progress = Progress(total=len(arrangement.steps),
                                 duration=arrangement.duration)
        self.error: Optional[str] = None
        self.pause_reason = ''
        self._pause_total = 0.0
        self._started = 0.0
        self._latencies: List[float] = []
        self._seek_request = 0.0
        self._seek_index = 0
        self.loops = 1
        self._loop_index = 0

    # -- 控制 --

    def pause(self, reason: str = '') -> None:
        if self.state is State.PLAYING:
            self.state = State.PAUSED
            self.pause_reason = reason       # 'hotkey' / 'focus' / ''
            self.backend.silence()
            self._prev_mods = ()      # 修饰键已全松，恢复后重新按下
            self._pause_at = time.perf_counter()
            self.progress.state = self.state

    def resume(self) -> None:
        if self.state is State.PAUSED:
            self._pause_total += time.perf_counter() - self._pause_at
            self.state = State.PLAYING
            self.pause_reason = ''
            self.progress.state = self.state

    def stop(self) -> None:
        """停止。除了已经停/出错的状态，任何状态都收敛到 STOPPED
        （循环间隔的 READY、播完的 FINISHED 都一样 —— 用户按 F9 就是
        「别继续了」）。"""
        if self.state not in (State.STOPPED, State.ERROR):
            self.state = State.STOPPED
            self.backend.silence()
            self.progress.state = self.state

    # -- 主循环 --

    def run(self, on_progress: Optional[Callable[[Progress], None]] = None,
            countdown: float = 0.0, loops: int = 1) -> State:
        """播放全曲。loops>1 时播完自动重来，<=0 表示无限循环。"""
        # 整场演奏期间把计时器精度提到 1 ms：热键轮询的 8 ms 采样与音符
        # 调度都靠它（默认 15.6 ms 会把轻点 F8/F9 整段跳过）
        raise_timer_resolution()
        try:
            return self._run_loops(on_progress, countdown, loops)
        finally:
            restore_timer_resolution()

    def _run_loops(self, on_progress, countdown: float,
                   loops: int) -> State:
        cfg = self.config
        timing = INPUT_TIMINGS.get(cfg.timing, INPUT_TIMINGS['standard'])
        if countdown > 0 and not self._countdown(countdown):
            # 倒计时里按了 F9 / Esc：当作用户取消
            self.state = State.STOPPED
            self.progress.state = self.state
            try:
                self.backend.silence()
            except Exception:
                pass
            if on_progress:
                on_progress(self.progress)
            return self.state

        self.loops = loops
        self._loop_index = 0
        attempts = 0
        while True:
            state = self._run_once(on_progress, timing)
            if state is not State.FINISHED:
                return state
            attempts += 1
            if 0 < loops <= attempts:
                return state
            self._loop_index = attempts
            if on_progress:
                on_progress(self.progress)
            # 循环间隔：短暂停顿，期间按 F9 就不再重播
            self.state = State.READY
            self.progress.state = self.state
            try:
                self._wait_until(time.perf_counter() + 0.4,
                                 None, on_progress)
            except PlayAborted:
                return self.state

    def _countdown(self, seconds: float) -> bool:
        """等待倒计时；F9 / Esc 可取消。返回 False 表示被取消。"""
        deadline = time.perf_counter() + seconds
        while True:
            remain = deadline - time.perf_counter()
            if remain <= 0:
                return True
            if self.hotkeys and not self.hotkeys.self_foreground() \
                    and (self.hotkeys.down(VK_F9)
                         or self.hotkeys.down(VK_ESCAPE)):
                self.hotkeys.wait_release(VK_F9)
                self.hotkeys.wait_release(VK_ESCAPE)
                return False
            time.sleep(min(remain, self.HOTKEY_SLICE))

    def _run_once(self, on_progress, timing) -> State:
        """单遍播放。返回结束状态。"""
        cfg = self.config
        self.backend.begin()
        self._started = time.perf_counter()
        self._pause_total = 0.0
        self._seek_request = 0.0
        self._prev_mods = ()
        self.state = State.PLAYING
        self.progress.state = self.state
        self.progress.index = 0

        steps = self.arrangement.steps
        offset = 0.0            # 重触发保护累计顺延（safe 档启用）
        index = 0
        last_due = -1.0
        last_key = None

        def due_of(step):
            """本音的目标时刻。暂停过的时长记在 _pause_total 里，恢复后自动顺延。"""
            return self._started + self._pause_total + step.start + offset

        try:
            while index < len(steps):
                step = steps[index]
                if self._consume_seek(offset):
                    # 跳转后重建时间轴基准，从新位置继续
                    offset = 0.0
                    index = self._seek_index
                    last_due = -1.0
                    last_key = None
                    continue
                if not self._pump(on_progress):
                    return self.state

                fing = step.fingering
                try:
                    due = due_of(step)

                    # 同键重触发保护：两次按下间隔不足时顺延本音（safe 档）
                    if timing['gap_guard'] and fing.key == last_key \
                            and due - last_due < timing['retrig']:
                        extra = timing['retrig'] - (due - last_due)
                        offset += extra
                        due += extra

                    # 修饰键提前量：提前 mod_lead 把修饰状态发出去，让目标
                    # 程序先完整采样一帧。空间不足时用可用空间的 90%，
                    # 完全没有空间才退化为同帧（旧行为）。
                    if tuple(fing.modifiers) != self._prev_mods:
                        mod_due = due - timing['mod_lead']
                        now = time.perf_counter()
                        if mod_due > now:
                            self._wait_until(
                                mod_due,
                                lambda: due_of(step) - timing['mod_lead'],
                                on_progress)
                        elif due - 0.002 > now:
                            self._wait_until(now + (due - now) * 0.9,
                                             None, on_progress)
                        due = due_of(step)      # 期间暂停过的话基准已顺延
                        self.backend.press_modifiers(step)
                        self._prev_mods = tuple(fing.modifiers)

                    due = self._wait_until(due, lambda: due_of(step),
                                           on_progress)
                    t0 = time.perf_counter()
                    self.backend.play_note(step)
                    self._latencies.append(t0 - due)

                    last_due = due
                    last_key = fing.key
                    self.progress.index = index + 1
                    self.progress.position = step.start
                    self.progress.step = step
                    if on_progress:
                        on_progress(self.progress)

                    next_start = steps[index + 1].start \
                        if index + 1 < len(steps) else None
                    hold = max(step.duration, timing['min_hold'])
                    if not cfg.tight or next_start is None or \
                            step.end < next_start - cfg.epsilon:
                        # 尾部路径：按住不足一帧的音补足最短按住
                        self._wait_until(due + hold,
                                         lambda: due_of(step) + hold,
                                         on_progress)
                        self.backend.release_note_keys()
                    elif next_start - step.start < timing['min_hold'] \
                            and timing['gap_guard']:
                        # 紧凑路径上音符间隔不足一帧（safe 档）：顺延后音，
                        # 保证本音至少被完整采样一帧
                        offset += timing['min_hold'] - (next_start - step.start)
                    index += 1
                except (FocusOnSelf, FocusLost) as exc:
                    # 正要发按键的一瞬间前台跑了（切回本程序 / 切到别的
                    # 程序）：暂停，本音重来，别把整场演奏丢掉
                    self.pause(reason='focus' if isinstance(exc, FocusOnSelf)
                               else 'lost')
                    continue
            self.state = State.FINISHED
        except PlayAborted:
            pass                    # 等待期间被停止：状态已就位，直接收尾
        except OSError as exc:
            self.error = str(exc)
            self.state = State.ERROR
        except KeyboardInterrupt:
            self.state = State.STOPPED
        finally:
            try:
                self.backend.silence()
            except Exception:
                pass
            self._prev_mods = ()
            self.progress.state = self.state
            if on_progress:
                on_progress(self.progress)
        return self.state

    def _consume_seek(self, offset: float) -> bool:
        """检查并应用 F5/F7 跳转请求。返回 True 表示位置已改变。"""
        delta = self._seek_request
        if not delta:
            return False
        self._seek_request = 0.0
        duration = self.arrangement.duration
        steps = self.arrangement.steps
        target = self.progress.position + delta
        if target >= duration - 0.05 or target >= steps[-1].end:
            index = len(steps)      # 跳过末尾 → 直接收尾
            target = duration
        else:
            target = max(target, 0.0)
            index = len(steps)
            for i, s in enumerate(steps):
                if s.start >= target - 0.01:
                    index = i
                    break
        self._seek_index = index
        try:
            self.backend.silence()
        except Exception:
            pass
        self._prev_mods = ()
        # 重建时间轴：新的 _started 让「当前位置」恰好等于 target
        self._started = time.perf_counter() - target - offset
        self._pause_total = 0.0
        self.progress.position = target
        return True

    def _pump(self, on_progress) -> bool:
        """处理暂停/停止热键与暂停等待。返回 False 表示应当退出。

        暂停/停止可能就发生在这里（热键、切回本程序），而此时主循环正
        堵在这个函数里，所以状态一变就要自己通知界面 —— 否则界面一直
        显示"演奏中"，用户以为暂停没生效。
        """
        cfg = self.config
        last_state = self.state
        while True:
            if self.state is not last_state:
                last_state = self.state
                if on_progress is not None:
                    try:
                        on_progress(self.progress)
                    except Exception:
                        pass
            # 本程序自己是前台时不做全局轮询：那种情况下 F8/F9 由界面的
            # QShortcut 处理。两边都响应的话，按一次 F8 会「暂停」完又被
            # 轮询「继续」，净效果就是按了没反应。
            if self.hotkeys and not self.hotkeys.self_foreground():
                if self.hotkeys.down(VK_F5):
                    self.hotkeys.wait_release(VK_F5)
                    self._seek_request = -5.0
                if self.hotkeys.down(VK_F7):
                    self.hotkeys.wait_release(VK_F7)
                    self._seek_request = 5.0
                if self.hotkeys.down(VK_F9):
                    self.hotkeys.wait_release(VK_F9)
                    self.stop()
                    return False
                if self.hotkeys.down(VK_ESCAPE):
                    self.hotkeys.wait_release(VK_ESCAPE)
                    self.stop()
                    return False
                if self.hotkeys.down(VK_F8):
                    self.hotkeys.wait_release(VK_F8)
                    if self.state is State.PLAYING:
                        self.pause(reason='hotkey')
                    else:
                        self.resume()

            if self.state is State.PLAYING:
                if self._focus_on_self():
                    # 用户切回本程序（看进度、按 F8）：暂停而不是中断
                    self.pause(reason='focus')
                    continue
                if not self._target_foreground():
                    # 前台既不是本程序也不是游戏（比如切去看攻略）：
                    # 按键发不出去，暂停等着，回游戏按 F8 继续
                    self.pause(reason='lost')
                    continue
                return True
            if self.state in (State.STOPPED, State.ERROR, State.FINISHED):
                return False
            time.sleep(cfg.poll_interval)

    def _focus_on_self(self) -> bool:
        """前台是不是切回了本程序。后端不支持（干跑/试听）时恒为 False。"""
        probe = getattr(self.backend, 'focus_on_self', None)
        if probe is None:
            return False
        try:
            return bool(probe())
        except Exception:
            return False

    def _target_foreground(self) -> bool:
        """前台是不是仍然是目标窗口。后端不支持时恒为 True。"""
        probe = getattr(self.backend, 'target_foreground', None)
        if probe is None:
            return True
        try:
            return bool(probe())
        except Exception:
            return True

    #: 热键轮询间隔（见模块级 HOTKEY_SLICE 的说明）
    HOTKEY_SLICE = HOTKEY_SLICE

    def _wait_until(self, target: float, recompute=None,
                    on_progress=None) -> float:
        """睡到 target 时刻；期间轮询热键，处理暂停 / 停止 / 跳转。

        返回最终的目标时刻：期间若发生过暂停，时间基准会顺延
        （_pause_total 变了），调用方要用返回值覆盖原来的 due。
        """
        while True:
            if target - time.perf_counter() <= 0:
                return target
            if self.hotkeys:
                if not self._pump(on_progress):
                    raise PlayAborted()     # 已停止：立刻收尾，别再发按键
                if recompute is not None:
                    fresh = recompute()
                    if fresh != target:
                        target = fresh
                        continue
            time.sleep(self.HOTKEY_SLICE)

    # -- 诊断 --

    def timing_report(self) -> dict:
        if not self._latencies:
            return {'samples': 0}
        lat = sorted(self._latencies)
        n = len(lat)
        return {
            'samples': n,
            'mean_ms': sum(lat) / n * 1000,
            'p50_ms': lat[n // 2] * 1000,
            'p95_ms': lat[min(n - 1, int(n * 0.95))] * 1000,
            'max_ms': lat[-1] * 1000,
        }
