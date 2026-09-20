"""曲库安全性的自检：位置迁移 + 构建校验。

背景：曲库原先放在 exe 旁边的 songs，而那个位置在构建产物 dist/ 内部，
PyInstaller 重建会先整个删掉该目录 —— 用户自己转的谱因此丢过一次，
而且构建日志还写着「已恢复」。这里把两个防线的行为固定下来：

  1. 打包后曲库落在 %APPDATA%，旧曲库首次启动自动合并（只补不覆盖）
  2. 构建前把用户曲目落盘备份，构建后逐文件校验，缺一首就报错

运行：python tools/_check_library.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gtiharmonica import app as app_mod            # noqa: E402
import build as build_mod                          # noqa: E402

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


def write_song(path: str, title: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf8') as fh:
        json.dump({'title': title, 'notes': []}, fh, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 1. 旧曲库合并到用户数据目录
# ---------------------------------------------------------------------------

def test_adopt() -> None:
    print('\n[1] 旧便携曲库 → 用户数据目录的合并')

    root = tempfile.mkdtemp(prefix='gti-adopt-')
    try:
        portable = os.path.join(root, 'program', 'songs')
        user = os.path.join(root, 'appdata', 'songs')
        os.makedirs(user, exist_ok=True)

        # 旧曲库：内置曲 + 用户自己转的谱 + 一个不该被搬的杂项文件
        write_song(os.path.join(portable, '内置A.json'), 'A')
        write_song(os.path.join(portable, '用户曲目.json'), '用户')
        with open(os.path.join(portable, 'readme.txt'), 'w') as fh:
            fh.write('not a song')

        # 用户目录里已有一首同名的、内容不同的曲子 —— 必须保留现场
        write_song(os.path.join(user, '内置A.json'), '新版的A')
        user_a = os.path.join(user, '内置A.json')
        before_bytes = open(user_a, 'rb').read()

        moved = app_mod._adopt_portable_library(portable, user)
        check('合并数量正确（只补缺失的 1 首）', moved == 1, 'moved=%d' % moved)
        check('用户曲目已搬过来',
              os.path.exists(os.path.join(user, '用户曲目.json')))
        check('同名文件没有被覆盖',
              open(user_a, 'rb').read() == before_bytes)
        check('非曲库后缀不搬',
              not os.path.exists(os.path.join(user, 'readme.txt')))

        # 幂等：再跑一次不应该有任何新动作
        moved2 = app_mod._adopt_portable_library(portable, user)
        check('重复合并是幂等的', moved2 == 0, 'moved=%d' % moved2)

        # 源目录文件必须原样保留（绝不搬运式删除）
        check('源曲库文件仍然在',
              os.path.exists(os.path.join(portable, '用户曲目.json')))

        # 同一个目录不算迁移
        check('源与目标相同则不动',
              app_mod._adopt_portable_library(user, user) == 0)

        # 源目录不存在也不能炸
        check('源目录不存在返回 0',
              app_mod._adopt_portable_library(
                  os.path.join(root, 'nope'), user) == 0)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2. 曲库位置决策
# ---------------------------------------------------------------------------

def test_resolve() -> None:
    print('\n[2] 曲库位置决策')

    root = tempfile.mkdtemp(prefix='gti-resolve-')
    prog = os.path.join(root, 'program')
    data = os.path.join(root, 'appdata')
    os.makedirs(prog, exist_ok=True)
    os.makedirs(data, exist_ok=True)
    write_song(os.path.join(prog, 'songs', '用户的谱.json'), 'x')

    real_frozen = app_mod.is_frozen
    real_root = app_mod.app_root
    real_data = app_mod.user_data_dir

    def fake_root():
        return prog

    def fake_data():
        return data

    try:
        # --- 开发环境：用项目根目录的 songs ---
        app_mod.is_frozen = lambda: False
        app_mod.app_root = fake_root
        app_mod.user_data_dir = fake_data
        dev = app_mod.resolve_library_dir()
        check('开发环境用程序旁边的 songs',
              os.path.normcase(dev) == os.path.normcase(
                  os.path.join(prog, 'songs')), dev)

        # --- 打包环境：用 %APPDATA% ---
        app_mod.is_frozen = lambda: True
        packed = app_mod.resolve_library_dir()
        check('打包后改用用户数据目录',
              os.path.normcase(packed) == os.path.normcase(
                  os.path.join(data, 'songs')), packed)
        check('旧曲库已自动合并过来',
              os.path.exists(os.path.join(packed, '用户的谱.json')))

        # --- 便携模式：exe 旁边放 portable.txt 就跟着程序走 ---
        with open(os.path.join(prog, 'portable.txt'), 'w') as fh:
            fh.write('')
        portable = app_mod.resolve_library_dir()
        check('portable.txt 存在时保持便携',
              os.path.normcase(portable) == os.path.normcase(
                  os.path.join(prog, 'songs')), portable)
        os.remove(os.path.join(prog, 'portable.txt'))
    finally:
        app_mod.is_frozen = real_frozen
        app_mod.app_root = real_root
        app_mod.user_data_dir = real_data
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. 构建前的快照与构建后的校验
# ---------------------------------------------------------------------------

def test_manifest_and_verify() -> None:
    print('\n[3] 曲库快照与构建后校验')

    root = tempfile.mkdtemp(prefix='gti-verify-')
    try:
        before_dir = os.path.join(root, 'before')
        after_dir = os.path.join(root, 'after')
        write_song(os.path.join(before_dir, '甲.json'), 'a')
        write_song(os.path.join(before_dir, '乙.json'), 'b')

        before = build_mod.library_manifest(before_dir)
        check('快照记录全部曲子', len(before) == 2, str(sorted(before)))

        # 情况一：全都还在 → 通过
        write_song(os.path.join(after_dir, '甲.json'), 'a')
        write_song(os.path.join(after_dir, '乙.json'), 'b')
        check('全都还在时校验通过',
              build_mod.verify_library(before, after_dir, {}) == [])

        # 情况二：少了一首 → 必须点名报出来
        os.remove(os.path.join(after_dir, '乙.json'))
        missing = build_mod.verify_library(before, after_dir, {})
        check('缺一首时校验失败并点名', missing == ['乙.json'], str(missing))

        # 情况三：救出来的用户曲目没恢复 → 同样要报
        write_song(os.path.join(after_dir, '乙.json'), 'b')
        missing = build_mod.verify_library(
            before, after_dir, {'用户曲目.json': b'{}'})
        check('用户曲目没恢复时校验失败',
              missing == ['用户曲目.json'], str(missing))

        # 情况四：整个曲库目录不见了
        missing = build_mod.verify_library(
            before, os.path.join(root, 'gone'), {})
        check('曲库目录整个丢失能查出',
              sorted(missing) == ['乙.json', '甲.json'], str(missing))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_keep_dir_wiring() -> None:
    print('\n[4] 用户曲目备份落在 build/ 而不是 dist/')

    check('备份目录不在 dist 内',
          os.path.normcase(build_mod.DIST)
          not in os.path.normcase(build_mod.KEEP_DIR),
          build_mod.KEEP_DIR)
    check('备份目录名为 songs-keep',
          os.path.basename(build_mod.KEEP_DIR) == 'songs-keep')
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'build.py'), encoding='utf8').read()
    check('构建脚本里确实调用了校验',
          'verify_library(before' in src)
    check('校验失败会返回非零退出码',
          '曲库校验未通过' in src and 'return 1' in src)


if __name__ == '__main__':
    print('=' * 62)
    print('曲库安全性自检')
    print('=' * 62)
    test_adopt()
    test_resolve()
    test_manifest_and_verify()
    test_keep_dir_wiring()
    print('\n' + '=' * 62)
    print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
    print('=' * 62)
    sys.exit(1 if FAIL else 0)
