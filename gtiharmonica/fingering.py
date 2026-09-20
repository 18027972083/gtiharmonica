"""指法策略：为每个音高挑选具体按法。

这是本工具相对原程序最主要的**可优化面**。

同一个音高通常有多个候选指法，例如 MIDI 60 可以是：

    z                    （不按修饰键）
    x  + 左键            （降八度 + 音阶第二级）
    ,  + 左键 + 中键      （……等等）

挑哪个直接影响：
  * 修饰键切换次数 —— 每次切换都要松开再按下，是最容易出错的环节
  * 手部移动距离   —— 影响连续音的稳定性
  * 同时按住的键数 —— 越多越容易被游戏漏读

原程序固定选择「修饰键最少」的那一个（= MinModifiers），
本模块提供 4 种策略，并支持用动态规划求全局最优。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .instrument import Fingering, Instrument, key_distance


# ---------------------------------------------------------------------------
# 成本模型
# ---------------------------------------------------------------------------

@dataclass
class CostModel:
    """指法切换成本。数值越大表示越应避免。"""

    modifier_switch: float = 1.00     # 修饰键集合发生变化
    key_move: float = 0.35            # 键盘移动（单位：键宽）
    extra_press: float = 0.30         # 每多按一个修饰键
    key_repeat_bonus: float = -0.20   # 复用同一个音阶键（负成本 = 奖励）
    octave_jump: float = 0.15         # 跨八度修饰键切换的附加成本

    def transition(self, prev: Optional[Fingering], cur: Fingering) -> float:
        """从 prev 切到 cur 的代价。prev 为 None 表示这是第一个音。"""
        cost = self.extra_press * len(cur.modifiers)

        if prev is None:
            return cost

        prev_mods, cur_mods = set(prev.modifiers), set(cur.modifiers)

        # 修饰键集合的对称差 = 需要松开 + 需要按下的修饰键总数
        changed = prev_mods ^ cur_mods
        if changed:
            cost += self.modifier_switch * len(changed)
            # 跨八度（左/右键互换或增减）额外惩罚：这两类切换在游戏里最容易丢音
            octave_keys = {'mouse_left', 'mouse_right'}
            if (prev_mods ^ cur_mods) & octave_keys:
                cost += self.octave_jump

        # 键盘移动
        cost += self.key_move * key_distance(prev.key, cur.key)

        # 同键复用奖励
        if prev.key == cur.key:
            cost += self.key_repeat_bonus

        return cost


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------

@dataclass
class Metrics:
    """一次编排的质量指标，用于横向对比不同策略。"""

    notes: int = 0
    total_presses: int = 0
    modifier_notes: int = 0
    modifier_switches: int = 0
    key_changes: int = 0
    motion: float = 0.0
    max_simultaneous: int = 0

    def as_row(self) -> List[str]:
        return ['%d' % self.notes, '%d' % self.total_presses,
                '%d (%.0f%%)' % (self.modifier_notes,
                                 100.0 * self.modifier_notes / max(self.notes, 1)),
                '%d' % self.modifier_switches, '%d' % self.key_changes,
                '%.1f' % self.motion, '%d' % self.max_simultaneous]


METRIC_HEADERS = ['音符', '按键次数', '需修饰键', '修饰键切换', '换键次数', '移动量', '最大同按']


def measure(fingerings: Sequence[Fingering]) -> Metrics:
    m = Metrics(notes=len(fingerings))
    prev: Optional[Fingering] = None
    for fing in fingerings:
        m.total_presses += fing.presses
        if fing.modifiers:
            m.modifier_notes += 1
        m.max_simultaneous = max(m.max_simultaneous, fing.presses)
        if prev is not None:
            if set(prev.modifiers) != set(fing.modifiers):
                m.modifier_switches += 1
            if prev.key != fing.key:
                m.key_changes += 1
            m.motion += key_distance(prev.key, fing.key)
        prev = fing
    return m


# ---------------------------------------------------------------------------
# 策略
# ---------------------------------------------------------------------------

class FingeringStrategy(ABC):
    """把音高序列映射成指法序列。"""

    name = 'base'
    description = ''

    @abstractmethod
    def plan(self, pitches: Sequence[int],
             table: Dict[int, List[Fingering]]) -> List[Fingering]:
        ...

    def __repr__(self) -> str:
        return '<%s>' % self.name


class MinModifiers(FingeringStrategy):
    """原程序的策略：每个音独立选择修饰键最少的指法。

    简单、可预测，但会造成修饰键频繁开关。
    """

    name = 'min-modifiers'
    description = '每个音选修饰键最少的指法（原程序行为）'

    def plan(self, pitches, table):
        return [table[p][0] for p in pitches]


class Greedy(FingeringStrategy):
    """贪心：在当前音的所有候选里，选「相对上一个音切换成本最小」的。"""

    name = 'greedy'
    description = '逐个音符贪心最小化切换成本'

    def __init__(self, model: Optional[CostModel] = None):
        self.model = model or CostModel()

    def plan(self, pitches, table):
        out: List[Fingering] = []
        prev: Optional[Fingering] = None
        for pitch in pitches:
            best = min(table[pitch],
                       key=lambda f: self.model.transition(prev, f))
            out.append(best)
            prev = best
        return out


class Optimal(FingeringStrategy):
    """全局最优：用动态规划（Viterbi）求整条轨迹的最小总成本。

    相比贪心，它愿意在某个音上多花一点代价，换取后续更少的修饰键切换。
    时间复杂度 O(n · k²)，k 是单个音高的候选数（通常 ≤ 8），
    一万个音符也在毫秒级完成。
    """

    name = 'optimal'
    description = '动态规划全局最优（推荐）'

    def __init__(self, model: Optional[CostModel] = None):
        self.model = model or CostModel()

    def plan(self, pitches, table):
        if not pitches:
            return []

        layers: List[List[Fingering]] = [table[p] for p in pitches]
        if len(layers) == 1:
            return [layers[0][0]]

        # dp[i] = 走到当前音第 i 个候选的最小累计成本
        dp = [self.model.transition(None, f) for f in layers[0]]
        predecessors: List[List[int]] = []

        for idx in range(1, len(layers)):
            cands, prev_cands = layers[idx], layers[idx - 1]
            new_dp = [0.0] * len(cands)
            pred = [0] * len(cands)
            for j, cur in enumerate(cands):
                best_cost, best_i = float('inf'), 0
                for i, prev in enumerate(prev_cands):
                    c = dp[i] + self.model.transition(prev, cur)
                    if c < best_cost:
                        best_cost, best_i = c, i
                new_dp[j], pred[j] = best_cost, best_i
            dp = new_dp
            predecessors.append(pred)

        # Viterbi 回溯：predecessors[idx] 把 layers[idx+1] 的下标映射回 layers[idx]
        j = min(range(len(dp)), key=lambda k: dp[k])
        path = [layers[-1][j]]
        for idx in range(len(predecessors) - 1, -1, -1):
            j = predecessors[idx][j]
            path.append(layers[idx][j])
        path.reverse()
        return path


class StableKey(FingeringStrategy):
    """优先复用上一个音阶键，把修饰键切换集中处理。

    适合指法密集、手速吃紧的曲子。
    """

    name = 'stable-key'
    description = '优先保持同一个音阶键，减少换键'

    def __init__(self, model: Optional[CostModel] = None):
        self.model = model or CostModel(key_move=1.5, key_repeat_bonus=-1.2,
                                        modifier_switch=0.6)

    def plan(self, pitches, table):
        out: List[Fingering] = []
        prev: Optional[Fingering] = None
        for pitch in pitches:
            best = min(table[pitch],
                       key=lambda f: self.model.transition(prev, f))
            out.append(best)
            prev = best
        return out


# ---------------------------------------------------------------------------

STRATEGIES: Dict[str, type] = {
    MinModifiers.name: MinModifiers,
    Greedy.name: Greedy,
    Optimal.name: Optimal,
    StableKey.name: StableKey,
}


def build_strategy(name: str, model: Optional[CostModel] = None) -> FingeringStrategy:
    if name not in STRATEGIES:
        raise KeyError('未知策略 %r，可选：%s' % (name, ', '.join(STRATEGIES)))
    cls = STRATEGIES[name]
    if cls is MinModifiers:
        return cls()
    return cls(model)


def compare(pitches: Sequence[int], table: Dict[int, List[Fingering]],
            model: Optional[CostModel] = None) -> List[Tuple[str, Metrics, float]]:
    """跑遍所有策略并统计指标，返回 [(策略名, 指标, 总成本), ...]。"""
    results = []
    for name, cls in STRATEGIES.items():
        strat = cls() if cls is MinModifiers else cls(model)
        plan = strat.plan(pitches, table)
        results.append((name, measure(plan), total_cost(plan, model)))
    return results


def total_cost(plan: Sequence[Fingering], model: Optional[CostModel] = None) -> float:
    """按成本模型累计整条指法轨迹的代价。Optimal 策略应给出最小值。"""
    m = model or CostModel()
    total = 0.0
    prev = None
    for fing in plan:
        total += m.transition(prev, fing)
        prev = fing
    return total
