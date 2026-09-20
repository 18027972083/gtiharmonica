# -*- coding: utf-8 -*-
"""端到端验证：打包后的 exe 上，「删除曲目后重启不再复活」。

流程（全程只碰一个临时假曲目，不碰任何真实曲子）：
  1. 往 exe 旁的 songs/ 放一首假曲目 _临时验证曲.json
  2. 启动 exe 再关掉 → 假曲目应被「程序目录曲库补齐」搬进用户曲库
  3. 模拟用户在界面里删除：删文件 + 写删除名单（这就是 _delete_current 做的事）
  4. 再启动 exe 再关掉 → 假曲目不应该回来
  5. 清理：假文件与删除名单条目都删掉
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, 'dist', '大肥鲸洲琴工具包')
EXE = os.path.join(DIST, '大肥鲸洲琴工具包.exe')
SRC = os.path.join(DIST, 'songs')
USER = os.path.join(os.environ['APPDATA'], 'GTIHarmonica', 'songs')
PROBE = '_临时验证曲.json'

sys.path.insert(0, ROOT)
from gtiharmonica import library_state as lstate


def run_app(seconds=10):
    proc = subprocess.Popen([EXE], cwd=DIST)
    time.sleep(seconds)
    subprocess.run(['taskkill', '/PID', str(proc.pid), '/F'],
                   capture_output=True)
    time.sleep(1.5)


def count_songs(directory):
    return len([n for n in os.listdir(directory)
                if not n.startswith('.')
                and n.lower().endswith(('.json', '.mid', '.midi'))])


probe_src = os.path.join(SRC, PROBE)
probe_user = os.path.join(USER, PROBE)
before = count_songs(USER)
print('用户曲库起始：%d 首' % before)

shutil.copy2(os.path.join(SRC, '小星星.json'), probe_src)
if os.path.exists(probe_user):
    os.remove(probe_user)
lstate.forget_deleted(USER, PROBE)

print('第 1 次启动 exe ...')
run_app()
copied = os.path.exists(probe_user)
print('  → 假曲目是否被补齐进用户曲库：%s' % copied)

os.remove(probe_user)
lstate.mark_deleted(USER, PROBE)
print('模拟界面删除（删文件 + 写删除名单）：%s' % lstate.read_deleted(USER))

print('第 2 次启动 exe ...')
run_app()
back = os.path.exists(probe_user)
print('  → 删除的曲目是否复活：%s' % back)

# 清理
os.remove(probe_src)
lstate.forget_deleted(USER, PROBE)
after = count_songs(USER)
print('清理完成：用户曲库 %d 首（起始 %d 首），删除名单=%s'
      % (after, before, lstate.read_deleted(USER)))
ok = copied and not back and after == before
print('结论：%s' % ('PASS —— 补齐路径正常，且删除后重启不再复活'
                    if ok else 'FAIL —— 见上面各步结果'))
sys.exit(0 if ok else 1)
