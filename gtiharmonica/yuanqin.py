"""原琴谱解析（sigma player / Sigma-呱呱谱 / B站社区键盘谱 -> 曲谱）。

「原琴」指原神的风物之诗琴：三排 21 键，每排一个八度的大调七音，
没有半音键。三角洲口琴的三个八度带（左键/无修饰/右键）与它一一
对应，且口琴是它的严格超集 —— 任何原琴谱都能无损转成口琴谱。

格式语义以 Sigma Player 官方说明书为准（Sigma-呱呱谱章节）：

音符
    Z~M / A~J / Q~U   低/中/高三个八度的大调 1..7
    字母连写           同时按（和弦），(AB) 括号同样同时按
    #Z                 黑键（升半音）
    一~七 / 壹~柒      低/高扩展八度（呱呱谱预留轨）
    数字混合记法        +6 6 -6 = 高/中/低 la，与 Y H N 等价

时值（两种记法自动判别，同一份谱不混用）
    键盘谱模式         / 是拍线，段内 token（含和弦、空格占位）均分
                       拍长；空段 = 休止一拍。B站 UP 主谱绝大多数如此
    呱呱谱模式         出现延时符号 + - = ·：+ = 4X（四分）、- = 2X、
                       = = X、· = X/3；X = 拍长/4。字母连写同时按

头部与杂项
    BPM:190 4/4拍      拍长 = 60/BPM
    《0.2》            + 符号的停顿秒数（拍长）
    【…】注释、行尾 /N 小节编号、【歌词】均忽略
    ▲ 休止、| 多音轨分隔（视为单轨，只取第一轨）
    同一段谱贴两遍（字母版+数字版）时自动只取第一遍

base 固定 60（C4），与 jianpu.py 同一个键位世界观：谱面记录的是
键位而不是绝对音高，与原曲的绝对音高差用「移调」解决。
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .jianpu import STEP, describe
from .score import Note, Score

#: 21 键 -> (数字 1..7, 八度偏移)
KEYBOARD = {
    'Q': (1, 12), 'W': (2, 12), 'E': (3, 12), 'R': (4, 12),
    'T': (5, 12), 'Y': (6, 12), 'U': (7, 12),
    'A': (1, 0), 'S': (2, 0), 'D': (3, 0), 'F': (4, 0),
    'G': (5, 0), 'H': (6, 0), 'J': (7, 0),
    'Z': (1, -12), 'X': (2, -12), 'C': (3, -12), 'V': (4, -12),
    'B': (5, -12), 'N': (6, -12), 'M': (7, -12),
}

#: 呱呱谱汉字八度：一~七 = 低两个八度的 1..7，壹~柒 = 高一个八度
CN_LOW = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7}
CN_HIGH = {'壹': 1, '贰': 2, '叁': 3, '肆': 4, '伍': 5, '陆': 6, '柒': 7}

_DIGIT_TOKEN = re.compile(r'[#]?[+-]?[1-7]')
_MEASURE_NO = re.compile(r'/(\d+)\s*$')
_DELAY = set('+=·')
#: 可以作为谱面起点的标题（「简谱」「数字谱」常出现在 UP 主的说明
#: 文字里，不能当起点，只能当结束标记）
_START_KEYS = ('电脑谱', '键盘谱', '呱呱谱')
#: 带冒号的下一个谱区标题 = 当前谱面的结束（字母版 + 数字版两段式）
_SECTION_TITLE = re.compile(r'(?:电脑谱|键盘谱|简谱|数字谱|呱呱谱)\s*[:：]')
#: jpeditor（genshin-jianpu-editor）导出键盘谱的元数据行
_JPED_META = re.compile(
    r'^(//|标题=|副标题=|作曲=|编曲=|调号=|1=[A-G]|拍号=|\d+/\d+拍[：:]'
    r'|速度=|点=|花括号=|方括号=|音符=|空格=|声部=)')
_JPED_BPM = re.compile(r'速度=.*?\(.*?([\d.]+)\s*BPM\)')
_JPED_DIV = re.compile(r'点=(\d+)分音符')
_KBD_LETTER = re.compile(r'[#]?[A-Z]')


def looks_like_yuanqin(text: str) -> bool:
    """判断粘贴文本是不是原琴谱（字母谱 / BPM 头 / 呱呱延时 / jpeditor）。"""
    if re.search(r'BPM\s*[:：]\s*\d+', text) or re.search(r'《[\d.]+》', text):
        return True
    if _JPED_META.search(text) and re.search(r'[A-Z]', text):
        return True
    letters = set(re.findall(r'[QWERTYUASDFGHJZXCVBNM]', text))
    # 出现多个 21 键字母且不是数字简谱（数字+减时线那种）
    return len(letters) >= 4 and not re.search(r'[0-8][_*]', text)


def _is_jpeditor(text: str) -> bool:
    """是否为 jpeditor 导出的键盘谱（线性时值网格模型）。"""
    return bool(_JPED_BPM.search(text) or _JPED_DIV.search(text)
                or re.search(r'^键盘谱\s*$', text, re.M))


def _extract_body(text: str) -> str:
    """只取谱面区：谱区标题之后、下一个带冒号的谱区标题之前。"""
    start = -1
    for key in _START_KEYS:
        pos = text.find(key)
        if pos >= 0 and (start < 0 or pos < start):
            start = pos
    body = text[start:] if start >= 0 else text
    m = _SECTION_TITLE.search(body, 12)
    if m:
        body = body[:m.start()]
    return body


def _decode_token(token: str) -> Tuple[List[int], bool]:
    """一个 token -> (同时按下的音高列表, 是否为音符)。

    支持 Z~M/A~J/Q~U 字母、# 黑键、+N/-N 数字、汉字八度。
    """
    pitches: List[int] = []
    i = 0
    while i < len(token):
        ch = token[i]
        if ch == '#':
            sharp = 1
            i += 1
            if i >= len(token):
                return ([], False)
            ch = token[i]
        else:
            sharp = 0
        if ch in KEYBOARD:
            num, off = KEYBOARD[ch]
            pitches.append(60 + STEP[num] + off + sharp)
        elif ch in CN_LOW:
            pitches.append(60 + STEP[CN_LOW[ch]] - 12 + sharp)
        elif ch in CN_HIGH:
            pitches.append(60 + STEP[CN_HIGH[ch]] + 12 + sharp)
        else:
            m = _DIGIT_TOKEN.match(token[i:])
            if not m:
                return ([], False)
            s = m.group(0)
            oct_off = 12 if s.startswith('+') else (
                -12 if s.startswith('-') else 0)
            num = int(s[-1])
            pitches.append(60 + STEP[num] + oct_off + sharp)
            i += len(s) - 1
        i += 1
    return (pitches, bool(pitches))


def _split_events(segment: str) -> List[Tuple[List[int], bool]]:
    """一段（两根 / 之间）-> [(pitches, is_note)...]。

    括号 (AB) 整体一个事件；▲ 视为休止；【】注释已在外层剥掉。
    """
    events: List[Tuple[List[int], bool]] = []
    buf = ''
    depth = 0

    def flush():
        nonlocal buf
        buf = buf.strip()
        if not buf:
            return
        if buf == '▲':
            events.append(([], False))
        else:
            events.append(_decode_token(buf))
        buf = ''

    for ch in segment:
        if ch == '(':
            flush()
            depth = 1
            buf = ''
        elif ch == ')':
            if depth:
                pitches, ok = _decode_token(buf)
                if ok:
                    events.append((pitches, True))
                buf = ''
                depth = 0
        elif depth:
            if not ch.isspace():
                buf += ch
        elif ch in '【】':
            continue
        elif ch.isspace():
            flush()
        elif ch == '|':
            flush()
            events.append(([], True))   # 多音轨分隔：占位推进时间
        else:
            buf += ch
    if not depth:
        flush()
    return events


def _has_delay_symbols(body: str) -> bool:
    """谱面是否使用呱呱延时符号（+-=·）作为节奏主体。"""
    tokens = [t for t in re.split(r'[\s/()【】|▲]+', body) if t]
    if not tokens:
        return False
    delay = sum(1 for t in tokens if all(c in '+-=·' for c in t))
    return delay >= len(tokens) * 0.3


def _dedupe(notes: List[Note]) -> List[Note]:
    """多声部谱同一键位会在多个声部重复出现，合并为单音。"""
    seen = set()
    out = []
    for n in sorted(notes, key=lambda n: (n.start, n.pitch)):
        key = (round(n.start, 3), n.pitch)
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


def _parse_jpeditor(text: str, title: str, bpm: float) -> Score:
    """jpeditor 键盘谱（线性时值网格）-> Score。

    语义：每个音符/括号和弦/点（`.` 占位）各占一个「点=16分音符」
    网格位；括号内逗号是多声部分隔（同起点），`/`、空格只是排版。
    """
    mdiv = _JPED_DIV.search(text)
    division = int(mdiv.group(1)) if mdiv else 16
    unit = 60.0 / bpm * 4.0 / division
    notes: List[Note] = []
    t = 0.0
    sharp = 0
    depth = 0
    group: List[int] = []

    def emit(pitches: List[int]) -> None:
        for p in pitches:
            notes.append(Note(pitch=p, start=round(t, 4),
                              duration=round(unit, 4), velocity=90,
                              track=0, phrase_end=False, tie=False))

    for raw in text.splitlines():
        line = raw.strip()
        if not line or _JPED_META.match(line):
            continue
        for ch in line:
            if ch == '#':
                sharp = 1
            elif ch == '(':
                depth += 1
                group = []
            elif ch == ')':
                if depth:
                    emit(group)
                    group = []
                    depth = max(0, depth - 1)
                    t += unit
                sharp = 0
            elif ch in KEYBOARD:
                pitch = 60 + STEP[KEYBOARD[ch][0]] + KEYBOARD[ch][1] + sharp
                if depth:
                    group.append(pitch)
                else:
                    emit([pitch])
                    t += unit
                sharp = 0
            elif ch == '.':
                if not depth:
                    t += unit
            elif ch in ",'[]{}":
                continue
            else:
                continue
    if not notes:
        raise ValueError('jpeditor 谱面没有解析到音符')
    notes = _dedupe(notes)
    return Score(title=title, notes=notes)


def parse_yuanqin(text: str, title: str) -> Score:
    """原琴谱全文 -> Score。解析失败抛 ValueError（带行号）。"""
    beat = 60.0 / detect_bpm(text)
    if _is_jpeditor(text):
        return _parse_jpeditor(text, title, detect_bpm(text))

    body = _extract_body(text)
    lyric_mode = _has_delay_symbols(body)
    notes: List[Note] = []
    errors: List[str] = []
    t = 0.0
    last_measure = 0

    for no, raw in enumerate(body.splitlines(), 1):
        mno = _MEASURE_NO.search(raw.rstrip())
        line = _MEASURE_NO.sub('', raw).strip()
        if not line:
            continue
        # 同一段谱贴两遍（字母版+数字版）时小节号会归零 —— 只取第一遍
        if mno:
            value = int(mno.group(1))
            if value <= last_measure:
                break
            last_measure = value
        line = re.sub(r'【[^】]*】', '', line)          # 注释/歌词
        if not line or line.startswith('BPM') or line.endswith('拍'):
            continue
        if _SECTION_TITLE.match(line):                  # 谱区标题不占时间
            continue
        # 行尾的 / 只是排版分隔，不是第 5 拍
        line = line.rstrip().rstrip('/').rstrip()
        if not line:
            continue
        segments = line.split('/')
        for seg in segments:
            if lyric_mode:
                # 呱呱谱：顺序读取，延时符号停顿，字母连写同时按
                for token, is_ev in _token_stream(seg):
                    if token in ('+', '-', '=', '·'):
                        mult = {'+': 4.0, '-': 2.0, '=': 1.0, '·': 1 / 3}[token]
                        t += beat / 4.0 * mult
                        continue
                    pitches, ok = _decode_token(token)
                    if not ok:
                        if token and any(c.isalpha() and c not in KEYBOARD
                                         and c.isascii() for c in token):
                            errors.append('第 %d 行: 无法解析 %r' % (no, token))
                        continue
                    dur = beat / 4.0    # 音符占一个十六分位，延到下一符号
                    for p in pitches:
                        notes.append(Note(pitch=p, start=round(t, 4),
                                          duration=round(dur, 4), velocity=90,
                                          track=0, phrase_end=False,
                                          tie=False))
                    t += dur
                continue
            # 键盘谱：段 = 一拍，段内事件均分
            events = _split_events(seg)
            if not events:
                t += beat
                continue
            step = beat / len(events)
            for (pitches, is_note) in events:
                if is_note and pitches:
                    for p in pitches:
                        notes.append(Note(
                            pitch=p, start=round(t, 4),
                            duration=round(step, 4), velocity=90,
                            track=0, phrase_end=False, tie=False))
                t += step
    if errors:
        raise ValueError('\n'.join(errors[:5]) +
                         ('\n… 共 %d 处' % len(errors)
                          if len(errors) > 5 else ''))
    if not notes:
        raise ValueError('没有解析到任何音符 —— 请确认粘贴的是谱面区')
    notes = _dedupe(notes)
    return Score(title=title, notes=notes)


def _token_stream(seg: str) -> List[Tuple[str, bool]]:
    """呱呱谱片段 -> [(token, True)...]；括号和弦为一个 token。"""
    out: List[Tuple[str, bool]] = []
    buf = ''
    depth = 0
    for ch in seg:
        if ch == '(':
            if buf:
                out.append((buf, True))
                buf = ''
            depth = 1
            buf = ''
        elif ch == ')':
            if depth:
                out.append((buf, True))
                buf = ''
                depth = 0
        elif depth:
            if not ch.isspace():
                buf += ch
        elif ch.isspace():
            if buf:
                out.append((buf, True))
                buf = ''
        else:
            buf += ch
    if buf:
        out.append((buf, True))
    return out


def detect_bpm(text: str) -> float:
    """从谱面文本提取 BPM（BPM:190 头 /《0.3158》速度 / jpeditor 速度行）。"""
    m = re.search(r'BPM\s*[:：]\s*([\d.]+)', text)
    if not m:
        m = _JPED_BPM.search(text)
    if m:
        return float(m.group(1))
    mt = re.search(r'《([\d.]+)》', text)
    if mt:
        return 60.0 / float(mt.group(1))
    return 190.0


def describe_yuanqin(text: str, score: Score) -> str:
    """原琴谱曲谱概览（对话框预览用，按谱面 BPM 折算拍值）。"""
    return describe(score, 60.0 / detect_bpm(text))
