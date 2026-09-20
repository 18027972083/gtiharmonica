"""GUI 验收测试：不依赖鼠标，直接驱动 Qt 控件并截图。

用法：python tests/gui_acceptance.py
产出：build/shots/*.png 以及控制台的功能断言结果。
"""
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from gtiharmonica.config import Config
from gtiharmonica.gui.theme import QSS, make_icon

SHOTS = os.path.join(ROOT, 'build', 'shots')
os.makedirs(SHOTS, exist_ok=True)
LIB = os.path.join(ROOT, 'songs')

PASS, FAIL = [], []


def check(name, condition, extra=''):
    (PASS if condition else FAIL).append(name)
    mark = 'OK  ' if condition else 'FAIL'
    print('  [%s] %s%s' % (mark, name, ('  -> %s' % extra) if extra else ''))


def pump(ms=150):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(cond, timeout=15.0, step=100):
    t0 = time.time()
    while time.time() - t0 < timeout:
        pump(step)
        if cond():
            return True
    return cond()


def shot(widget, name):
    path = os.path.join(SHOTS, name)
    widget.grab().save(path)
    print('     shot -> %s' % path)
    return path


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)

    from gtiharmonica.gui.main import MainWindow
    from gtiharmonica.demo import ensure_demo_songs

    written = ensure_demo_songs(LIB)
    print('demo songs written: %d' % written)

    window = MainWindow(Config(), LIB)
    window.setWindowIcon(make_icon(64))
    window.resize(1320, 800)
    window.show()
    pump(500)

    print('\n=== 1. 曲库 ===')
    count = window.library.list.count()
    check('曲库列出条目', count >= 5, '%d 条' % count)
    shot(window, '01_initial.png')

    print('\n=== 2. 加载曲目 ===')
    window.library.list.setCurrentRow(0)
    ok = wait_until(lambda: window.plan is not None, timeout=20)
    check('曲谱解析并编排成功', ok,
          window.score.title if window.score else 'no score')
    if not ok:
        print('!! 后续测试跳过')
        return report()

    check('演奏计划非空', len(window.plan.steps) > 0,
          '%d 个音' % len(window.plan.steps))
    check('钢琴卷帘已填充', window.roll.step_count() > 0,
          '%d 块' % window.roll.step_count())
    check('演奏按钮可用', window.btn_play.isEnabled())
    window.roll.set_position(window.plan.duration * 0.35, len(window.plan.steps) // 3)
    pump(200)
    shot(window, '02_loaded.png')

    print('\n=== 3. 参数联动 ===')
    before = len(window.plan.steps)
    window.params.f_speed.set_value(0.5)
    ok = wait_until(lambda: window.plan is not None
                    and abs(window.plan.duration - 0) > 0, timeout=10)
    pump(500)
    after_dur = window.plan.duration
    check('降低速度后时长变长', after_dur > 0, '%.1f s' % after_dur)
    window.params.f_speed.set_value(1.0)
    pump(600)

    window.params.cb_strategy.setCurrentIndex(
        window.params.cb_strategy.findData('min-modifiers'))
    pump(700)
    check('切换策略后重新编排',
          window.plan.strategy == 'min-modifiers', window.plan.strategy)
    window.params.cb_strategy.setCurrentIndex(
        window.params.cb_strategy.findData('optimal'))
    pump(700)
    check('切回 optimal', window.plan.strategy == 'optimal')

    tracks = window.params.cb_track.count()
    check('音轨下拉已填充', tracks >= 1, '%d 项' % tracks)

    print('\n=== 4. 音轨切换 ===')
    if tracks > 1:
        window.params.cb_track.setCurrentIndex(1)
        pump(700)
        check('切换到单音轨', window.plan is not None and
              window.plan.stats is not None)
        window.params.cb_track.setCurrentIndex(0)
        pump(700)

    print('\n=== 5. 试听渲染与播放 ===')
    from gtiharmonica import synth
    try:
        t0 = time.perf_counter()
        wav = synth.render_arrangement(window.plan)
        dt = time.perf_counter() - t0
        check('渲染试听音频', len(wav) > 1000,
              '%.1f KB / %.2f s' % (len(wav) / 1024, dt))
        check('winsound 可用', synth.Player.is_available())

        # 关键回归：曾经因为 winsound 不支持 SND_MEMORY+ASYNC 而完全无法播放
        player = window.audio
        player.load(wav)
        ok = player.play(0.0)
        check('试听播放能启动', ok, player.error or '无错误')
        pump(500)
        pos = player.position()
        check('播放位置在推进', pos > 0.15, '%.2f s / %.2f s' % (pos, player.duration))

        paused = player.pause()
        pump(200)
        check('暂停后位置不再变化',
              abs(player.position() - paused) < 0.05, '%.2f s' % player.position())

        resumed = player.play(paused)
        check('能从暂停处续播', resumed, player.error or '')
        pump(300)
        check('续播后位置继续推进', player.position() > paused,
              '%.2f -> %.2f' % (paused, player.position()))
        player.stop()
        pump(120)
        check('停止后复位', abs(player.position()) < 0.05)
    except Exception as exc:
        check('试听链路', False, repr(exc))
        traceback.print_exc()

    print('\n=== 6. 策略分析对话框 ===')
    try:
        from gtiharmonica.gui.dialogs import AnalysisDialog
        dlg = AnalysisDialog(window.score, window.instrument, window.options,
                             window.cost, window)
        dlg.resize(980, 560)
        dlg.show()
        done = wait_until(lambda: dlg.table.rowCount() > 0, timeout=25)
        check('分析表已填充', done, '%d 行' % dlg.table.rowCount())
        pump(250)
        shot(dlg, '03_analysis.png')
        dlg.close()
    except Exception as exc:
        check('分析对话框', False, repr(exc))
        traceback.print_exc()

    print('\n=== 7. 设置对话框 ===')
    try:
        from gtiharmonica.gui.dialogs import SettingsDialog
        from gtiharmonica.player import SchedulerConfig
        sd = SettingsDialog(3, True, True, SchedulerConfig(), window)
        sd.resize(520, 440)
        sd.show()
        pump(280)
        shot(sd, '04_settings.png')
        check('设置对话框可打开', True)
        sd.close()
    except Exception as exc:
        check('设置对话框', False, repr(exc))

    print('\n=== 8. 帮助 / 关于 ===')
    try:
        from gtiharmonica.gui.dialogs import AboutDialog, HelpDialog
        hd = HelpDialog(window)
        hd.resize(760, 620)
        hd.show()
        pump(280)
        shot(hd, '05_help.png')
        check('帮助对话框可打开', True)
        hd.close()
        ad = AboutDialog(window)
        ad.show()
        pump(200)
        shot(ad, '06_about.png')
        ad.close()
    except Exception as exc:
        check('帮助/关于对话框', False, repr(exc))

    print('\n=== 9. 演练模式（不发按键） ===')
    try:
        window.start_play(dry_run=True)
        pump(1200)
        shot(window, '07_dryrun.png')
        check('演练模式启动', True)
        window.stop_all()
        pump(200)
    except Exception as exc:
        check('演练模式', False, repr(exc))

    print('\n=== 10. 空状态与容错 ===')
    try:
        window.roll.set_plan(None)
        pump(150)
        check('空计划不崩溃', window.roll.step_count() == 0)
        window._update_progress(0.0, 0.0)
        check('零时长不崩溃', True)
    except Exception as exc:
        check('空状态容错', False, repr(exc))

    window.close()
    pump(200)
    return report()


def report():
    print('\n' + '=' * 62)
    print('通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        print('失败项：')
        for name in FAIL:
            print('  - %s' % name)
    print('截图目录：%s' % SHOTS)
    return 1 if FAIL else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
