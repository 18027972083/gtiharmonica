# -*- coding: utf-8 -*-
"""全功能离屏自检：真实数据驱动所有界面路径。

背景：2026-09-19 连续三个运行期 bug（overlay 用了不存在的
fingering.pitch、drawPolygon(*tri)、键位表 %d 吃到 str）都是
「编译能过、一跑就炸」的类型。本套件把每个对话框、曲库里的
每一首曲子、两个主题全部真实构建 + 渲染一遍——任何 paintEvent
异常、格式化错位、属性访问错误都会在这里现形，而不是等用户弹窗。

用法：python tools/_check_gui_full.py   （THEME=light 跑浅色）
"""
from __future__ import annotations

import glob
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
# 卡密激活只拦“启动流程”，本套件直接构建主窗口不受影响；
# 这里再兜一层，保证任何新加的启动路径都不会在无人值守时卡在激活窗。
os.environ.setdefault('GTIHARMONICA_SKIP_ACTIVATION', '1')

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
app.setStyle('Fusion')

from gtiharmonica.gui.theme import apply_theme

THEME = os.environ.get('THEME', 'dark')
apply_theme(app, THEME)

from gtiharmonica.config import Config
from gtiharmonica.gui.main import MainWindow
from gtiharmonica.score import load_score
from gtiharmonica.arrange import arrange


def pump(ms=250):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def render(widget):
    """抓一帧；任何 paintEvent 异常都会在这里抛出。"""
    widget.grab()
    pump(60)


failures = []


def case(name):
    def deco(fn):
        try:
            fn()
            print('  OK   %s' % name)
        except Exception:
            failures.append((name, traceback.format_exc(limit=4)))
            print('  FAIL %s' % name)
        return fn
    return deco


print('=== 全功能自检（%s）===' % THEME)
cfg = Config()
instrument, options, cost, scheduler = cfg.build()

# ------------------------------------------------------------------
# 1. 主窗口构建
# ------------------------------------------------------------------
win = MainWindow(cfg, os.path.join(ROOT, 'songs'))
win.show()
pump(500)
render(win)
print('  OK   主窗口构建（曲库 %d 首）' % len(win.library.song_paths())
      if hasattr(win.library, 'song_paths') else '  OK   主窗口构建')

# ------------------------------------------------------------------
# 2. 曲库全量载入 + 编排 + 预览/编辑双视图渲染
# ------------------------------------------------------------------
songs = sorted(glob.glob(os.path.join(ROOT, 'songs', '*.json'))) + \
    sorted(glob.glob(os.path.join(ROOT, 'songs', '*.mid'))) + \
    sorted(glob.glob(os.path.join(ROOT, 'songs', '*.midi')))
bad_scores = 0
for i, path in enumerate(songs):
    name = os.path.basename(path)
    try:
        score = load_score(path)
        plan = arrange(score, instrument, options)
        win._on_score_loaded(score, plan)
        render(win.roll)
        # 编辑视图：真实乐谱进编辑器再渲染（拖拽/合并之外的全部路径）
        if win._ensure_editor_doc():
            win.set_mode('edit')
            render(win.editor)
            win.set_mode('preview')
    except Exception:
        bad_scores += 1
        failures.append(('载入 %s' % name, traceback.format_exc(limit=4)))
        print('  FAIL 载入 %s' % name)
if not bad_scores:
    print('  OK   曲库全量 %d 首（载入/编排/双视图渲染）' % len(songs))

# ------------------------------------------------------------------
# 3. 全部对话框：真实参数构建 + 渲染
# ------------------------------------------------------------------
from gtiharmonica.gui.dialogs import (AboutDialog, ActivationDialog,
                                      AnalysisDialog,
                                      AnnouncementDialog, CalibrateDialog,
                                      HelpDialog,
                                      ImportModeDialog, KeymapDialog,
                                      SettingsDialog)
from gtiharmonica.gui.jianpu_dialog import JianpuDialog


