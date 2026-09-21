# -*- coding: utf-8 -*-
"""热键（F8 暂停 / F9 停止）行为自检。

背景：用户报过「F8/F9 不生效」。查出来的原因不是一个而是四个：

  1. 只在音符边界轮询热键 —— 长音或长间隙里按 F8/F9 一直没反应
  2. GetAsyncKeyState 是瞬时采样，两次检查之间按一下又松开就整个漏掉
  3. 倒计时 3 秒里焦点在游戏上，界面的 QShortcut 收不到，也没有轮询
  4. 程序自己是前台时 QShortcut 与轮询都会响应一次，暂停完立刻又被
     「继续」，净效果就是按了没反应；而切回程序时 check_focus 还会先把
     演奏直接中断掉

这套用例把四种情形都钉住：用一个假 watcher 精确控制「按键状态」与
「谁在前台」，用一个假后端记录到底发了哪些音。不需要真键盘。

另外还有一项真机用例：用 SendInput 真的发一次 F8，验证轮询确实能
采到系统级的按键（注入的按键同样会进键盘状态）。

用法：python tools/_check_hotkeys.py
"""
from __future__ import annotations

import ctypes
import os
# 公告框是模态的：自检没人点按钮，必须显式关掉（否则 exec() 永久阻塞）
os.environ.setdefault('GTIHARMONICA_NO_ANNOUNCE', '1')
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gtiharmonica import player as P                                   # noqa: E402
from gtiharmonica.arrange import arrange                              # noqa: E402
from gtiharmonica.backend import Backend, FocusOnSelf                 # noqa: E402
from gtiharmonica.config import Config                                # noqa: E402
from gtiharmonica.instrument import Instrument                        # noqa: E402
from gtiharmonica.score import Note, Score                            # noqa: E402
from gtiharmonica.player import SchedulerConfig, VK_ESCAPE, VK_F8, VK_F9  # noqa: E402

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


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------

class FakeWatcher:
    """可控的热键状态源：想让它「按下」哪个键就往 pressed 里加。"""

    def __init__(self):
        self.pressed = set()
        self.foreground_self = False
        self.checks = 0

    def down(self, vk: int) -> bool:
        self.checks += 1
        return vk in self.pressed

    def wait_release(self, vk: int, timeout: float = 0.5) -> None:
        self.pressed.discard(vk)

    def self_foreground(self) -> bool:
        return self.foreground_self


class FakeBackend(Backend):
    """只记录不发送。"""

    def __init__(self):
        self.played = []            # [(perf_counter, step)]
        self.silences = 0
        self.focus_self = False
        self.release_calls = 0

    def begin(self) -> None:
        pass

    def play(self, step) -> None:
        pass

    def press_modifiers(self, step) -> None:
        pass

    def play_note(self, step) -> None:
        self.played.append((time.perf_counter(), step))

    def release_note_keys(self) -> None:
        self.release_calls += 1

    def silence(self) -> None:
        self.silences += 1

    def close(self) -> None:
        pass

    def focus_on_self(self) -> bool:
        return self.focus_self


def make_plan(gap: float = 4.0, note_len: float = 2.0):
    """两三个音、之间留大空档：正是旧代码里「按了没反应」的长间隙场景。"""
    notes = [Note(pitch=p, start=i * gap, duration=note_len)
             for i, p in enumerate((60, 64, 67))]
    score = Score(title='热键自检', notes=notes, bpm=120.0)
    instrument, options, cost, scheduler = Config().build()
    return arrange(score, instrument, options), scheduler


def run_player(plan, scheduler, watcher, backend=None, countdown=0.0,
               on_progress=None):
    """在后台线程跑一遍演奏，返回 (thread, player, backend, 状态容器)。"""
    backend = backend or FakeBackend()
    player = P.Player(plan, backend, scheduler, hotkeys=watcher)
    box = {}

    def go():
        try:
            box['state'] = player.run(on_progress=on_progress,
                                      countdown=countdown)
        except BaseException as exc:            # noqa: BLE001
            box['error'] = exc

    thread = threading.Thread(target=go, daemon=True)
    thread.start()
    return thread, player, backend, box


def wait_for(pred, timeout=2.0, step=0.01):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if pred():
            return True
        time.sleep(step)
    return False


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------

