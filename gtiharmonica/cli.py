"""命令行入口。

    gtiharmonica play     song.mid          演奏
    gtiharmonica preview  song.mid          本机试听（不动游戏）
    gtiharmonica analyze  song.mid          策略对比，输出指标表
    gtiharmonica tracks   song.mid          查看音轨，挑主旋律
    gtiharmonica calibrate                  校准游戏响应
    gtiharmonica selftest                   指法表自检
    gtiharmonica windows                    列出可见窗口
    gtiharmonica init                       生成默认配置
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import List, Optional

from . import __version__
from .arrange import Arrangement, Options, arrange
from .backend import (DryRunBackend, PreviewBackend, SendInputBackend,
                      foreground_window, list_windows, load_user32)
from .config import DEFAULT_FILENAME, Config, find_config
from .fingering import METRIC_HEADERS, STRATEGIES, total_cost
from .instrument import STEPS, Instrument, note_name
from .player import Player, State
from .score import load_score, save_json_score


# ---------------------------------------------------------------------------
# 输出工具
# ---------------------------------------------------------------------------

def setup_console() -> None:
    """让 Windows 控制台正确输出 UTF-8 中文。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def hr(title: str = '') -> None:
    if title:
        print('\n' + title)
        print('-' * max(len(title) * 2, 60))
    else:
        print('-' * 60)


def print_table(headers: List[str], rows: List[List[str]], indent: str = '  ') -> None:
    if not rows:
        return
    ncols = max([len(headers)] + [len(r) for r in rows])
    widths = [0] * ncols
    for i, h in enumerate(headers):
        widths[i] = _display_width(h)
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _display_width(cell))

    def render(cells: List[str]) -> str:
        cells = list(cells) + [''] * (ncols - len(cells))
        return indent + '  '.join(
            c + ' ' * max(0, widths[i] - _display_width(c))
            for i, c in enumerate(cells))

    print(render(headers))
    print(indent + '  '.join('-' * w for w in widths))
    for row in rows:
        print(render(row))