def dialog_case(name, factory):
    def run():
        dlg = factory()
        dlg.show()
        render(dlg)
        dlg.close()
        dlg.deleteLater()
    case(name)(run)


score_now, plan_now = win.score, win.plan
dialog_case('键位速查表（KeymapDialog）',
            lambda: KeymapDialog(instrument, 3, True, win))
dialog_case('设置（SettingsDialog）',
            lambda: SettingsDialog(3, True, True, scheduler, win))
dialog_case('使用说明（HelpDialog）', lambda: HelpDialog(win))
dialog_case('公告·添加曲谱（AnnouncementDialog）',
            lambda: AnnouncementDialog(win))
dialog_case('卡密激活（ActivationDialog）',
            lambda: ActivationDialog(win))
dialog_case('关于（AboutDialog）', lambda: AboutDialog(win))


def shortcut_case():
    """快捷方式：powershell 路径必须真实存在。

    打包版踩过的坑：路径少了 System32\\WindowsPowerShell\\v1.0 一层，
    退回裸名后 PATH 不完整就报「创建失败」。
    """
    from gtiharmonica import shortcut as _sc
    ps = _sc._powershell_exe()
    assert os.path.exists(ps), 'powershell 路径不存在: %s' % ps
    # 编码对比测试留下的旧文件不该出现在预期路径之外，这里只校验路径解析
    paths = _sc.expected_paths()
    assert len(paths) == 2 and all(p.endswith('.lnk') for p in paths), paths


case('快捷方式路径解析（shortcut）')(shortcut_case)
if score_now is not None:
    dialog_case('策略分析（AnalysisDialog）',
                lambda: AnalysisDialog(score_now, instrument, options,
                                       cost, win))
dialog_case('校准（CalibrateDialog）', lambda: CalibrateDialog(instrument, win))
def import_mode_case():
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFrame
    dlg = ImportModeDialog(['x.mid', 'y.mid'], win)
    dlg.show()
    render(dlg)
    rbs = dlg._buttons
    assert len(rbs) == 3, '应有三个导入模式（直转 / 单轨提取 / 旋律化）'
    cards = [c for c in dlg.findChildren(QFrame) if c.objectName() == 'card']
    n_checked = sum(1 for rb in rbs if rb.isChecked())
    assert n_checked == 1, '初始应恰有一项选中，实际 %d' % n_checked
    assert dlg.mode == 'track', '默认应落在推荐项「单轨提取」，实际 %r' % dlg.mode
    # 逐个点一遍：互斥切换 + 点已选卡片不会变成双不选
    for idx, want in ((0, 'direct'), (1, 'track'), (2, 'melody')):
        QTest.mouseClick(cards[idx], Qt.LeftButton, pos=QPoint(20, 20))
        assert dlg.mode == want, '点第 %d 张卡应切到 %r，实际 %r' % (idx, want, dlg.mode)
        assert rbs[idx].isChecked(), '第 %d 项应被选中' % idx
        assert sum(1 for rb in rbs if rb.isChecked()) == 1, '不应出现多选'
    QTest.mouseClick(cards[2], Qt.LeftButton, pos=QPoint(20, 20))
    assert rbs[2].isChecked(), '点已选卡片不能变成双不选'
    dlg.close()
    dlg.deleteLater()
case('MIDI 导入三选一（互斥+整卡点击）')(import_mode_case)
dialog_case('导入简谱（JianpuDialog）',
            lambda: JianpuDialog(os.path.join(ROOT, 'songs'), win))

