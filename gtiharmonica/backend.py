"""输入注入后端。

三种后端共用一套接口，可在不改动编排逻辑的前提下切换：

    SendInputBackend  向游戏窗口发送扫描码（实际演奏用）
    PreviewBackend    本机 MIDI 试听，不动游戏，用于选曲/调参
    DryRunBackend     只记录不发送，用于分析按键序列

关于扫描码：游戏（DirectInput / Raw Input）读取的是**硬件扫描码**，
只设置虚拟键码的 SendInput / keybd_event 通常会被忽略，
所以这里必须走 MapVirtualKeyW(VK→SCAN) + KEYEVENTF_SCANCODE。
"""
from __future__ import annotations

import ctypes
import os
import time
from abc import ABC, abstractmethod
from ctypes import wintypes
from typing import List, Optional, Sequence

from .arrange import PlayStep
from .instrument import MOUSE_FLAGS, OEM_VK

# ---------------------------------------------------------------------------
# Win32 结构体
# ---------------------------------------------------------------------------

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG),
                ('mouseData', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD),
                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD),
                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', wintypes.DWORD), ('wParamL', wintypes.WORD),
                ('wParamH', wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('union',)
    _fields_ = [('type', wintypes.DWORD), ('union', _INPUTUNION)]


def load_user32():
    """加载并声明 user32 的函数签名。"""
    if os.name != 'nt':
        raise OSError('键鼠注入目前只支持 Windows')
    api = ctypes.WinDLL('user32', use_last_error=True)
    api.GetForegroundWindow.restype = wintypes.HWND
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                             ctypes.POINTER(wintypes.DWORD)]
    api.GetWindowThreadProcessId.restype = wintypes.DWORD
    api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.GetWindowTextW.restype = ctypes.c_int
    api.GetAsyncKeyState.argtypes = [ctypes.c_int]
    api.GetAsyncKeyState.restype = ctypes.c_short
    api.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    api.SendInput.restype = wintypes.UINT
    api.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    api.MapVirtualKeyW.restype = wintypes.UINT
    return api


