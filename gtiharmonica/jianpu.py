"""键位简谱解析与转换（文本 -> 曲谱）。

「导入简谱」对话框与 tools/jianpu2score.py 命令行共用这一份逻辑。

记法约定（与社区键位谱一致）：

音高
    数字 1..7   = 琴的 8 个音阶键中的前 7 个（z x c v b n m），中音区
    数字 8      = 第 8 个键（逗号键），高音 do
    点在数字前  = 高八度（游戏里按住右键）    .5 = 右键+b
    点在数字后  = 低八度（游戏里按住左键）    5. = 左键+b
    连写        = 逐个音（"65" = 6、5 两个音）

时值（默认每音一拍；sec 参数定一拍多少秒）
    5 -         = 两拍（每个 - 追加一拍，可连用）
    5_          = 半拍（下划线数量 = 减时线数量：5__ = 四分之一拍）
    5*          = 附点（1.5 拍）
    0           = 休止一拍（0_ / 0* / 0 - 同样生效）

其他
    每行        = 一个乐句（行末音标记 phrase_end，行间留气口）
    | 或 ｜     = 小节线，忽略
    行内中文    = 注释（歌词/备忘），自动剥离；# 开头整行注释

base 固定 60（C4）：键位谱记录的是**键位**而不是绝对音高，转换与编排
共用同一个 base 世界观即可保证「谱面数字 -> 实际按键」逐位还原 —— 弹出
来的调由游戏琴本身决定，不需要（也无法）在本工具里校准。
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from .score import Note, Score

#: 简谱数字 -> 相对 do 的半音偏移（1=do 2=re 3=mi 4=fa 5=sol 6=la 7=si 8=高do）
STEP = {1: 0, 2: 2, 3: 4, 4: 5, 5: 7, 6: 9, 7: 11, 8: 12}

TOKEN = re.compile(r'^([#b]?)(\.*)([0-8])(\.*)([_*]*)$')
BARLINE = re.compile(r'^[|｜]+$')

#: 语法速查（对话框里直接展示给用户）
SYNTAX_HELP = """音高：1..7 = 琴键 z x c v b n m（中音区），8 = 高音 do
     点在数字前 = 高音（按住右键）    .5 → 右键 + b
     点在数字后 = 低音（按住左键）    5. → 左键 + b
     变化音：#5 = 升半音（中键+5），b3 = 降半音（中键+3）
     连写 = 逐个音                    65 → 6、5 两拍音
时值：默认每音一拍   5 - 两拍   5_ 半拍   5__ 1/4拍   5* 附点   0 休止
其他：每行一个乐句；行尾可直接写歌词当注释；# 开头整行注释；| 小节线忽略
     （变化音 # 和注释 # 不冲突：# 紧贴数字是变化音，# 加空格或中文是注释）"""


def strip_comment(line: str) -> str:
    """剥掉行尾注释：第一个中文字符/全角括号起的内容视为注释。"""
    for i, ch in enumerate(line):
        if '\u4e00' <= ch <= '\u9fff' or ch in '（(':
            return line[:i]
    return line