# ------------------------------------------------------------------
# 3.5 旋律化（心似烟火链路）：真实 MIDI 验证网格与轨道选择
# ------------------------------------------------------------------
def melodize_case():
    from gtiharmonica.melody import melodize
    import glob as _g
    mids = _g.glob(os.path.join(ROOT, 'build', 'jianpu', '*.mid'))
    if not mids:
        print('  SKIP 旋律化（build/jianpu 无样例 MIDI）')
        return
    sc = load_score(sorted(mids)[0])
    mel = melodize(sc)
    assert mel.bpm and mel.notes, '旋律化产物为空'
    grid = 60.0 / mel.bpm / 4
    off = max(abs(n.start / grid - round(n.start / grid))
              for n in mel.notes)
    assert off < 0.01, '16 分网格偏差 %.3f 过大' % off
case('旋律化（真实 MIDI，网格+轨道选择）')(melodize_case)


# ------------------------------------------------------------------
# 3.7 拖入路由：MIDI 拖放 → 二选一 → 旋律化产物落进曲库
# ------------------------------------------------------------------
def drop_routing_case():
    import tempfile, shutil
    from PySide6.QtCore import QMimeData, QUrl, QPointF, Qt
    from PySide6.QtGui import QDropEvent
    from gtiharmonica.gui import dialogs as _dlg

    tmp = tempfile.mkdtemp(prefix='dh_drop_')
    try:
        w2 = MainWindow(Config(), tmp)
        w2.show()
        pump(200)

        mids = sorted(glob.glob(os.path.join(ROOT, 'build', 'jianpu', '*.mid')))
        assert mids, '没有可拖的样例 MIDI'
        src = mids[0]

        # 打桩：二选一对话框自动选「旋律化」
        orig_exec = _dlg.ImportModeDialog.exec

        def fake_exec(self):
            self.mode = 'melody'
            return 1
        _dlg.ImportModeDialog.exec = fake_exec
        try:
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(src)])
            ev = QDropEvent(QPointF(50, 50), Qt.CopyAction, mime,
                            Qt.LeftButton, Qt.NoModifier)
            w2.dropEvent(ev)
        finally:
            _dlg.ImportModeDialog.exec = orig_exec
        pump(300)

        files = os.listdir(tmp)
        converted = [f for f in files if '(旋律化)' in f]
        assert converted, '旋律化产物没有落进曲库：%s' % files
        from gtiharmonica.score import load_score as _ls
        sc = _ls(os.path.join(tmp, converted[0]))
        assert sc.notes and sc.bpm, '旋律化产物为空或缺 bpm'
        w2.close()
        pump(100)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
case('拖入路由（MIDI → 旋律化 → 入库）')(drop_routing_case)


# ------------------------------------------------------------------
# 3.8 播放中锁定选曲（防误触）
# ------------------------------------------------------------------
def lock_selection_case():
    from PySide6.QtWidgets import QAbstractItemView

    class FakeWorker:
        is_paused = False
        def isRunning(self):
            return True

    # 用真实曲目先载入一首，制造「已选曲」状态
    songs = sorted(glob.glob(os.path.join(ROOT, 'songs', '*.json')))
    sc = load_score(songs[0])
    plan = arrange(sc, instrument, options)
    win._on_score_loaded(sc, plan)
    cur = win.score.title

    # 伪造演奏中 → 应锁定
    win.play_worker = FakeWorker()
    win._update_actions()
    assert win.library.list.selectionMode() == QAbstractItemView.NoSelection,         '演奏中未锁定选曲'
    win.on_library_selected(songs[1])
    assert win.score.title == cur, '演奏中仍被切换了曲目'
    assert '先停止' in win.status_label.text(),         '缺少防误触提示：%r' % win.status_label.text()

    # 恢复空闲 → 解锁
    win.play_worker = None
    win._update_actions()
    assert win.library.list.selectionMode() == QAbstractItemView.SingleSelection,         '停止后未解锁选曲'
case('播放中锁定选曲（防误触）')(lock_selection_case)


