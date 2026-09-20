"""键位简谱 -> 曲谱 JSON 命令行（与 GUI「导入简谱」共用同一套逻辑）。

语法与示例见 gtiharmonica/jianpu.py 的模块文档。

用法::

    python tools/jianpu2score.py 输入.txt "曲名" [输出.json] [--sec 0.6] [--gap 0.5]

输入是行分隔的简谱文本；省略输出路径时写到曲库（%APPDATA%/GTIHarmonica/songs）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gtiharmonica.jianpu import (           # noqa: E402
    build_score, default_library, describe,
)


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    src, title = argv[1], argv[2]
    sec, gap = 0.6, 0.5
    if '--sec' in argv:
        sec = float(argv[argv.index('--sec') + 1])
    if '--gap' in argv:
        gap = float(argv[argv.index('--gap') + 1])
    out = (argv[3] if len(argv) > 3 and not argv[3].startswith('--')
           else os.path.join(default_library(), '%s.json' % title))
    with open(src, encoding='utf-8') as f:
        text = f.read()
    score = build_score(text, title, sec=sec, gap=gap)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    from gtiharmonica.score import save_json_score
    save_json_score(score, out)

    # 汇报：概览 + 修饰键用量（走真实编排）
    from gtiharmonica.instrument import Instrument
    from gtiharmonica.arrange import Options, arrange
    plan = arrange(score, Instrument(), Options())
    import collections
    mods = collections.Counter(
        '无修饰' if not s.modifiers else '+'.join(s.modifiers)
        for s in plan.steps)
    print('转换完成: %s' % out)
    print('  %s' % describe(score, sec))
    print('  编排后修饰键用量: %s' % dict(mods))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
