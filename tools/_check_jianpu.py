"""「导入简谱」功能验收：核心解析/转换逻辑 + GUI 冒烟。

用法：python tools/_check_jianpu.py
"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, condition, extra=''):
    (PASS if condition else FAIL).append(name)
    print('  [%s] %s%s' % ('OK  ' if condition else 'FAIL', name,
                           ('  -> %s' % extra) if extra else ''))


SAMPLE = """.1 6_ 7_ .1 | .3 - 0 .5_ .5_ | .3 5_ .3_ .2_ .1_ .2_ 7 - 0
.1 7_ 6 - 0
# 注释行要被忽略
5 65 65 6 .3 - 0
.4_ .3_ .4 .5* 0"""


def test_logic():
    print('\n=== 1. 解析与转换（逻辑层） ===')
    from gtiharmonica.jianpu import (
        build_score, describe, parse_line, parse_text, strip_comment)

    check('音高直译：1..7 = 60..71',
          [parse_line('%d' % n)[0][0] for n in range(1, 8)]
          == [60, 62, 64, 65, 67, 69, 71])
    check('8 = 高音 do（第 8 键）', parse_line('8')[0][0] == 72)
    check('前点 = 高八度（右键区）', parse_line('.1')[0][0] == 72)
    check('后点 = 低八度（左键区）', parse_line('1.')[0][0] == 48)
    check('连写拆成逐个音', [x[0] for x in parse_line('65')] == [69, 67])

    check('默认一拍', parse_line('5')[0][1] == 1.0)
    check('增时线：5 - = 两拍', parse_line('5 -')[0][1] == 2.0)
    check('减时线：5_ = 半拍，5__ = 1/4 拍',
          parse_line('5_')[0][1] == 0.5 and parse_line('5__')[0][1] == 0.25)
    check('附点：5* = 1.5 拍', parse_line('5*')[0][1] == 1.5)
    check('休止符 0 不产生音符', all(m is None for m, _ in parse_line('0 0_')))

    check('行尾中文注释被剥离', len(parse_line('5 6 总是')) == 2)
    check('strip_comment 只在中文/全角括号处截断',
          strip_comment('.1 6') == '.1 6' and '总' not in strip_comment('5 总'))

    rows, errors = parse_text(SAMPLE)
    check('样例解析无错误', not errors, '；'.join(errors))
    check('注释行不进入 rows', all('注释' not in r[1] for r in rows))
    check('小节线被忽略', parse_line('5 | 6') == [(67, 1.0), (69, 1.0)])

    score = build_score(SAMPLE, '测试', sec=0.6, gap=0.5)
    check('转换产生音符', len(score.notes) == 28, '%d 个' % len(score.notes))
    check('音域与样例一致',
          min(n.pitch for n in score.notes) == 67
          and max(n.pitch for n in score.notes) == 79,
          '%d..%d' % (min(n.pitch for n in score.notes),
                      max(n.pitch for n in score.notes)))
    check('时值按记号缩放（半拍 = 0.3s @600ms）',
          abs(min(n.duration for n in score.notes) - 0.3) < 1e-6)
    check('每行的末音标记 phrase_end（4 行乐句）',
          sum(1 for n in score.notes if n.phrase_end) == 4,
          '%d 个' % sum(1 for n in score.notes if n.phrase_end))

    try:
        build_score('.1 5x 5 -', '坏谱')
        check('非法记号应当报错', False)
    except ValueError as exc:
        check('非法记号给出行号', '第 1 行' in str(exc), str(exc)[:40])

    try:
        build_score('- 5', '行首增时线')
        check('行首孤立增时线应当报错', False)
    except ValueError:
        check('行首孤立增时线被拒绝', True)

    text = describe(build_score(SAMPLE, '测试'), sec=0.6)
    check('describe 含音符数/音域/时长',
          all(k in text for k in ('个音符', '音域', '时长')))


def test_gui():
    print('\n=== 2. GUI 冒烟 ===')
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    from gtiharmonica.gui.theme import build_qss
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_qss())

    from gtiharmonica.gui.jianpu_dialog import JianpuDialog

    lib = tempfile.mkdtemp(prefix='gti-jianpu-')
    dlg = JianpuDialog(lib)
    dlg.show()

    def pump(ms=150):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(200)
    check('对话框构建成功', dlg.isVisible())
    check('空文本时「加入曲库」禁用', not dlg.btn_save.isEnabled())

    dlg.edit_text.setPlainText(SAMPLE)
    dlg.edit_title.setText('简谱测试')
    pump(600)                                    # 防抖 300ms + 余量
    check('粘贴后自动解析出音符', dlg.score is not None
          and len(dlg.score.notes) == 28,
          '%s' % ('None' if dlg.score is None else len(dlg.score.notes)))
    check('「加入曲库」启用', dlg.btn_save.isEnabled())

    dlg.edit_text.setPlainText('.1 5x 5')
    pump(600)
    check('非法记号显示错误且禁用保存',
          '问题' in dlg.lbl_preview.text() and not dlg.btn_save.isEnabled())

    dlg.edit_text.setPlainText(SAMPLE)
    dlg.edit_title.setText('简谱测试')
    pump(600)
    dlg._save()
    saved = dlg.saved_path
    check('「加入曲库」写出文件', saved and os.path.isfile(saved),
          os.path.basename(saved) if saved else '未保存')
    if saved:
        from gtiharmonica.score import load_json_score
        back = load_json_score(saved)
        check('保存的曲谱可重新读入且音高一致',
              [n.pitch for n in back.notes]
              == [n.pitch for n in dlg.score.notes])

    dlg.close()
    pump(100)


def test_cli():
    print('\n=== 3. 命令行工具（与 GUI 共用逻辑） ===')
    import subprocess
    tmp = tempfile.mkdtemp(prefix='gti-jianpu-')
    src = os.path.join(tmp, 'in.txt')
    out = os.path.join(tmp, 'out.json')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(SAMPLE)
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, 'tools', 'jianpu2score.py'),
         src, 'CLI测试', out],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    check('CLI 转换退出码 0', r.returncode == 0,
          (r.stderr or r.stdout)[-120:])
    check('CLI 产物存在且可读', os.path.isfile(out))
    if os.path.isfile(out):
        from gtiharmonica.score import load_json_score
        check('CLI 产物 28 个音符', len(load_json_score(out).notes) == 28)


def main():
    print('=' * 72)
    print('导入简谱 —— 功能验收')
    print('=' * 72)
    test_logic()
    try:
        test_gui()
    except Exception as exc:
        import traceback
        check('GUI 冒烟整体', False, str(exc))
        traceback.print_exc()
    test_cli()
    print()
    print('=' * 72)
    print('通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        for name in FAIL:
            print('  FAIL: %s' % name)
    print('=' * 72)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