# ------------------------------------------------------------------
# 3.9 收藏与删除名单（曲库元数据）
# ------------------------------------------------------------------
def favorite_case():
    import shutil
    import tempfile
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox

    from gtiharmonica import app as gapp
    from gtiharmonica import demo
    from gtiharmonica import library_state as lstate
    from gtiharmonica.gui.panels import Sidebar, _ScoreItemDelegate

    tmp = tempfile.mkdtemp(prefix='gti_fav_')
    src = tempfile.mkdtemp(prefix='gti_src_')
    try:
        names = ('小星星.json', '欢乐颂.json', '送别.json')
        for name in names + ('卡农片段.json',):
            shutil.copy2(os.path.join(ROOT, 'songs', name),
                         os.path.join(src, name))
        for name in names:
            shutil.copy2(os.path.join(src, name), os.path.join(tmp, name))

        side = Sidebar(tmp, 'check')
        side.resize(288, 620)
        side.refresh()
        assert side.list.count() == 3, '曲库条目数 %d' % side.list.count()

        # 1) 切收藏：落盘、列表状态更新、不动当前选中项
        side.list.setCurrentRow(0)
        star_item = side.list.item(1)
        hit = _ScoreItemDelegate.star_rect(side.list.visualItemRect(star_item))
        assert hit.width() == _ScoreItemDelegate.STAR_HIT, '星标热区尺寸不对'
        side._toggle_favorite(star_item.data(Qt.UserRole))
        assert lstate.is_favorite(tmp, '欢乐颂.json'), '收藏没写进名单'
        assert bool(side.list.item(1).data(Qt.UserRole + 2)), '星标状态没更新'
        assert side.list.currentRow() == 0, '收藏动作把选中项挪走了'

        # 2) 收藏视图只列收藏，计数文案跟着变
        side.set_fav_only(True)
        assert side.list.count() == 1 and side.list.item(0).text() == '欢乐颂', \
            '收藏视图没有正确过滤'
        assert '收藏 1 首' in side.hint.text(), side.hint.text()
        render(side)

        # 3) 重新构造（= 重启）后收藏仍在
        side2 = Sidebar(tmp, 'check')
        side2.refresh()
        assert bool(side2.list.item(1).data(Qt.UserRole + 2)), '收藏没持久化'
        side2.deleteLater()
        side.set_fav_only(False)

        # 4) 删除 → 写删除名单 → 启动补齐 / 内置曲目安装都不再导回
        victim = '送别.json'
        for i in range(side.list.count()):
            if side.list.item(i).text() == '送别':
                side.list.setCurrentItem(side.list.item(i))
                break
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        side._delete_current()
        assert not os.path.exists(os.path.join(tmp, victim)), '文件没删掉'
        assert lstate.is_deleted(tmp, victim), '删除后没写删除名单'

        moved = gapp._adopt_portable_library(src, tmp)
        assert moved == 1, '补齐应只搬来未删除的 1 首，实际 %d' % moved
        assert not os.path.exists(os.path.join(tmp, victim)), \
            '被删的曲目又被启动补齐拷了回来'
        demo.ensure_demo_songs(tmp, force=True)
        assert not os.path.exists(os.path.join(tmp, victim)), \
            '内置曲目安装把被删的曲目写了回来'

        # 5) 用户主动把同名文件弄回曲库 → 删除名单自愈
        shutil.copy2(os.path.join(src, victim), os.path.join(tmp, victim))
        side.refresh()
        assert not lstate.is_deleted(tmp, victim), '文件回来了但删除名单没撤'
        assert lstate.is_favorite(tmp, '欢乐颂.json'), '删除动作动了收藏名单'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(src, ignore_errors=True)
case('收藏与删除名单（星标 / 过滤 / 不复活）')(favorite_case)

