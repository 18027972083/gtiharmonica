"""抓长曲子的旋律预览，验证「固定时间窗 + 随播放滚动」。
"""
from __future__ import annotations

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEventLoop, QTimer            # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

from gtiharmonica.arrange import Options, arrange        # noqa: E402
from gtiharmonica.config import Config                   # noqa: E402
from gtiharmonica.gui.theme import QSS, dark_palette     # noqa: E402
from gtiharmonica.gui.main import MainWindow             # noqa: E402
from gtiharmonica.instrument import Instrument           # noqa: E402
from gtiharmonica.score import load_score                # noqa: E402

SHOTS = os.path.join(ROOT, 'build', 'shots')


def pump(ms=300):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setPalette(dark_palette())
    app.setStyleSheet(QSS)

    lib = os.path.join(ROOT, 'songs')
    win = MainWindow(Config(), lib)
    win.resize(1360, 880)
    win.show()
    pump(800)

    longs = []
    for p in glob.glob(os.path.join(lib, '*.json')):
        try:
            with open(p, encoding='utf8') as fh:
                n = len(json.load(fh).get('notes', []))
            if n > 700:
                longs.append((n, p))
        except Exception:
            continue
    if not longs:
        print('没有找到长曲谱')
        return 1
    longs.sort(reverse=True)
    n, target = longs[0]

    score = load_score(target)
    plan = arrange(score, Instrument(), Options())
    win.roll.set_plan(plan)
    pump(300)

    print('曲目：%s' % os.path.basename(target))
    print('  音符 %d / 时长 %.1f 秒 / 可见窗 %.0f 秒'
          % (len(plan.steps), plan.duration, win.roll._span()))

    for frac, tag in ((0.0, 'start'), (0.45, 'mid')):
        pos = plan.duration * frac
        win.roll.set_position(pos, index=int(len(plan.steps) * frac))
        pump(350)
        print('  播到 %6.1fs -> 窗口起点 %6.1fs（播放头在窗口 %.0f%% 处）'
              % (pos, win.roll._view_left,
                 (pos - win.roll._view_left) / win.roll._span() * 100))
        path = os.path.join(SHOTS, 'ui_roll_%s.png' % tag)
        win.roll.grab().save(path)
        print('     shot -> %s' % path)

    win.close()
    pump(150)
    return 0


if __name__ == '__main__':
    sys.exit(main())
