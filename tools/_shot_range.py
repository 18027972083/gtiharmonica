# -*- coding: utf-8 -*-
import ctypes, ctypes.wintypes, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtWidgets import QApplication
from gtiharmonica.config import Config
from gtiharmonica.gui.theme import QSS, dark_palette, make_icon
from gtiharmonica.gui.main import MainWindow
from gtiharmonica.score import load_score
from gtiharmonica.arrange import arrange
from PySide6.QtTest import QTest

app = QApplication(sys.argv)
app.setStyle('Fusion'); app.setPalette(dark_palette()); app.setStyleSheet(QSS)
app.setWindowIcon(make_icon(256))
win = MainWindow(Config(), os.path.join(ROOT, 'songs'))
win.show(); win.resize(1420, 900)
state = {'step': 0}

def grab(tag):
    from PIL import ImageGrab
    app.processEvents()
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(int(win.winId()), ctypes.byref(r))
    ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom)).save(
        os.path.join(ROOT, 'build', 'logo', 'ui_%s.png' % tag))
    print('saved', tag)

def step():
    state['step'] += 1
    s = state['step']
    if s == 1:
        for name in sorted(os.listdir(win.library_dir)):
            if not name.lower().endswith(('.mid', '.midi', '.json')):
                continue
            try:
                score = load_score(os.path.join(win.library_dir, name))
                plan = arrange(score, win.instrument, win.options)
            except Exception:
                continue
            if plan.duration > 40:
                break
        win.score = score; win.plan = plan
        win.roll.set_plan(plan); win._refresh_info(score)
        win._ensure_editor_doc()
        win.set_mode('edit')
        QTimer.singleShot(500, step)
    elif s == 2:
        ed = win.editor.editor
        ed.set_zoom(0.5)
        win.editor.toolbar.btn_range.click()
        QTest.mouseClick(ed, Qt.LeftButton,
                         pos=QPoint(int(ed.width()*0.3), int(ed.height()-8)))
        # 挪鼠标模拟「正在选终点」的悬停预览
        c = ed.mapFromGlobal(QPoint(int(ed.width()*0.72), int(ed.height()*0.5)))
        QTest.mouseMove(ed, c)
        QTimer.singleShot(400, step)
    elif s == 3:
        grab('range')
        app.quit()

QTimer.singleShot(900, step)
app.exec()