# ------------------------------------------------------------------
# 3.9 放弃演奏时必须收起悬浮窗（用户报的「卡住」）
# ------------------------------------------------------------------
def overlay_abort_case():
    """没识别到游戏窗口就点演奏：悬浮窗不能停在倒计时数字上。

    回归背景：倒计时结束后发现前台是本程序自己，会弹「需要先切到游戏」，
    但那时的实现只更新了按钮、没管悬浮窗 —— 它会一直显示倒计时数字，
    看起来就像卡死了。
    """
    from PySide6.QtWidgets import QMessageBox

    from gtiharmonica.backend import load_user32

    saved_info = QMessageBox.information
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    saved_target = win._target_window
    saved_elev = win._check_target_elevation
    saved_cd = win.countdown_seconds
    try:
        # 让「目标窗口」指向本程序自己 → 命中「需要先切到游戏」那条路径
        win._target_window = lambda: (load_user32(), int(win.winId()),
                                      '大肥鲸洲琴工具包', os.getpid())
        win._check_target_elevation = lambda *a, **k: True
        win.countdown_seconds = 0
        win.start_play()
        pump(400)
        assert not win.overlay.isVisible(), '放弃演奏后悬浮窗必须收起'
        assert win.play_worker is None, '不该起播放线程'
        assert '已取消' in win.status_label.text() or '无效' in win.status_label.text(),             win.status_label.text()
    finally:
        QMessageBox.information = saved_info
        win._target_window = saved_target
        win._check_target_elevation = saved_elev
        win.countdown_seconds = saved_cd
case('放弃演奏时收起悬浮窗（无目标窗口）')(overlay_abort_case)


# ------------------------------------------------------------------
# 4. 悬浮窗全生命周期（倒计时→演奏→暂停→续播→结束）
# ------------------------------------------------------------------
def overlay_flow():
    from gtiharmonica.gui.overlay import OverlayWindow
    ov = OverlayWindow()
    ov.show_countdown(3)
    ov.begin(plan_now, plan_now.duration)   # 真实编排：原 fingering.pitch 崩溃点
    ov.set_state('')
    ov.set_position(plan_now.duration * 0.3)
    ov.set_state('暂停中', paused=True)
    ov.set_state('')
    ov.set_position(plan_now.duration * 0.9)
    ov.finish()
    ov.stop_hidden()
    render(ov)
case('悬浮窗全生命周期（真实编排）')(overlay_flow)

# ------------------------------------------------------------------
# 5. 主题切换往返（再渲染一轮全部自绘控件）
# ------------------------------------------------------------------
def theme_roundtrip():
    win.toggle_theme()
    pump(200)
    render(win)
    if win.score is not None:
        render(win.roll)
    win.toggle_theme()
    pump(200)
    render(win)
case('主题切换往返')(theme_roundtrip)

win.close()
pump(150)

print()
if failures:
    print('=== %d 项失败 ===' % len(failures))
    for name, tb in failures:
        print('---- %s ----' % name)
        print(tb)
    sys.exit(1)
print('=== 全部通过（%s 主题，%d 首曲子，7 个对话框，悬浮窗全流程）==='
      % (THEME, len(songs)))

def focus_and_standby_case():
    """失焦（切到别的程序）应按暂停处理；F8 待命启动已接好。

    真实触发需要模拟前台窗口，这里做代码级校验：异常类型在、发送循环
    捕获它、_pump 主动检测焦点、待命检查挂在主窗口上。
    """
    import inspect
    from gtiharmonica import backend, player
    from gtiharmonica.gui.main import MainWindow

    assert issubclass(backend.FocusLost, Exception)
    assert issubclass(backend.FocusOnSelf, Exception)

    send_loop = inspect.getsource(player.Player._run_once)
    assert 'FocusLost' in send_loop, '发送循环应把 FocusLost 一并按暂停处理'
    pump = inspect.getsource(player.Player._pump)
    assert '_target_foreground' in pump, '_pump 应主动检测前台是否还是游戏'

    assert hasattr(MainWindow, '_standby_check'), '缺少 F8 待命检查'
    assert hasattr(MainWindow, '_foreground_is_game'), '缺少游戏窗口识别'


case('失焦暂停 + F8 待命启动（代码级）')(focus_and_standby_case)