def _display_width(text: str) -> int:
    """近似显示宽度：CJK 字符占 2 列。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)


# ---------------------------------------------------------------------------
# 公共
# ---------------------------------------------------------------------------

def build_all(args) -> tuple:
    cfg = find_config(getattr(args, 'config', None))
    instrument, options, cost, scheduler = cfg.build()
    _override_options(options, args)
    return cfg, instrument, options, cost, scheduler


def _override_options(options: Options, args) -> None:
    for name in ('speed', 'transpose', 'gate', 'breath_ms', 'track',
                 'strategy', 'chord_policy', 'fold_prefer'):
        value = getattr(args, name, None)
        if value is not None:
            setattr(options, name, value)
    if getattr(args, 'no_fold', False):
        options.fold_octaves = False


def resolve_target(args, api) -> int:
    """确定演奏目标窗口。"""
    if getattr(args, 'hwnd', None):
        return int(args.hwnd)
    hwnd, title, pid = foreground_window(api)
    if not title:
        raise SystemExit('当前没有有效的前台窗口，请先切到游戏，或用 --hwnd 指定')
    print('目标窗口：%r  (hwnd=%d, pid=%d)' % (title, hwnd, pid))
    return hwnd


def countdown(seconds: int) -> None:
    if seconds <= 0:
        return
    print('\n请在 %d 秒内切到游戏的口琴演奏界面...' % seconds)
    for i in range(seconds, 0, -1):
        print('  %d' % i, flush=True)
        time.sleep(1)


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------

def cmd_play(args) -> int:
    cfg, instrument, options, cost, scheduler = build_all(args)
    score = load_score(args.file)
    plan = arrange(score, instrument, options)
    print(plan.summary())

    api = load_user32()

    if args.dry_run:
        backend = DryRunBackend(verbose=True)
    else:
        hwnd = resolve_target(args, api)
        backend = SendInputBackend(hwnd, keep_focus=not args.no_focus,
                                   verbose=args.verbose)
        countdown(args.countdown)
        hwnd2, title2, _ = foreground_window(api)
        if hwnd2 != hwnd:
            print('注意：前台窗口已变为 %r' % (title2 or '未知'))
            print('      将按当前前台窗口演奏；失焦会自动停止。')

    player = Player(plan, backend, scheduler, hotkeys=not args.no_hotkeys)
    print('\nF8 暂停/继续   F9 或 Esc 停止\n')

    state = player.run(countdown=0)

    hr()
    print('状态：%s' % state.value)
    if player.error:
        print('错误：%s' % player.error)
    report = player.timing_report()
    if report.get('samples'):
        print('时序：平均 %+.2f ms   p95 %+.2f ms   最大 %+.2f ms   (%d 个音)'
              % (report['mean_ms'], report['p95_ms'], report['max_ms'],
                 report['samples']))
    if isinstance(backend, SendInputBackend):
        st = backend.stats()
        print('注入：%d 次调用，平均 %.0f us，失败 %d 次'
              % (st['send_calls'], st['avg_us'], st['errors']))
    return 0 if state in (State.FINISHED, State.STOPPED) else 1


def cmd_preview(args) -> int:
    cfg, instrument, options, cost, scheduler = build_all(args)
    score = load_score(args.file)
    plan = arrange(score, instrument, options)
    print(plan.summary())
    backend = PreviewBackend(verbose=args.verbose)
    if backend.fallback is not None:
        print('（没有可用的 MIDI 输出设备，仅打印序列）')
    player = Player(plan, backend, scheduler, hotkeys=False)
    state = player.run()
    print('\n状态：%s' % state.value)
    return 0


def cmd_analyze(args) -> int:
    """对每个策略 / 每个音轨输出指标，用于挑选最佳指法方案。"""
    cfg, instrument, options, cost, scheduler = build_all(args)
    score = load_score(args.file)
    table = instrument.fingerings()

    hr('曲目：%s' % score.title)
    print('  音符 %d 个   时长 %.1f 秒   音轨 %s'
          % (len(score), score.duration, score.tracks() or '无'))
    print('  乐器音域 MIDI %d..%d (%s..%s)'
          % (*instrument.playable_range(),
             note_name(instrument.playable_range()[0]),
             note_name(instrument.playable_range()[1])))

    if args.verbose:
        hr('音轨明细')
        rows = [[str(t['track']), str(t['notes']),
                 '%d..%d' % (t['pitch_min'], t['pitch_max']),
                 '%s..%s' % (note_name(t['pitch_min']), note_name(t['pitch_max'])),
                 '%.1f' % t['duration']] for t in score.track_summary()]
        print_table(['音轨', '音符数', '音高范围', '音名', '时长(s)'], rows)

    track_ids = score.tracks()
    if args.track is not None:
        targets: List[Optional[int]] = [args.track]
    elif len(track_ids) > 1:
        targets = [None] + track_ids          # 先看合并效果，再逐轨看
    else:
        targets = list(track_ids)             # 单轨时无需重复输出

    for track in targets:
        label = '全部音轨（合并为单旋律）' if track is None else '音轨 %d' % track
        hr('%s' % label)
        rows = []
        costs = {}
        best_switch = None
        best_cost = None
        for name in STRATEGIES:
            trial = Options(**{**_options_as_dict(options), 'strategy': name,
                               'track': track})
            try:
                plan = arrange(score, instrument, trial)
            except ValueError as exc:
                print('  %s：%s' % (name, exc))
                continue
            m = plan.metrics
            plan_cost = total_cost([s.fingering for s in plan.steps], cost)
            costs[name] = plan_cost
            rows.append([name] + m.as_row()
                        + ['%.1f' % plan_cost, '%.1f' % plan.duration])
            key = (m.modifier_switches, m.total_presses)
            if best_switch is None or key < best_switch[0]:
                best_switch = (key, name, m)
            if best_cost is None or plan_cost < best_cost[0] - 1e-9:
                best_cost = (plan_cost, name)
        print_table(['策略'] + METRIC_HEADERS + ['成本', '时长(s)'], rows)

        if best_switch:
            print('  → 修饰键切换最少：%s（%d 次）'
                  % (best_switch[1], best_switch[2].modifier_switches))

        opt = costs.get('optimal')
        if best_cost and opt is not None:
            if best_cost[1] == 'optimal':
                print('  → 成本最低：optimal（%.1f）全局最优' % opt)
            elif abs(best_cost[0] - opt) < 1e-9:
                print('  → 成本最低：%s 与 optimal 并列（%.1f）'
                      % (best_cost[1], opt))
            else:
                print('  → 成本最低：%s（%.1f），optimal 为 %.1f'
                      % (best_cost[1], best_cost[0], opt))
                if best_cost[0] < opt - 1e-9:
                    print('  ! 有策略成本低于 optimal，DP 实现可能有误')
    return 0


def _options_as_dict(options: Options) -> dict:
    from dataclasses import asdict
    data = asdict(options)
    data.pop('cost', None)
    return data


def cmd_tracks(args) -> int:
    _, instrument, options, cost, scheduler = build_all(args)
    score = load_score(args.file)
    hr('曲目：%s   共 %d 个音轨' % (score.title, len(score.tracks())))
    rows = []
    for t in score.track_summary():
        playable = sum(1 for n in score.track_notes(t['track'])
                       if instrument.is_playable(n.pitch + options.transpose))
        rows.append([str(t['track']), str(t['notes']),
                     '%s..%s' % (note_name(t['pitch_min']), note_name(t['pitch_max'])),
                     '%d' % playable,
                     '%.0f%%' % (100.0 * playable / max(t['notes'], 1)),
                     '%.1f' % t['duration']])
    print_table(['音轨', '音符数', '音名范围', '可直接演奏', '覆盖率', '时长(s)'], rows)
    print('\n建议：覆盖率最高、音域最集中的音轨通常就是主旋律。')
    return 0


def cmd_selftest(args) -> int:
    cfg = find_config(getattr(args, 'config', None))
    instrument = Instrument.from_config(cfg.instrument)
    table = instrument.fingerings()
    lo, hi = instrument.playable_range()

    hr('乐器配置')
    for key, value in instrument.export_config().items():
        print('  %-16s %s' % (key, value))

    hr('可用音域')
    print('  %d 个音高，MIDI %d..%d (%s..%s)'
          % (len(table), lo, hi, note_name(lo), note_name(hi)))

    hr('音阶键')
    rows = [[k, str(s), str(instrument.base + s), note_name(instrument.base + s)]
            for k, s in zip(instrument.keys, STEPS)]
    print_table(['键', '半音偏移', 'MIDI', '音名'], rows)

    hr('修饰键组合样例')
    rows = []
    for pitch in sorted(table):
        if pitch in (lo, lo + 1, instrument.base, instrument.base + 1, hi):
            fings = table[pitch]
            rows.append([note_name(pitch), str(pitch),
                         fings[0].describe(),
                         '%d 种候选' % len(fings)])
    print_table(['音名', 'MIDI', '首选指法', '候选数'], rows)
    return 0


def cmd_calibrate(args) -> int:
    from .calibrate import full_calibration
    api = load_user32()
    hwnd = resolve_target(args, api)
    cfg = find_config(getattr(args, 'config', None))
    instrument = Instrument.from_config(cfg.instrument)

    cal = full_calibration(hwnd, instrument, interactive=not args.quick)
    hr('校准结论')
    print('  SendInput 平均 %.0f us (p95 %.0f us)'
          % (cal.sendinput_us, cal.sendinput_p95_us))
    print('  最短可识别音长 %.1f ms' % (cal.min_note * 1000))
    print('  接受按键 %d 项 / 拒绝 %d 项'
          % (len(cal.accepted_keys), len(cal.rejected_keys)))

    rec = cal.recommended_options()
    hr('推荐参数')
    for k, v in rec.items():
        print('  %-12s %s' % (k, v))

    if args.save:
        cal.save(args.save)
        print('\n已保存到 %s' % args.save)
        cfg.options.update(rec)
        cfg.save(args.config or DEFAULT_FILENAME)
        print('已写入配置：%s' % (args.config or DEFAULT_FILENAME))
    return 0


def cmd_windows(args) -> int:
    api = load_user32()
    hr('可见顶层窗口')
    rows = []
    for hwnd, title, pid in list_windows(api):
        if args.filter and args.filter.lower() not in title.lower():
            continue
        rows.append([str(hwnd), str(pid), title[:60]])
    print_table(['hwnd', 'pid', '标题'], rows)
    print('\n用 --hwnd <句柄> 直接指定演奏目标。')
    return 0


def cmd_import_legacy(args) -> int:
    """批量导入旧版曲库。

    会把曲谱规范化为本工具格式，并**保留 phrase_end 换气标记**，
    同时报告每首曲子在本乐器音域内的可演奏覆盖率。
    """
    from .app import resolve_library_dir
    from .score import load_score, save_json_score

    src = args.source
    if not os.path.isdir(src):
        print('错误：目录不存在 %s' % src, file=sys.stderr)
        return 2

    cfg, instrument, options, cost, scheduler = build_all(args)
    dst = args.to or resolve_library_dir()
    os.makedirs(dst, exist_ok=True)

    names = sorted(n for n in os.listdir(src)
                   if n.lower().endswith(('.json', '.mid', '.midi')))
    if not names:
        print('目录里没有找到曲谱文件：%s' % src)
        return 1

    hr('导入 %d 首曲谱：%s → %s' % (len(names), src, dst))
    rows, ok, failed = [], 0, 0
    for name in names:
        try:
            score = load_score(os.path.join(src, name))
        except Exception as exc:
            failed += 1
            rows.append([name[:26], '读取失败', '-', '-', str(exc)[:26]])
            continue
        target = os.path.join(dst, os.path.splitext(name)[0] + '.json')
        try:
            save_json_score(score, target)
        except Exception as exc:
            failed += 1
            rows.append([name[:26], '写入失败', '-', '-', str(exc)[:26]])
            continue
        ok += 1
        playable = sum(1 for n in score.notes
                       if instrument.is_playable(n.pitch + options.transpose))
        covered = 100.0 * playable / max(len(score.notes), 1)
        note = '' if covered >= 98 else ('超域 %d' % (len(score.notes) - playable))
        rows.append([score.title[:26], '%d' % len(score.notes),
                     '%.0fs' % score.duration, '%.0f%%' % covered, note])

    print_table(['曲名', '音符', '时长', '可演奏', '备注'], rows)
    print('\n成功 %d 首，失败 %d 首' % (ok, failed))
    print('目标目录：%s' % dst)
    if ok:
        print('\n提示：这些多为受版权保护的流行曲编曲，请仅在本机个人使用，'
              '\n      不要打包进再分发的软件包里。')
    return 0


def cmd_init(args) -> int:
    path = args.config or DEFAULT_FILENAME
    if os.path.exists(path) and not args.force:
        print('%s 已存在（加 --force 覆盖）' % path)
        return 1
    cfg = Config()
    if os.path.exists('instrument.json'):
        try:
            import json
            with open('instrument.json', encoding='utf8') as fh:
                cfg.instrument.update(json.load(fh))
            print('已从 instrument.json 导入键位')
        except Exception:
            pass
    cfg.save(path)
    print('已生成 %s' % path)
    return 0


def cmd_export(args) -> int:
    cfg, instrument, options, cost, scheduler = build_all(args)
    score = load_score(args.file)
    plan = arrange(score, instrument, options)
    out = args.out or (os.path.splitext(args.file)[0] + '.plan.json')
    save_json_score(_plan_to_score(plan), out)
    print('已导出演奏计划：%s（%d 个音，%.1f 秒）'
          % (out, len(plan), plan.duration))
    return 0


def _plan_to_score(plan: Arrangement):
    from .score import Note, Score
    notes = [Note(pitch=s.pitch, start=s.start, duration=s.duration,
                  track=s.track) for s in plan.steps]
    return Score(title=plan.title + ' [plan]', notes=notes)


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------

def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--config', help='配置文件路径（默认 gtiharmonica.json）')


def add_tuning(parser: argparse.ArgumentParser) -> None:
    g = parser.add_argument_group('编排调参')
    g.add_argument('--speed', type=float, help='演奏速度 0.25..2.0')
    g.add_argument('--transpose', type=int, help='移调半音 -24..24')
    g.add_argument('--gate', type=float, help='音符连贯度 0.4..1.0')
    g.add_argument('--breath-ms', type=int, help='句尾换气毫秒 0..300')
    g.add_argument('--track', type=int, help='只演奏指定音轨（默认合并全部）')
    g.add_argument('--strategy', choices=list(STRATEGIES),
                   help='指法策略：%s' % ' | '.join(STRATEGIES))
    g.add_argument('--chord-policy', choices=['highest', 'lowest', 'first', 'loudest'],
                   help='和弦取音方式（默认 highest）')
    g.add_argument('--fold-prefer', choices=['nearest', 'down', 'up'],
                   help='八度折叠方向（默认 nearest）')
    g.add_argument('--no-fold', action='store_true', help='关闭八度折叠')


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog='gtiharmonica',
        description='《三角洲行动》口琴自动演奏引擎 v%s' % __version__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='示例:\n'
               '  gtiharmonica analyze song.mid --verbose\n'
               '  gtiharmonica play song.mid --strategy optimal --speed 0.9\n'
               '  gtiharmonica calibrate --save cal.json\n')
    ap.add_argument('--version', action='version', version=__version__)
    sub = ap.add_subparsers(dest='command', required=True)

    p = sub.add_parser('play', help='演奏（向游戏发送按键）')
    p.add_argument('file')
    add_common(p); add_tuning(p)
    p.add_argument('--hwnd', type=int, help='目标窗口句柄（默认取当前前台窗口）')
    p.add_argument('--countdown', type=int, default=3, help='开场倒计时秒数')
    p.add_argument('--dry-run', action='store_true', help='只打印不发送')
    p.add_argument('--no-focus', action='store_true', help='不校验窗口焦点')
    p.add_argument('--no-hotkeys', action='store_true', help='禁用 F8/F9 热键')
    p.add_argument('-v', '--verbose', action='store_true')
    p.set_defaults(func=cmd_play)

    p = sub.add_parser('preview', help='本机 MIDI 试听')
    p.add_argument('file')
    add_common(p); add_tuning(p)
    p.add_argument('-v', '--verbose', action='store_true')
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser('analyze', help='策略与音轨对比（不演奏）')
    p.add_argument('file')
    add_common(p); add_tuning(p)
    p.add_argument('-v', '--verbose', action='store_true')
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser('tracks', help='查看音轨信息')
    p.add_argument('file')
    add_common(p); add_tuning(p)
    p.set_defaults(func=cmd_tracks)

    p = sub.add_parser('export', help='导出编排后的演奏计划')
    p.add_argument('file')
    add_common(p); add_tuning(p)
    p.add_argument('-o', '--out')
    p.set_defaults(func=cmd_export)

    p = sub.add_parser('selftest', help='指法表自检')
    add_common(p)
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser('calibrate', help='校准游戏响应')
    add_common(p)
    p.add_argument('--hwnd', type=int)
    p.add_argument('--quick', action='store_true', help='只跑性能基准')
    p.add_argument('--save', help='把结果保存到 JSON')
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser('windows', help='列出可见窗口')
    p.add_argument('--filter', help='按标题过滤')
    p.set_defaults(func=cmd_windows)

    p = sub.add_parser('import-legacy',
                       help='批量导入旧版曲库')
    p.add_argument('source', help='旧版 songs 目录')
    p.add_argument('--to', help='目标曲库目录（默认本程序的曲库）')
    add_common(p); add_tuning(p)
    p.set_defaults(func=cmd_import_legacy)

    p = sub.add_parser('init', help='生成默认配置文件')
    add_common(p)
    p.add_argument('--force', action='store_true')
    p.set_defaults(func=cmd_init)

    p = sub.add_parser('transcribe',
                       help='音频 → 曲谱（内置 GAME 模型；支持 mp3/wav/flac/ogg）')
    p.add_argument('audio', help='音频文件，或装着一堆音频的目录（批量转）')
    p.add_argument('-o', '--output', help='输出目录（默认写入本程序的曲库）')
    p.add_argument('--title', help='曲名（默认取文件名；批量时忽略）')
    p.set_defaults(func=cmd_transcribe)

    return ap


def cmd_transcribe(args) -> int:
    """音频 → 曲谱：内置 GAME ONNX 推理，输出本程序的 JSON 曲谱。"""
    from .app import resolve_library_dir
    from .audio2score import (AUDIO_EXTS, default_model_dir, model_available,
                              transcribe_audio)
    from .score import save_json_score

    if not model_available():
        print('内置转谱模型不完整：%s' % default_model_dir(), file=sys.stderr)
        print('请使用完整解压的版本（模型在 gtiharmonica/assets/game_model）。')
        return 1

    src = args.audio
    files: List[str] = []
    if os.path.isdir(src):
        files = [os.path.join(src, n) for n in sorted(os.listdir(src))
                 if n.lower().endswith(AUDIO_EXTS)]
    elif os.path.isfile(src):
        files = [src]
    if not files:
        print('没找到音频文件：%s' % src, file=sys.stderr)
        return 1

    out_dir = args.output or resolve_library_dir()
    os.makedirs(out_dir, exist_ok=True)
    hr('音频转曲谱（内置 GAME ONNX 推理）')
    print('  音频 %d 个   输出 %s' % (len(files), out_dir))

    failed = 0
    for path in files:
        name = args.title if (args.title and len(files) == 1) else \
            os.path.splitext(os.path.basename(path))[0]
        print('  [%s]' % os.path.basename(path))
        shown = ['']

        def on_progress(stage: str, done: int, total: int,
                        _shown=shown) -> None:
            msg = '%s %d/%d' % (stage, done, total)
            if msg != _shown[0]:
                _shown[0] = msg
                print('\r      %-42s' % msg, end='', flush=True)

        try:
            score = transcribe_audio(path, title=name, progress=on_progress)
        except Exception as exc:
            print('\r      失败：%s' % exc)
            failed += 1
            continue
        dest = os.path.join(out_dir, name + '(音频转谱).json')
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(out_dir, '%s(音频转谱) (%d).json' % (name, n))
            n += 1
        save_json_score(score, dest)
        print('\r      -> %s（%d 音符，%d:%02d）%s'
              % (os.path.basename(dest), len(score.notes),
                 int(score.duration) // 60, int(score.duration) % 60,
                 ' ' * 12))

    print()
    if failed:
        print('  完成：%d 个成功，%d 个失败' % (len(files) - failed, failed))
        return 1
    print('  全部完成：%d 个' % len(files))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    setup_console()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print('\n已中断')
        return 130
    except (ValueError, OSError, ImportError, KeyError) as exc:
        print('\n错误：%s' % exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