def case_stop_during_long_note() -> None:
    print('\n[1] 长音/长间隙里按 F9 能停（旧代码在这里全程不轮询）')
    plan, scheduler = make_plan(gap=6.0, note_len=4.0)
    watcher = FakeWatcher()
    thread, player, backend, box = run_player(plan, scheduler, watcher)

    assert wait_for(lambda: player.state is P.State.PLAYING, 2.0), '演奏没起来'
    time.sleep(0.4)                     # 此刻正卡在长音/长间隙的等待里
    fired = len(backend.played)
    watcher.pressed.add(VK_F9)          # 轻点一下 F9
    ok = wait_for(lambda: player.state is P.State.STOPPED, 1.5)
    check('长等待中途按 F9 立刻停止', ok, 'state=%s' % player.state.value)
    thread.join(timeout=2.0)
    check('停止后没有漏出额外的音', len(backend.played) == fired,
          '%d -> %d' % (fired, len(backend.played)))


def case_pause_and_resume() -> None:
    print('\n[2] F8 暂停 / 再按继续，暂停期间不发按键')
    plan, scheduler = make_plan(gap=4.0, note_len=1.5)
    watcher = FakeWatcher()
    thread, player, backend, box = run_player(plan, scheduler, watcher)

    assert wait_for(lambda: player.state is P.State.PLAYING, 2.0), '演奏没起来'
    time.sleep(0.3)
    watcher.pressed.add(VK_F8)
    check('按 F8 进入暂停', wait_for(lambda: player.state is P.State.PAUSED, 1.5),
          player.state.value)

    fired = len(backend.played)
    time.sleep(0.5)
    check('暂停期间一个音都不发', len(backend.played) == fired,
          '%d -> %d' % (fired, len(backend.played)))

    watcher.pressed.add(VK_F8)
    check('再按 F8 继续', wait_for(lambda: player.state is P.State.PLAYING, 1.5),
          player.state.value)
    watcher.pressed.add(VK_F9)
    thread.join(timeout=3.0)


def case_pause_shifts_timeline() -> None:
    print('\n[3] 暂停过的时间会顺延，恢复后节奏不乱')
    plan, scheduler = make_plan(gap=3.0, note_len=1.0)
    watcher = FakeWatcher()
    thread, player, backend, box = run_player(plan, scheduler, watcher)

    assert wait_for(lambda: len(backend.played) >= 1, 2.0), '第一个音没发出来'
    first_t = backend.played[0][0]

    watcher.pressed.add(VK_F8)          # 暂停
    assert wait_for(lambda: player.state is P.State.PAUSED, 1.5)
    paused_at = time.perf_counter()
    time.sleep(0.6)
    watcher.pressed.add(VK_F8)          # 继续
    assert wait_for(lambda: player.state is P.State.PLAYING, 1.5)

    assert wait_for(lambda: len(backend.played) >= 2, 4.0), '第二个音没发出来'
    second_t = backend.played[1][0]
    gap = second_t - first_t
    step_gap = plan.steps[1].start - plan.steps[0].start
    check('第二个音被推迟了（≈ 暂停时长）', gap > step_gap + 0.4,
          '间隔 %.2fs（原 %.2fs）' % (gap, step_gap))
    check('推迟量接近真实暂停时长', 0.5 < (gap - step_gap) < 1.4,
          '推迟 %.2fs' % (gap - step_gap))
    watcher.pressed.add(VK_F9)
    thread.join(timeout=3.0)


def case_foreground_handoff() -> None:
    print('\n[4] 程序自己在前台时，F8/F9 交给界面 QShortcut（不双触发）')
    plan, scheduler = make_plan(gap=4.0, note_len=1.5)
    watcher = FakeWatcher()
    watcher.foreground_self = True       # 模拟用户切回本程序
    thread, player, backend, box = run_player(plan, scheduler, watcher)

    assert wait_for(lambda: player.state is P.State.PLAYING, 2.0), '演奏没起来'
    time.sleep(0.3)
    watcher.pressed.add(VK_F9)
    time.sleep(0.4)
    check('前台是自己时轮询不抢热键（不停止）',
          player.state in (P.State.PLAYING, P.State.PAUSED),
          player.state.value)

    watcher.foreground_self = False      # 切回游戏
    check('切回游戏后轮询接管，F9 能停',
          wait_for(lambda: player.state is P.State.STOPPED, 1.5),
          player.state.value)
    thread.join(timeout=3.0)


