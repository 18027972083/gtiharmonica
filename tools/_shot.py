# -*- coding: utf-8 -*-
"""截图自检：加载曲库第一首，分别截预览/编辑两个视图。"""
import ctypes
import ctypes.wintypes
import os
# 公告框是模态的：自检没人点按钮，必须显式关掉（否则 exec() 永久阻塞）
os.environ.setdefault('GTIHARMONICA_NO_ANNOUNCE', '1')
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication

from gtiharmonica.config import Config
from gtiharmonica.gui.theme import apply_theme, make_icon
from gtiharmonica.gui.main import MainWindow
from gtiharmonica.score import load_score
from gtiharmonica.arrange import arrange

app = QApplication(sys.argv)
app.setStyle('Fusion')
import os as _os
THEME = _os.environ.get('THEME', 'dark')
apply_theme(app, THEME)
app.setWindowIcon(make_icon(256))

win = MainWindow(Config(), os.path.join(ROOT, 'songs'))
win.resize(1420, 900)   # 先设尺寸再 show：showEvent 会把窗口夹回工作区
win.show()

state = {'step': 0}

def load_song():
    for name in sorted(os.listdir(win.library_dir)):
        if not name.lower().endswith(('.mid', '.midi', '.json')):
            continue
        try:
            score = load_score(os.path.join(win.library_dir, name))
            plan = arrange(score, win.instrument, win.options)
        except Exception:
            continue
        if plan.duration > 12:
            break
    win.score = score
    win.score_path = os.path.join(win.library_dir, name)
    win.plan = plan
    win.roll.set_plan(plan)
    win.params.set_tracks(score, win.instrument, None)
    win._refresh_info(score)
    win._update_progress(0.0, plan.duration)
    win._ensure_editor_doc()
    win._update_actions()
    # 播到中段，让播放头出现在画面里
    mid = plan.duration * 0.42
    win.roll.set_position(mid, -1)
    win.editor.set_position(mid)
    win._update_progress(mid, plan.duration)

def grab(tag):
    from PIL import ImageGrab
    # 提到最前再抓：桌面上常有别的置顶窗（桌面歌词、助手面板），
    # 不置顶的话截下来的是别人的界面
    win.setWindowFlags(win.windowFlags() | Qt.WindowStaysOnTopHint)
    win.show()
    win.activateWindow()
    # setWindowFlags 会销毁重建原生窗口，DWM 标题栏属性随之丢失，
    # showEvent 的时机也不可靠 —— 直接再设一遍
    win._apply_titlebar_theme()
    loop = QEventLoop()
    QTimer.singleShot(500, loop.quit)
    loop.exec()
    app.processEvents()
    hwnd = int(win.winId())
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
    out = os.path.join(ROOT, 'build', 'logo', 'ui_%s.png' % tag)
    img.save(out)
    print('saved', out)

def step():
    state['step'] += 1
    if state['step'] == 1:
        load_song()
        QTimer.singleShot(600, step)
    elif state['step'] == 2:
        grab('preview')
        win.set_mode('edit')
        QTimer.singleShot(700, step)
    elif state['step'] == 3:
        grab('edit')
        app.quit()

QTimer.singleShot(900, step)
app.exec()
print('done')
