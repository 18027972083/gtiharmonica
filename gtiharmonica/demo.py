"""内置示例曲目。

前两首（小星星、欢乐颂）是从原程序 GTIartist v1.1 硬编码的
`demo_scores()` 逐字段复刻的：同样的音高序列、同样的拍数、同样的 BPM，
以及同样的「每 7 个音标记一次乐句结尾」规则 —— 所以换气位置也和原来一致。

其余是校准用的练习曲。

关于版权：这里选用的都是公有领域旋律。
从别处导入的流行歌曲编曲（受版权保护）请只在本机使用，
不要再打包分发给他人。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Sequence, Tuple

from .library_state import is_deleted

MARKER = '.demo_installed'


# ---------------------------------------------------------------------------
# 与原程序一致的曲谱构造
# ---------------------------------------------------------------------------

def make(title: str, pitches: Sequence[Optional[int]],
         beats: Sequence[float], bpm: float,
         phrase_every: int = 7) -> dict:
    """按拍数与 BPM 生成曲谱。

    逐个字段复刻原程序 gtiartist.score.make()：
      start    = 累计拍数 × 60 / bpm
      duration = 该音拍数 × 60 / bpm
      phrase_end = (序号 + 1) % phrase_every == 0
    注意拍数是**无条件累加**的，即使某个位置是休止（pitch 为 None）。
    """
    notes: List[dict] = []
    beat = 0.0
    for index, (pitch, length) in enumerate(zip(pitches, beats)):
        if pitch is not None:
            notes.append({
                'pitch': int(pitch),
                'start': round(beat * 60.0 / bpm, 6),
                'duration': round(length * 60.0 / bpm, 6),
                'velocity': 80,
                'track': 0,
                'phrase_end': (index + 1) % phrase_every == 0,
            })
        beat += length
    return {'title': title, 'notes': notes}


# ---------------------------------------------------------------------------
# 内置曲目
# ---------------------------------------------------------------------------

# 小星星：42 个音，节拍型 [1,1,1,1,1,1,2] 重复 6 次，BPM 104
_TWINKLE = make(
    '小星星',
    (60, 60, 67, 67, 69, 69, 67,
     65, 65, 64, 64, 62, 62, 60,
     67, 67, 65, 65, 64, 64, 62,
     67, 67, 65, 65, 64, 64, 62,
     60, 60, 67, 67, 69, 69, 67,
     65, 65, 64, 64, 62, 62, 60),
    [1, 1, 1, 1, 1, 1, 2] * 6,
    104)

# 欢乐颂：30 个音，BPM 108
_ODE = make(
    '欢乐颂',
    (64, 64, 65, 67, 67, 65, 64, 62, 60, 60, 62, 64, 64, 62, 62,
     64, 64, 65, 67, 67, 65, 64, 62, 60, 60, 62, 64, 62, 60, 60),
    [1] * 12 + [1.5, 0.5, 2] + [1] * 12 + [1.5, 0.5, 2],
    108)

# C 大调音阶：用来熟悉键位与音高对应
_SCALE = make(
    'C大调音阶练习',
    (60, 62, 64, 65, 67, 69, 71, 72, 71, 69, 67, 65, 64, 62, 60),
    [0.5] * 15,
    120,
    phrase_every=8)

# 半音阶：用来校准「升半音」修饰键，也用来测最短可识别音长
_CHROMATIC = make(
    '半音阶练习',
    tuple(range(60, 73)) + tuple(range(71, 59, -1)),
    [0.4] * 25,
    120,
    phrase_every=13)

# 卡农片段（Pachelbel，公有领域）：跨八度，用来验证八度折叠与修饰键切换
_CANON = make(
    '卡农片段',
    (64, 62, 60, 62, 64, 64, 64, 62, 62, 62, 64, 67, 67,
     64, 62, 60, 62, 64, 64, 64, 62, 62, 64, 62, 60),
    [1] * 25,
    100,
    phrase_every=13)

# 送别（李叔同，公有领域）
_FAREWELL = make(
    '送别',
    (67, 67, 67, 64, 67, 69, 69, 67, 67, 67,
     62, 64, 65, 64, 62, 60, 62, 64, 65, 64, 62, 60),
    [1, 1, 1, 1, 2, 1, 1, 1, 1, 2,
     1, 1, 1, 1, 2, 1, 1, 1, 1, 1, 1, 2],
    90,
    phrase_every=11)

DEMO_SONGS: Dict[str, dict] = {
    '小星星.json': _TWINKLE,
    '欢乐颂.json': _ODE,
    '送别.json': _FAREWELL,
    '卡农片段.json': _CANON,
    'C大调音阶练习.json': _SCALE,
    '半音阶练习.json': _CHROMATIC,
}


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------

def ensure_demo_songs(directory: str, force: bool = False) -> int:
    """把内置曲目写入曲库目录，返回新写入的数量。

    只在「这台机器从没装过内置曲目」时写入：老曲库里已经有曲子就只留个
    安装标记走人，用户以后把曲库清空了也不会被重新灌一遍内置曲。删过某首
    内置曲的话该名字在删除名单里，同样跳过。

    force=True 时按文件名逐个补齐缺失的内置曲目（仍跳过删除名单）。
    """
    try:
        os.makedirs(directory, exist_ok=True)
        marker = os.path.join(directory, MARKER)
        existing = [n for n in os.listdir(directory)
                    if n.lower().endswith(('.mid', '.midi', '.json'))]
        if not force and (existing or os.path.exists(marker)):
            _touch_marker(marker)
            return 0

        written = 0
        for name, payload in DEMO_SONGS.items():
            path = os.path.join(directory, name)
            if os.path.exists(path) and not force:
                continue
            if is_deleted(directory, name):
                continue
            with open(path, 'w', encoding='utf8') as fh:
                json.dump({'format': 'gtiharmonica-score-v1',
                           'title': payload['title'],
                           'notes': payload['notes']},
                          fh, ensure_ascii=False, indent=1)
            written += 1

        _touch_marker(marker)
        return written
    except OSError:
        return 0


def _touch_marker(marker: str) -> None:
    try:
        with open(marker, 'w', encoding='utf8') as fh:
            fh.write('内置曲目已安装过\n')
    except OSError:
        pass


def builtin_titles() -> List[str]:
    return [song['title'] for song in DEMO_SONGS.values()]
