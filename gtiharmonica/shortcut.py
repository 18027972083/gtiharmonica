"""创建 Windows 快捷方式。

为什么用 PowerShell 而不是自己写 .lnk：
  .lnk 是带 CLSID 的复合二进制格式，手写容易出错且难以维护。
  这里通过 WScript.Shell COM 对象创建，是 Windows 上最稳的方式。
  这是一次性操作，调用开销无所谓。

两个踩过的坑（2026-09-20 用户报告「创建失败」但快捷方式其实建好了）：
  * powershell.exe 在 System32\\WindowsPowerShell\\**v1.0**\\ 下，
    早先路径少了一层，找不到就退回裸名，PATH 不完整时直接失败；
  * 脚本里的输出是 UTF-8，而打包版 Python 的 locale 是 **cp936**，
    text=True 会把中文路径按 GBK 解成乱码 → 校验「文件不存在」→
    误报失败。现在：脚本用 -EncodedCommand 传入、输出走 base64，
    并且最终以**磁盘上的文件**为准判断成功与否。
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import time
from typing import List, Optional

LINK_NAME = '大肥鲸洲琴工具包'
DESCRIPTION = '三角洲行动口琴自动演奏'


def can_create() -> bool:
    return sys.platform == 'win32'


def _ps_quote(value: str) -> str:
    """把字符串安全地嵌入 PowerShell 单引号字面量。"""
    return "'" + value.replace("'", "''") + "'"


def _powershell_exe() -> str:
    """powershell.exe 的绝对路径；都不在时退回 PATH 查找。"""
    root = os.environ.get('SystemRoot', r'C:\Windows')
    for rel in (r'System32\WindowsPowerShell\v1.0\powershell.exe',
                r'System32\WindowsPowerShell\v1.0\pwsh.exe'):
        path = os.path.join(root, rel)
        if os.path.exists(path):
            return path
    return shutil.which('powershell.exe') or shutil.which('pwsh.exe') \
        or 'powershell.exe'


def resolve_target() -> Optional[str]:
    """当前程序的可执行文件路径；开发环境下返回 None。"""
    if getattr(sys, 'frozen', False):
        return sys.executable
    return None


def _desktop_dir() -> str:
    return os.path.join(os.path.expanduser('~'), 'Desktop')


def _start_menu_dir() -> str:
    appdata = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(appdata, 'Microsoft', 'Windows', 'Start Menu', 'Programs')


def _log(text: str) -> None:
    """写应用日志（诊断用；日志失败绝不影响创建）。"""
    try:
        from .app import log_path
        with open(log_path(), 'a', encoding='utf8') as fh:
            fh.write('[shortcut] %s\n' % text)
    except Exception:
        pass


def create(target: Optional[str] = None, workdir: Optional[str] = None,
           name: str = LINK_NAME, desktop: bool = True,
           start_menu: bool = True, icon: Optional[str] = None) -> List[str]:
    """在桌面与开始菜单创建快捷方式，返回实际创建的 .lnk 路径。

    未打包（开发环境）时 target 为 None，直接返回空列表。
    """
    if not can_create():
        return []
    target = target or resolve_target()
    if not target:
        return []

    workdir = workdir or os.path.dirname(target)
    icon = icon or ('%s,0' % target)

    wanted: List[str] = []
    if desktop:
        wanted.append(os.path.join(_desktop_dir(), name + '.lnk'))
    if start_menu:
        wanted.append(os.path.join(_start_menu_dir(), name + '.lnk'))

    script = [
        '$ErrorActionPreference = "Stop"',
        # GUI 进程没有控制台，这行可能抛异常 —— 但不该因此中止创建
        'try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}',
        '$ws = New-Object -ComObject WScript.Shell',
        '$dirs = @()',
    ]
    if desktop:
        script.append("$dirs += [Environment]::GetFolderPath('Desktop')")
    if start_menu:
        script.append("$dirs += (Join-Path $env:APPDATA "
                      "'Microsoft\\Windows\\Start Menu\\Programs')")
    script += [
        'foreach ($d in $dirs) {',
        '  if (-not (Test-Path $d)) { continue }',
        '  $p = Join-Path $d %s' % _ps_quote(name + '.lnk'),
        '  $s = $ws.CreateShortcut($p)',
        '  $s.TargetPath = %s' % _ps_quote(target),
        '  $s.WorkingDirectory = %s' % _ps_quote(workdir),
        '  $s.IconLocation = %s' % _ps_quote(icon),
        '  $s.Description = %s' % _ps_quote(DESCRIPTION),
        '  $s.Save()',
        # 输出 base64：绕开控制台代码页 —— 打包版 Python 的 locale 是
        # cp936，直接输出中文路径会被按 GBK 解码成乱码而校验失败
        '  Write-Output ([Convert]::ToBase64String('
        '[Text.Encoding]::UTF8.GetBytes($p)))',
        '}',
    ]

    started = time.time()
    rc = -1
    raw = b''
    try:
        encoded = base64.b64encode(
            '\n'.join(script).encode('utf-16-le')).decode('ascii')
        proc = subprocess.run(
            [_powershell_exe(), '-NoProfile', '-NonInteractive',
             '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
            capture_output=True, timeout=25,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        rc, raw = proc.returncode, proc.stdout or b''
    except Exception as exc:
        _log('create 调用失败（%s: %s）' % (type(exc).__name__, exc))

    made: List[str] = []
    for line in raw.splitlines():
        try:
            p = base64.b64decode(line.strip()).decode('utf8')
        except Exception:
            continue
        if p.lower().endswith('.lnk') and os.path.exists(p):
            made.append(p)

    # 兜底：以磁盘上的结果为准 —— 刚被创建/重写的 .lnk 就算成功
    if not made:
        for p in wanted:
            try:
                if os.path.exists(p) and os.path.getmtime(p) >= started - 2:
                    made.append(p)
            except OSError:
                pass

    if made:
        _log('已创建：%s' % '；'.join(os.path.basename(p) for p in made))
    else:
        _log('创建失败（powershell rc=%s，无新 .lnk）' % rc)
    return made


def expected_paths(name: str = LINK_NAME) -> List[str]:
    """预计会创建的路径（不实际创建），用于界面提示。"""
    return [os.path.join(_desktop_dir(), name + '.lnk'),
            os.path.join(_start_menu_dir(), name + '.lnk')]


def already_exists(name: str = LINK_NAME) -> bool:
    return any(os.path.exists(p) for p in expected_paths(name))
