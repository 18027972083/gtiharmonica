"""乐器建模：键位、音阶、指法生成。

核心概念
--------
游戏里的口琴有 8 个音阶键，外加 3 个鼠标「修饰键」（按住生效）用来移调：

    鼠标左键 = 降一个八度     鼠标中键 = 升半音     鼠标右键 = 升一个八度

因此同一个音高往往有多种按法，我们把每一种按法叫一个 **Fingering**
（音阶键 + 一组修饰键）。本模块负责穷举出全部候选，交给 fingering 策略去挑。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

# ---------------------------------------------------------------------------
# 默认键位（对应游戏内默认设置）
# ---------------------------------------------------------------------------

#: 8 个音阶键
KEYS: Tuple[str, ...] = ('z', 'x', 'c', 'v', 'b', 'n', 'm', ',')

#: 相对基础音的半音偏移 —— 大调音阶 1 2 3 4 5 6 7 高音1
STEPS: Tuple[int, ...] = (0, 2, 4, 5, 7, 9, 11, 12)

#: 修饰键 -> (按下标志, 抬起标志)，标志值取自 SendInput 的 MOUSEEVENTF_*
MOUSE_FLAGS: Dict[str, Tuple[int, int]] = {
    'mouse_left':   (0x0002, 0x0004),   # LEFTDOWN / LEFTUP
    'mouse_middle': (0x0020, 0x0040),   # MIDDLEDOWN / MIDDLEUP
    'mouse_right':  (0x0008, 0x0010),   # RIGHTDOWN / RIGHTUP
}

#: 英文标点 -> OEM 虚拟键码。ord() 对它们给出的值不是正确的 VK，
#: 例如 ord(',') == 44 落在 VK_SNAPSHOT 区域，正确值是 188。
OEM_VK: Dict[str, int] = {
    ',': 188, '.': 190, ';': 186, '/': 191,
    '[': 219, ']': 221, '-': 189, '=': 187,
}

#: 允许用作音阶键的字符集合
SUPPORTED_CHARS = set('abcdefghijklmnopqrstuvwxyz0123456789,.;/[]-=')

#: QWERTY 物理布局（列, 行），用于计算手部移动成本。行 0 = 数字行
_KEYBOARD_POS: Dict[str, Tuple[float, float]] = {}
for _row, _keys in enumerate(('1234567890', 'qwertyuiop', 'asdfghjkl;', 'zxcvbnm,./')):
    for _col, _ch in enumerate(_keys):
        # 逐行缩进半个键位，近似真实错位键盘
        _KEYBOARD_POS[_ch] = (_col + _row * 0.25, float(_row))


def key_position(ch: str) -> Tuple[float, float]:
    """返回按键在键盘上的近似坐标；未知字符回落到 (0, 0)。"""
    return _KEYBOARD_POS.get(ch.lower(), (0.0, 0.0))


def key_distance(a: str, b: str) -> float:
    """两个按键之间的欧氏距离（单位：键宽）。"""
    ax, ay = key_position(a)
    bx, by = key_position(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Fingering:
    """一次按键组合：先按住 modifiers，再按 key。"""

    key: str
    modifiers: Tuple[str, ...] = ()

    @property
    def presses(self) -> int:
        """这次指法需要发多少次按下事件。"""
        return len(self.modifiers) + 1

    @property
    def uses_mouse(self) -> bool:
        return bool(self.modifiers)

    def describe(self) -> str:
        if not self.modifiers:
            return self.key
        return '%s + %s' % ('+'.join(self.modifiers), self.key)

    def __str__(self) -> str:  # pragma: no cover - 便于调试
        return self.describe()


# ---------------------------------------------------------------------------
# 乐器
# ---------------------------------------------------------------------------

@dataclass
class Instrument:
    """口琴键位与音域配置。

    base 是第一个音阶键（keys[0]）在不按任何修饰键时发出的 MIDI 音高。
    默认 60 = C4，即 Z 键发 C4。
    """

    base: int = 60
    keys: Tuple[str, ...] = KEYS
    lower: str = 'mouse_left'
    semitone: str = 'mouse_middle'
    upper: str = 'mouse_right'
    semitone_step: int = 1          # +1 表示中键升半音，-1 表示降半音
    _cache: Dict[int, List[Fingering]] = field(default_factory=dict,
                                               repr=False, compare=False)

    # -- 校验 ---------------------------------------------------------------

    def validate(self) -> None:
        if len(self.keys) != 8:
            raise ValueError('音阶键必须是 8 个，当前 %d 个' % len(self.keys))
        if len(set(self.keys)) != 8:
            raise ValueError('音阶键不能重复：%s' % (self.keys,))
        bad = [k for k in self.keys if k not in SUPPORTED_CHARS]
        if bad:
            raise ValueError('不支持的音阶键 %s（只允许字母、数字和 ,.;/[]-=）' % bad)
        if not (24 <= self.base <= 96):
            raise ValueError('base 必须在 24..96（MIDI 音高）之间，当前 %d' % self.base)
        if self.semitone_step not in (1, -1):
            raise ValueError('semitone_step 只能是 +1 或 -1')
        if {self.lower, self.semitone, self.upper} != set(MOUSE_FLAGS):
            raise ValueError('三个修饰键必须分别是 mouse_left / mouse_middle / mouse_right')

    # -- 指法生成 -----------------------------------------------------------

    def fingerings(self) -> Dict[int, List[Fingering]]:
        """返回 {pitch: [候选指法...]}，候选按「修饰键数量升序」排序。

        与原程序不同，这里**保留全部候选**而不是只留最优的一个，
        因为「哪个最优」取决于演奏场景，交给 fingering 策略决定。
        """
        if self._cache:
            return self._cache
        self.validate()

        table: Dict[int, List[Fingering]] = {}
        octave_plan = (
            (0, ()),
            (-12, (self.lower,)),
            (12, (self.upper,)),
        )
        for octave, modifier in octave_plan:
            for half in (0, self.semitone_step):
                mods = modifier + ((self.semitone,) if half else ())
                for key, step in zip(self.keys, STEPS):
                    pitch = self.base + octave + step + half
                    if not (0 <= pitch <= 127):
                        continue
                    bucket = table.setdefault(pitch, [])
                    fing = Fingering(key, mods)
                    if fing not in bucket:
                        bucket.append(fing)

        for bucket in table.values():
            bucket.sort(key=lambda f: (len(f.modifiers), f.key))
        self._cache = table
        return table

    # -- 便捷查询 -----------------------------------------------------------

    def playable_range(self) -> Tuple[int, int]:
        table = self.fingerings()
        return min(table), max(table)

    def is_playable(self, pitch: int) -> bool:
        return pitch in self.fingerings()

    def nearest_playable(self, pitch: int, prefer: str = 'nearest') -> int | None:
        """把超音域的音高折到最近的可演奏音高。

        prefer: 'nearest' 就近 | 'down' 优先向下 | 'up' 优先向上
        """
        table = self.fingerings()
        if pitch in table:
            return pitch
        candidates = []
        for shift in range(-5, 6):          # ±5 个八度
            p = pitch + shift * 12
            if p in table:
                if prefer == 'down' and shift > 0:
                    continue
                if prefer == 'up' and shift < 0:
                    continue
                candidates.append((abs(shift), p))
        if not candidates:
            return None
        candidates.sort()
        return candidates[0][1]

    def export_config(self) -> dict:
        """导出为与原程序 instrument.json 兼容的结构。"""
        return {
            'base': self.base,
            'keys': list(self.keys),
            'lower': self.lower,
            'semitone': self.semitone,
            'upper': self.upper,
            'semitone_step': self.semitone_step,
        }

    @classmethod
    def from_config(cls, data: dict) -> 'Instrument':
        """从 instrument.json 载入（忽略未知字段，便于直接复用旧配置）。"""
        known = {'base', 'keys', 'lower', 'semitone', 'upper', 'semitone_step'}
        kwargs = {k: v for k, v in data.items() if k in known}
        if 'keys' in kwargs:
            kwargs['keys'] = tuple(kwargs['keys'])
        return cls(**kwargs)


# 音名工具 -----------------------------------------------------------------

_NOTE_NAMES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')


def note_name(pitch: int) -> str:
    """MIDI 音高 -> 科学音高记号，例如 60 -> C4。"""
    return '%s%d' % (_NOTE_NAMES[pitch % 12], pitch // 12 - 1)