def case_focus_back_pauses() -> None:
    print('\n[5] 切回本程序 → 自动暂停（不是中断）并通知界面')
    plan, scheduler = make_plan(gap=4.0, note_len=1.5)
    watcher = FakeWatcher()
    backend = FakeBackend()
    seen = []
    thread, player, b2, box = run_player(
        plan, scheduler, watcher, backend=backend,
        on_progress=lambda pr: seen.append(pr.state.value))

    assert wait_for(lambda: player.state is P.State.PLAYING, 2.0), '演奏没起来'
    backend.focus_self = True            # 用户切回本程序
    check('切回本程序后自动暂停',
          wait_for(lambda: player.state is P.State.PAUSED, 1.5),
          player.state.value)
    check('暂停原因标记为 focus', player.pause_reason == 'focus',
          player.pause_reason)
    check('暂停状态推给了界面', wait_for(lambda: 'paused' in seen, 1.5),
          ','.join(seen[-3:]))
    check('暂停不是中断（没有进 ERROR）', player.state is not P.State.ERROR,
          player.state.value)
    thread.join(timeout=2.0)


def case_focus_race_pauses() -> None:
    print('\n[6] 正要发按键时前台切回自己：暂停而不是报错中断')
    plan, scheduler = make_plan(gap=1.0, note_len=0.5)
    watcher = FakeWatcher()
    watcher.foreground_self = True
    backend = FakeBackend()

    # backend 的 play_note 抛 FocusOnSelf —— 模拟 check_focus 撞上切窗口
    def boom(step):
        raise FocusOnSelf('前台切回了本程序')
    backend.play_note = boom               # type: ignore[method-assign]

    thread, player, _, box = run_player(plan, scheduler, watcher,
                                        backend=backend)
    time.sleep(0.8)
    check('FocusOnSelf 走暂停而不是 ERROR',
          player.state in (P.State.PAUSED, P.State.PLAYING),
          player.state.value)
    check('没有抛出到线程外', 'error' not in box, str(box.get('error'))[:60])

    watcher.foreground_self = False
    watcher.pressed.add(VK_F9)
    thread.join(timeout=3.0)
    check('最终能停下', player.state is P.State.STOPPED, player.state.value)


def case_countdown_cancel() -> None:
    print('\n[7] 倒计时里按 F9 / Esc 能取消')
    plan, scheduler = make_plan()
    watcher = FakeWatcher()
    thread, player, backend, box = run_player(plan, scheduler, watcher,
                                              countdown=3.0)
    time.sleep(0.3)
    watcher.pressed.add(VK_ESCAPE)
    t0 = time.perf_counter()
    thread.join(timeout=4.0)
    elapsed = time.perf_counter() - t0
    check('倒计时被取消（没等满 3 秒）', elapsed < 1.5, '%.2fs' % elapsed)
    check('状态是 stopped', player.state is P.State.STOPPED, player.state.value)
    check('一个音都没发', len(backend.played) == 0, str(len(backend.played)))


def case_real_keyboard() -> None:
    print('\n[8] 真机：SendInput 注入的 F9 能被 8ms 轮询采到')
    from gtiharmonica.backend import (INPUT, INPUT_KEYBOARD, KEYEVENTF_KEYUP,
                                      KEYEVENTF_SCANCODE, load_user32)

    api = load_user32()
    VK = VK_F9
    scan = api.MapVirtualKeyW(VK, 0)

    def tap(seconds: float = 0.12) -> None:
        tap_key(VK, seconds)

    def sample(seconds: float = 0.12):
        """以 8ms（HOTKEY_SLICE）粒度采样一次按键，返回采到的次数。"""
        from gtiharmonica.backend import (raise_timer_resolution,
                                          restore_timer_resolution)
        raise_timer_resolution()        # 不提高精度的话 12ms 的短按会被跳过
        try:
            watcher = P.HotkeyWatcher()
            hits = []
            stop = threading.Event()

            def poll():
                while not stop.is_set():
                    if watcher.down(VK):
                        hits.append(1)
                        return
                    time.sleep(P.HOTKEY_SLICE)

            thread = threading.Thread(target=poll, daemon=True)
            thread.start()
            time.sleep(0.05)
            tap(seconds)
            time.sleep(0.05)
            stop.set()
            thread.join(timeout=0.5)
            return len(hits)
        finally:
            restore_timer_resolution()

    check('一次 120ms 的真实按键被采到', sample(0.12) >= 1, '')
    check('12ms 的短按也能采到', sample(0.012) >= 1, '')


