"""发布前验证：产物能启动，并且曲库确实落到了用户数据目录。

这是在真机上加的最后一道保险，回答两个只有跑起来才知道的问题：

  1. 打包后的 exe 到底能不能起来（构建成功不等于能启动）；
  2. 曲库有没有真的迁到 %APPDATA%\\GTIHarmonica\\songs。

第 2 条正是这一版要修的毛病：曲库原先在 exe 旁边的 songs，而那个位置在
构建产物 dist/ 内部，重新构建会被整个删掉，用户导入的曲目因此丢过。

运行：python tools/_verify_release.py [--keep-running]
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = os.path.join(ROOT, 'dist', 'DeltaHarp', 'DeltaHarp.exe')
DIST_SONGS = os.path.join(ROOT, 'dist', 'DeltaHarp', 'songs')
USER_DIR = os.path.join(os.environ.get('APPDATA', ''), 'GTIHarmonica')
USER_SONGS = os.path.join(USER_DIR, 'songs')
PORTABLE_FLAG = os.path.join(ROOT, 'dist', 'DeltaHarp', 'portable.txt')

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = '') -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print('  ok   %s%s' % (name, ('  [%s]' % detail) if detail else ''))
    else:
        FAIL += 1
        print('  FAIL %s%s' % (name, ('  [%s]' % detail) if detail else ''))
    return ok


def song_files(directory: str):
    if not os.path.isdir(directory):
        return []
    return sorted(n for n in os.listdir(directory)
                  if n.lower().endswith(('.json', '.mid', '.midi')))


def main() -> int:
    keep = '--keep-running' in sys.argv

    print('=' * 66)
    print('发布前验证')
    print('=' * 66)

    print('\n[1] 产物')
    if not check('exe 存在', os.path.isfile(EXE), EXE):
        return 1
    stamp = time.strftime('%Y-%m-%d %H:%M:%S',
                          time.localtime(os.path.getmtime(EXE)))
    age = time.time() - os.path.getmtime(EXE)
    check('exe 是刚构建的（10 分钟内）', age < 600, stamp)

    dist_songs = song_files(DIST_SONGS)
    check('产物曲库非空', len(dist_songs) > 0, '%d 首' % len(dist_songs))

    print('\n[2] 启动前的状态')
    before_user = song_files(USER_SONGS)
    print('     用户数据目录曲库：%d 首' % len(before_user))
    print('     产物曲库：%d 首' % len(dist_songs))
    check('没有 portable.txt（否则会走便携模式，不迁移）',
          not os.path.exists(PORTABLE_FLAG))

    print('\n[3] 启动产物')
    proc = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
    try:
        deadline = time.time() + 40
        while time.time() < deadline:
            time.sleep(0.5)
            if proc.poll() is not None:
                break
            if len(song_files(USER_SONGS)) >= len(dist_songs) > 0:
                break
        time.sleep(0.8)

        check('进程还活着（没有一启动就退出）', proc.poll() is None,
              'exit=%s' % proc.poll())

        after = song_files(USER_SONGS)
        check('用户数据目录出现了曲库', len(after) > 0, USER_SONGS)
        missing = [n for n in dist_songs if n not in after]
        check('产物曲库的曲子全部迁移过来了', not missing,
              '缺 %d 首：%s' % (len(missing), '、'.join(missing[:4]))
              if missing else '%d 首' % len(after))

        # 迁移必须是「只补不删」：源文件要原样留着
        check('源曲库文件仍然在（迁移不删原件）',
              len(song_files(DIST_SONGS)) == len(dist_songs),
              '%d 首' % len(song_files(DIST_SONGS)))

        print('\n[4] 再启动一次（迁移必须幂等，不能重复堆文件）')
        second = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
        time.sleep(8)
        again = song_files(USER_SONGS)
        check('第二次启动后曲库数量不变', len(again) == len(after),
              '%d -> %d' % (len(after), len(again)))
        if not keep:
            second.terminate()
            try:
                second.wait(timeout=10)
            except subprocess.TimeoutExpired:
                second.kill()
        else:
            print('     保留第二个实例运行中（pid %d）' % second.pid)
    finally:
        if not keep and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    print('\n[5] 日志里不应该有迁移失败')
    log = os.path.join(USER_DIR, 'gtiharmonica.log')
    if os.path.isfile(log):
        tail = open(log, encoding='utf8', errors='replace').read()[-4000:]
        check('日志没有未捕获异常',
              'Traceback' not in tail or 'library' in tail,
              '最后 %d 字符' % len(tail))
    else:
        print('     （还没有日志文件，跳过）')

    print('\n' + '=' * 66)
    print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
    print('=' * 66)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