def parse_line(line: str) -> List[Tuple[Optional[int], float]]:
    """一行简谱 -> [(midi 或 None=休止, 拍数)...]。

    解析失败的 token 抛 ValueError（消息里带原记号）。
    """
    out: List[Tuple[Optional[int], float]] = []
    for raw in strip_comment(line).split():
        tok = raw.strip()
        if BARLINE.match(tok):
            continue
        if tok == '-' or tok.startswith('-_'):
            # 增时线：- 追加一拍，-_ 追加半拍，-__ 追加四分之一拍…
            if not out:
                raise ValueError('行首出现孤立的增时线 %r' % raw)
            midi, beats = out[-1]
            out[-1] = (midi, beats + (1.0 if tok == '-'
                                      else 0.5 ** tok.count('_')))
            continue
        m = TOKEN.match(tok)
        if not m:
            # 连写："65" = 6、5 两个音，前导点与升降号作用于每一个音
            run = re.match(r'^([#b]?)(\.*)([0-8]+)$', tok)
            if run:
                acc, lead, digits = run.groups()
                off = 1 if acc == '#' else (-1 if acc == 'b' else 0)
                for ch in digits:
                    n = int(ch)
                    midi = None if n == 0 else 60 + STEP[n] + 12 * len(lead) + off
                    out.append((midi, 1.0))
                continue
            raise ValueError('无法解析的记号 %r' % raw)
        acc, lead, num, trail, suffix = m.groups()
        n = int(num)
        if n == 0:
            if acc:
                raise ValueError('休止符 0 不接受升降号 %r' % raw)
            midi = None
        else:
            midi = (60 + STEP[n] + 12 * len(lead) - 12 * len(trail)
                    + (1 if acc == '#' else (-1 if acc == 'b' else 0)))
        if midi is not None and not (0 < midi < 127):
            raise ValueError('记号 %r 超出音域' % raw)
        # 后缀里 _ 的个数 = 减时线，* = 附点；"3_*"、"3*_"
        # 两种顺序都接受，语义相同
        beats = (0.5 ** suffix.count('_')) * (1.5 if '*' in suffix else 1.0)
        out.append((midi, beats))
    return out


def parse_text(text: str) -> Tuple[List[Tuple[int, str, list]], List[str]]:
    """整段文本逐行解析。

    返回 (rows, errors)：rows = [(行号, 原文, parsed 或 None)]，
    errors = ['第 N 行: ...']。注释/空行不在 rows 里。
    """
    rows: List[Tuple[int, str, list]] = []
    errors: List[str] = []
    for no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        # 行首 # 是注释；但 "#5" 这种紧贴数字的是变化音记号，不是注释
        if not stripped or (stripped.startswith('#')
                            and not re.match(r'^#[b0-8]', stripped)):
            continue
        try:
            rows.append((no, stripped, parse_line(stripped)))
        except ValueError as exc:
            rows.append((no, stripped, None))
            errors.append('第 %d 行: %s' % (no, exc))
    return rows, errors


def build_score(text: str, title: str, sec: float = 0.6, gap: float = 0.5) -> Score:
    """整段简谱文本 -> Score。解析失败抛 ValueError（带行号）。"""
    rows, errors = parse_text(text)
    if errors:
        raise ValueError('\n'.join(errors[:5])
                         + ('\n… 共 %d 处' % len(errors) if len(errors) > 5 else ''))
    score = Score(title=title, notes=[])
    t = 0.0
    for _no, line, melody in rows:
        # 换气标记标在行内**最后一个实际音符**上 —— 行末常是休止符，
        # 而休止符不产生音符，标记没地方挂（实测换气意图全丢）。
        last_pitched = None
        for (midi, beats) in melody:
            dur = beats * sec
            if midi is not None:
                score.notes.append(Note(
                    pitch=midi, start=round(t, 4), duration=round(dur, 4),
                    velocity=90, track=0, phrase_end=False,
                ))
                last_pitched = score.notes[-1]
            t += dur
        if last_pitched is not None:
            last_pitched.phrase_end = True
        t += gap
    score.notes.sort(key=lambda n: (n.start, n.pitch))
    return score


def describe(score: Score, sec: float) -> str:
    """曲谱概览（对话框预览用）。"""
    import collections
    if not score.notes:
        return '（没有音符 —— 只写休止符是出不了声的）'
    dur_units = collections.Counter(round(n.duration / sec, 2) for n in score.notes)
    parts = ['%d 个音符' % len(score.notes),
             '音域 %d..%d' % (min(n.pitch for n in score.notes),
                              max(n.pitch for n in score.notes)),
             '时长 %.1f 秒' % max(n.start + n.duration for n in score.notes),
             '时值(拍): %s' % ', '.join('%g拍×%d' % (k, v)
                                        for k, v in sorted(dur_units.items()))]
    return ' · '.join(parts)


def default_library() -> str:
    return os.path.join(os.environ.get('APPDATA', ''), 'GTIHarmonica', 'songs')