def case_gui_wiring() -> None:
    """界面联动：真实注入的 F8/F9 能穿过整个程序（轮询 → 播放线程 → 界面）。

    用离屏窗口 + 真键盘事件。按键注入本身被换成空实现（no-op），只跑
    热键那一条链路 —— 否则测试会真的往当前前台窗口打字。
    """
    print('\n[9] 界面联动：真 F8/F9 → MainWindow 的暂停/停止')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

    from PySide6.QtWidgets import QApplication, QMessageBox

    import gtiharmonica.backend as B
    from gtiharmonica.arrange import arrange
    from gtiharmonica.gui.main import MainWindow
    from gtiharmonica.gui.theme import apply_theme
    from gtiharmonica.score import load_score

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    apply_theme(app, 'dark')

    # 只静音「发声」那几步：热键与焦点判断保持真实
    B.SendInputBackend.play_note = lambda self, step: None          # type: ignore
    B.SendInputBackend.press_modifiers = lambda self, step: None    # type: ignore
    B.SendInputBackend.release_note_keys = lambda self: None        # type: ignore
    B.SendInputBackend.silence = lambda self: None                  # type: ignore
    QMessageBox.information = staticmethod(lambda *a, **k: None)    # type: ignore

    win = MainWindow(Config(), os.path.join(root, 'songs'))
    win.keep_focus = False          # 测试环境没有游戏在前台
    win.countdown_seconds = 0
    win.minimize_on_play = False
    win._run_preflight = lambda: None       # type: ignore[method-assign]

    score = load_score(os.path.join(root, 'songs', '小星星.json'))
    instrument, options, cost, scheduler = Config().build()
    win._on_score_loaded(score, arrange(score, instrument, options))
    win.start_play()

    def spin(seconds=0.1):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.02)

    started = wait_for(
        lambda: win.play_worker is not None and win.play_worker.isRunning(), 3.0)
    check('演奏线程起来了', started)
    if not started:
        win.close()
        return

    tap_key(0x77)                   # F8
    paused = wait_for(lambda: spin(0.05) or
                      (win.play_worker is not None and win.play_worker.is_paused),
                      2.0)
    check('真实 F8 把演奏暂停了', paused)
    check('按钮提示同步成「已暂停」', '已暂停' in win.btn_play.text(),
          win.btn_play.text())

    tap_key(0x77)                   # 再按一次 F8 → 继续
    resumed = wait_for(lambda: spin(0.05) or
                       (win.play_worker is not None
                        and not win.play_worker.is_paused), 2.0)
    check('再按 F8 继续演奏', resumed)

    tap_key(0x78)                   # F9 → 停止
    stopped = wait_for(lambda: spin(0.05) or win.play_worker is None, 2.5)
    check('真实 F9 停掉了演奏', stopped)
    check('停止后界面回到可开始状态', win.btn_play.isEnabled(),
          str(win.btn_play.isEnabled()))

    win.stop_all()
    win.close()
    spin(0.2)


def tap_key(vk: int, seconds: float = 0.12) -> None:
    """向系统注入一次真实的按键（扫描码方式，与演奏同一条路）。"""
    from gtiharmonica.backend import (INPUT, INPUT_KEYBOARD, KEYEVENTF_KEYUP,
                                      KEYEVENTF_SCANCODE, load_user32)
    api = load_user32()
    scan = api.MapVirtualKeyW(vk, 0)
    down = INPUT()
    down.type = INPUT_KEYBOARD
    down.ki.wScan = scan
    down.ki.dwFlags = KEYEVENTF_SCANCODE
    up = INPUT()
    up.type = INPUT_KEYBOARD
    up.ki.wScan = scan
    up.ki.dwFlags = KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP
    api.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    time.sleep(seconds)
    api.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))


if __name__ == '__main__':
    print('=' * 62)
    print('热键行为自检（F8 暂停 / F9 停止）')
    print('=' * 62)
    case_stop_during_long_note()
    case_pause_and_resume()
    case_pause_shifts_timeline()
    case_foreground_handoff()
    case_focus_back_pauses()
    case_focus_race_pauses()
    case_countdown_cancel()
    case_real_keyboard()
    case_gui_wiring()
    print('\n' + '=' * 62)
    print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
    print('=' * 62)
    sys.exit(1 if FAIL else 0)