def window_title(api, hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    api.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def foreground_window(api):
    """返回 (hwnd, 标题, pid)。"""
    hwnd = api.GetForegroundWindow()
    pid = wintypes.DWORD()
    api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return hwnd, window_title(api, hwnd), pid.value


def foreground_is_self(api) -> bool:
    """前台窗口是不是本程序自己的窗口（主窗口 / 悬浮窗 / 对话框都算）。

    演奏中用户切回本程序看进度、按 F8，前台就变成了自己。这既不能当成
    「误发风险」直接中断演奏，也不能继续往游戏发按键 —— 正确反应是暂停。
    """
    _, _, pid = foreground_window(api)
    return pid == os.getpid()


class FocusOnSelf(Exception):
    """前台切回了本程序：应当暂停等待用户回到游戏，而不是中断演奏。"""


class FocusLost(Exception):
    """前台切到了别的程序：同样按「暂停」处理。

    早先这里抛 OSError，表现是「切出去看一眼再回来，演奏已经结束、
    悬浮窗也没了」。可按键本来就不可能发出去（前台不是游戏），
    暂停等用户回游戏按 F8 继续，才是安全又符合直觉的反应。
    """


def raise_timer_resolution(period_ms: int = 1) -> bool:
    """把系统计时器精度提到 1 ms（Windows 默认 15.6 ms）。

    不提高的话 time.sleep(0.008) 实际睡 15.6 ms：热键轮询的采样间隔就
    变成了 15.6 ms，用户轻点一下 F9（约 12 ms）会被整段跳过 —— 表现就是
    「按了没反应」。播放调度的 sleep 也一起吃这个红利。
    """
    try:
        winmm = ctypes.WinDLL('winmm', use_last_error=True)
        winmm.timeBeginPeriod.argtypes = [wintypes.UINT]
        winmm.timeBeginPeriod.restype = wintypes.UINT
        return winmm.timeBeginPeriod(period_ms) == 0
    except Exception:
        return False


def restore_timer_resolution(period_ms: int = 1) -> None:
    """还原计时器精度（与 raise_timer_resolution 成对调用）。"""
    try:
        winmm = ctypes.WinDLL('winmm', use_last_error=True)
        winmm.timeEndPeriod.argtypes = [wintypes.UINT]
        winmm.timeEndPeriod.restype = wintypes.UINT
        winmm.timeEndPeriod(period_ms)
    except Exception:
        pass


def list_windows(api) -> List[tuple]:
    """枚举所有可见顶层窗口，用于挑选演奏目标。"""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(
        ctypes.c_bool, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = window_title(api, hwnd)
        if not title:
            return True
        pid = wintypes.DWORD()
        api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((hwnd, title, pid.value))
        return True

    user32.EnumWindows(_cb, 0)
    return found


# ---------------------------------------------------------------------------
# 后端接口
# ---------------------------------------------------------------------------

class Backend(ABC):
    """演奏后端。"""

    name = 'backend'

    def begin(self) -> None:
        """开始演奏前的准备工作。"""

    @abstractmethod
    def play(self, step: PlayStep) -> None:
        """发出一个音（内部需保证先松开上一个音）。"""

    def press_modifiers(self, step: PlayStep) -> None:
        """提前按住本音的修饰键（输入兼容档位的「修饰键提前量」）。

        目标程序按帧采样键盘状态时，修饰键与音键落在同一帧会读不全
        组合键（漏音/音高错）。把修饰键提前 ≥2 帧按下，程序就能先
        采样到修饰状态。默认 no-op —— 修饰键留给 play() 一起发。
        """

    def play_note(self, step: PlayStep) -> None:
        """按下本音的音键（松开上一个音键由实现内部处理）。"""
        self.play(step)

    def release_note_keys(self) -> None:
        """只松开音键、保留修饰键（跨音保持修饰状态，避免反复重按）。"""

    @abstractmethod
    def silence(self) -> None:
        """释放所有按住的键。"""

    def close(self) -> None:
        try:
            self.silence()
        except Exception:
            pass


class DryRunBackend(Backend):
    """记录动作但不真正发送，用于分析和调试。"""

    name = 'dry-run'

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.log: List[tuple] = []

    def play(self, step: PlayStep) -> None:
        self.log.append(('down', step.fingering.describe(), step.start, step.pitch))
        if self.verbose:
            print('  %s' % step.describe())

    def silence(self) -> None:
        if self.log:
            self.log.append(('up', 'all', time.perf_counter(), -1))


class NullBackend(Backend):
    """什么都不做，只用于计时基准测试。"""

    name = 'null'

    def play(self, step: PlayStep) -> None:
        pass

    def silence(self) -> None:
        pass


# ---------------------------------------------------------------------------
# 游戏注入
# ---------------------------------------------------------------------------

class SendInputBackend(Backend):
    """向指定窗口发送扫描码。窗口失焦会立即抛错阻止继续演奏。"""

    name = 'sendinput'

    def __init__(self, hwnd: int, keep_focus: bool = True,
                 verbose: bool = False, count_errors: bool = True):
        self.api = load_user32()
        self.hwnd = hwnd
        self.keep_focus = keep_focus
        self.verbose = verbose
        self.held: List[str] = []
        self.send_calls = 0
        self.send_time = 0.0
        self.send_errors = 0
        self.count_errors = count_errors

    # -- 底层：一次按下或抬起 --

    def _emit(self, key: str, down: bool) -> None:
        event = INPUT()
        if key in MOUSE_FLAGS:
            event.type = INPUT_MOUSE
            event.mi.dwFlags = MOUSE_FLAGS[key][0 if down else 1]
        else:
            event.type = INPUT_KEYBOARD
            vk = OEM_VK.get(key, ord(key.upper()))
            event.ki.wScan = self.api.MapVirtualKeyW(vk, 0)
            event.ki.dwFlags = KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP)

        t0 = time.perf_counter()
        ok = self.api.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
        self.send_time += time.perf_counter() - t0
        self.send_calls += 1

        if ok != 1:
            self.send_errors += 1
            if self.count_errors and self.send_errors <= 3:
                raise OSError('SendInput 被拒绝（错误码 %d）。'
                              '通常是目标窗口以管理员权限运行，本进程也需要提权。'
                              % ctypes.get_last_error())
        if self.verbose:
            print('    %s %s' % ('DOWN' if down else 'UP  ', key))

    # -- 对外 --

    def focus_on_self(self) -> bool:
        """前台是否切回了本程序（关闭「保持焦点」时不判断）。"""
        return self.keep_focus and foreground_is_self(self.api)

    def target_foreground(self) -> bool:
        """前台是不是目标窗口（关闭「保持焦点」时恒为 True）。"""
        if not self.keep_focus:
            return True
        try:
            return self.api.GetForegroundWindow() == self.hwnd
        except Exception:
            return True

    def check_focus(self) -> None:
        if not self.keep_focus:
            return
        current = self.api.GetForegroundWindow()
        if current == self.hwnd:
            return
        if foreground_is_self(self.api):
            # 用户切回本程序（看进度、按 F8 暂停）：暂停，不算出错
            raise FocusOnSelf('前台切回了本程序')
        # 切到了别的程序：按键本来就发不出去，按暂停处理（切回游戏
        # 按 F8 即可继续）。早先这里抛 OSError 直接结束演奏，表现就是
        # 「切出去看一眼，回来悬浮窗都没了」。
        raise FocusLost('前台切到了别的程序（%s），已暂停'
                        % (window_title(self.api, current) or '未知'))

    def play(self, step: PlayStep) -> None:
        self.check_focus()
        self.silence()
        for key in (*step.fingering.modifiers, step.fingering.key):
            self.held.append(key)
            self._emit(key, True)

    def press_modifiers(self, step: PlayStep) -> None:
        """把修饰键状态对齐到本音需要的状态：先松多余的，再按缺的。"""
        self.check_focus()
        want = list(step.fingering.modifiers)
        held_mods = [k for k in self.held if k in MOUSE_FLAGS]
        for key in held_mods:
            if key not in want:
                self.held.remove(key)
                self._emit(key, False)
        for key in want:
            if key not in self.held:
                self.held.append(key)
                self._emit(key, True)

    def play_note(self, step: PlayStep) -> None:
        """先松开上一个音键（保留修饰键），再按下本音键。"""
        self.check_focus()
        for key in reversed(self.held[:]):
            if key in MOUSE_FLAGS:
                continue
            self._emit(key, False)
            self.held.remove(key)
        self.held.append(step.fingering.key)
        self._emit(step.fingering.key, True)

    def release_note_keys(self) -> None:
        """只松开音键；修饰键保持按住（跨音保持，减少重触发）。"""
        for key in reversed(self.held[:]):
            if key in MOUSE_FLAGS:
                continue
            try:
                self._emit(key, False)
            except OSError:
                continue
            self.held.remove(key)

    def silence(self) -> None:
        errors = []
        for key in reversed(self.held[:]):
            try:
                self._emit(key, False)
                self.held.remove(key)
            except OSError as exc:
                errors.append(exc)
        if errors:
            raise OSError('按键释放失败，请手动松开键鼠后重试') from errors[0]

    # -- 诊断 --

    def stats(self) -> dict:
        return {
            'send_calls': self.send_calls,
            'avg_us': (self.send_time / self.send_calls * 1e6) if self.send_calls else 0.0,
            'errors': self.send_errors,
        }


# ---------------------------------------------------------------------------
# 本机试听
# ---------------------------------------------------------------------------

class PreviewBackend(Backend):
    """用系统 MIDI 合成器试听，不触碰游戏。

    需要 python-rtmidi；不可用时自动降级为 DryRunBackend。
    """

    name = 'preview'

    def __init__(self, program: int = 22, channel: int = 0, verbose: bool = False):
        self.port = None
        self.channel = channel
        self.verbose = verbose
        self.fallback: Optional[DryRunBackend] = None
        try:
            import mido
            self.mido = mido
            self.port = mido.open_output()
            self._send(mido.Message('program_change', program=program,
                                    channel=channel))
        except Exception as exc:
            self.fallback = DryRunBackend(verbose=verbose)
            if verbose:
                print('  [试听不可用：%s] 降级为静默模式' % exc)

    def _send(self, msg) -> None:
        if self.port is not None:
            self.port.send(msg)

    def begin(self) -> None:
        if self.port is not None:
            self._send(self.mido.Message('control_change', control=7, value=100,
                                         channel=self.channel))

    def play(self, step: PlayStep) -> None:
        if self.fallback is not None:
            return self.fallback.play(step)
        self.silence()
        self._send(self.mido.Message('note_on', note=step.pitch, velocity=80,
                                     channel=self.channel))
        if self.verbose:
            print('  %s' % step.describe())

    def silence(self) -> None:
        if self.fallback is not None:
            return self.fallback.silence()
        if self.port is None:
            return
        for note in range(128):
            self._send(self.mido.Message('note_off', note=note, channel=self.channel))

    def close(self) -> None:
        self.silence()
        if self.port is not None:
            try:
                self.port.close()
            except Exception:
                pass
