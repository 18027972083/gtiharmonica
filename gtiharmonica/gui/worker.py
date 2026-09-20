"""后台工作线程：演奏、试听渲染、策略分析。

GUI 线程绝不能阻塞，所以三类耗时工作都放在 QThread 里：
  * PlayWorker     把按键送进游戏（阻塞式实时的，必须独立线程）
  * SynthWorker    渲染试听音频（1-2 秒）
  * AnalyzeWorker  跑 4 种指法策略并对比（大曲子可能上百毫秒）
"""
from __future__ import annotations

import traceback
from typing import List, Optional

from PySide6.QtCore import QThread, Signal

from ..arrange import Arrangement, Options, arrange
from ..backend import SendInputBackend
from ..fingering import STRATEGIES, total_cost
from ..player import Player, SchedulerConfig


class PlayWorker(QThread):
    """向游戏发送按键。"""

    tick = Signal(int, float, object)        # index, position, PlayStep
    stateChanged = Signal(str)               # playing / paused / stopped ...
    done = Signal(str, dict, dict)           # state, timing_report, backend_stats
    failed = Signal(str)

    def __init__(self, plan: Arrangement, hwnd: int,
                 scheduler: Optional[SchedulerConfig] = None,
                 keep_focus: bool = True, parent=None, loops: int = 1):
        super().__init__(parent)
        self.plan = plan
        self.hwnd = hwnd
        self.scheduler = scheduler or SchedulerConfig()
        self.keep_focus = keep_focus
        self.loops = loops
        self.player: Optional[Player] = None
        self._stop_requested = False

    def run(self) -> None:
        backend = None
        try:
            backend = SendInputBackend(self.hwnd, keep_focus=self.keep_focus)
            self.player = Player(self.plan, backend, self.scheduler,
                                 hotkeys=True)

            def on_progress(progress):
                self.stateChanged.emit(progress.state.value)
                if progress.step is not None:
                    self.tick.emit(progress.index, progress.position,
                                   progress.step)

            state = self.player.run(on_progress=on_progress,
                                    loops=self.loops)
            self.stateChanged.emit(state.value)
            self.done.emit(state.value, self.player.timing_report(),
                           backend.stats())
        except Exception as exc:
            self.failed.emit('%s\n%s' % (exc, traceback.format_exc(limit=3)))
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception:
                    pass

    # -- 外部控制（从 GUI 线程调用）--

    def pause(self) -> None:
        if self.player is not None:
            self.player.pause()
            self.stateChanged.emit('paused')

    def resume(self) -> None:
        if self.player is not None:
            self.player.resume()
            self.stateChanged.emit('playing')

    def stop(self) -> None:
        self._stop_requested = True
        if self.player is not None:
            self.player.stop()
            self.stateChanged.emit('stopped')

    @property
    def is_paused(self) -> bool:
        return self.player is not None and self.player.state.value == 'paused'


class SynthWorker(QThread):
    """渲染试听音频。"""

    ready = Signal(bytes, float)             # wav 字节, 时长
    failed = Signal(str)

    def __init__(self, plan: Arrangement, timbre=None,
                 sample_rate: int = 22050, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.timbre = timbre
        self.sample_rate = sample_rate

    def run(self) -> None:
        try:
            from .. import synth
            wav = synth.render_arrangement(self.plan, self.sample_rate, self.timbre)
            self.ready.emit(wav, self.plan.duration)
        except Exception as exc:
            self.failed.emit('%s\n%s' % (exc, traceback.format_exc(limit=3)))


class AnalyzeWorker(QThread):
    """对当前曲目跑遍所有指法策略并返回指标。"""

    rowReady = Signal(str, int, object, float)   # 策略名, 音轨, Metrics, 成本
    done = Signal(list, list)                    # 所有行, 错误信息

    def __init__(self, score, instrument, options: Options, cost,
                 tracks: Optional[List[Optional[int]]] = None, parent=None):
        super().__init__(parent)
        self.score = score
        self.instrument = instrument
        self.options = options
        self.cost = cost
        self.tracks = tracks

    def run(self) -> None:
        rows = []
        errors = []
        from dataclasses import asdict

        track_ids = self.tracks
        if track_ids is None:
            ids = self.score.tracks()
            track_ids = [None] + ids if len(ids) > 1 else list(ids)

        base = asdict(self.options)
        base.pop('cost', None)

        for track in track_ids:
            for name in STRATEGIES:
                kwargs = dict(base)
                kwargs['strategy'] = name
                kwargs['track'] = track
                try:
                    trial = Options(**kwargs)
                    plan = arrange(self.score, self.instrument, trial)
                except Exception as exc:
                    errors.append('%s / 音轨 %s：%s' % (name, track, exc))
                    continue
                value = total_cost([s.fingering for s in plan.steps], self.cost)
                rows.append((name, track, plan.metrics, value, plan.duration))
                self.rowReady.emit(name, -1 if track is None else track,
                                   plan.metrics, value)
        self.done.emit(rows, errors)


class LoadWorker(QThread):
    """后台解析曲谱并做首次编排（大 MIDI 可能耗时）。"""

    ready = Signal(object, object)           # Score, Arrangement
    failed = Signal(str)

    def __init__(self, path: str, instrument, options: Options, parent=None):
        super().__init__(parent)
        self.path = path
        self.instrument = instrument
        self.options = options

    def run(self) -> None:
        try:
            from ..score import load_score
            score = load_score(self.path)
            plan = arrange(score, self.instrument, self.options)
            self.ready.emit(score, plan)
        except Exception as exc:
            self.failed.emit('%s' % exc)
