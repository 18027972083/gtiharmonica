"""大肥鲸洲琴工具包 —— 《三角洲行动》口琴自动演奏引擎。

一套完全自主实现的演奏引擎，核心能力：

  * MIDI / JSON 曲谱解析（含 tempo 变化）
  * 音高 → 指法映射，支持 4 种可插拔策略（含动态规划全局最优）
  * 和弦单音化、八度折叠、移调、换气与连奏控制
  * 通过 SendInput 发送**扫描码**注入游戏；支持本机 MIDI 试听与干跑分析
  * perf_counter 绝对时钟调度，暂停/恢复不漂移
  * 校准工具：测量最小可识别音长、SendInput 延迟、按键通过率
"""

__version__ = '1.1.5'
__all__ = [
    'Instrument', 'Fingering', 'note_name', 'KEYS', 'STEPS',
    'Score', 'Note', 'load_score', 'load_midi',
    'Options', 'Arrangement', 'PlayStep', 'arrange',
    'CostModel', 'STRATEGIES', 'compare', 'measure',
    'SendInputBackend', 'PreviewBackend', 'DryRunBackend',
    'Player', 'SchedulerConfig',
]

from .instrument import KEYS, STEPS, Fingering, Instrument, note_name
from .score import Note, Score, load_midi, load_score
from .arrange import Arrangement, Options, PlayStep, arrange
from .fingering import STRATEGIES, CostModel, compare, measure
from .backend import DryRunBackend, PreviewBackend, SendInputBackend
from .player import Player, SchedulerConfig
