"""创建 Windows 快捷方式。

为什么用 PowerShell 而不是自己写 .lnk：
  .lnk 是带 CLSID 的复合二进制格式，手写容易出错且难以维护。
  这里通过 WScript.Shell COM 对象创建，是 Windows 上最稳的方式。
  这是一次性操作，调用开销无所谓。
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional

LINK_NAME = '大肥鲸洲琴工具包'
DESCRIPTION = '三角洲行动口琴自动演奏'


def can_create() -> bool:
    return sys.platform == 'win32'


def _ps_quote(value: str) -> str:
    """把字符串安全地嵌入 PowerShell 单引号字面量。"""
    return "'" + value.replace("'", "''") + "'"


def _powershell_exe() -> str:
    for name in ('powershell.exe', 'pwsh.exe'):
        for base in (os.environ.get('SystemRoot', r'C:\Windows'),):
            path = os.path.join(base, 'System32', 'WindowsPowerShell', name)
            if os.path.exists(path):
                return path
    return 'powershell.exe'


def resolve_target() -> Optional[str]:
    """当前程序的可执行文件路径；开发环境下返回 None。"""
    if getattr(sys, 'frozen', False):
        return sys.executable
    return None


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

    script = [
        '$ErrorActionPreference = "Stop"',
        '[Console]::OutputEncoding = [Text.Encoding]::UTF8',
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
        '  Write-Output $p',
        '}',
    ]

    try:
        proc = subprocess.run(
            [_powershell_exe(), '-NoProfile', '-NonInteractive',
             '-ExecutionPolicy', 'Bypass', '-Command', '\n'.join(script)],
            capture_output=True, text=True, timeout=25,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        return []

    if proc.returncode != 0:
        return []

    made = [line.strip() for line in (proc.stdout or '').splitlines()
            if line.strip().lower().endswith('.lnk')]
    return [p for p in made if os.path.exists(p)]


def expected_paths(name: str = LINK_NAME) -> List[str]:
    """预计会创建的路径（不实际创建），用于界面提示。"""
    out = []
    try:
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        out.append(os.path.join(desktop, name + '.lnk'))
    except Exception:
        pass
    appdata = os.environ.get('APPDATA')
    if appdata:
        out.append(os.path.join(appdata, 'Microsoft', 'Windows', 'Start Menu',
                                'Programs', name + '.lnk'))
    return out


def already_exists(name: str = LINK_NAME) -> bool:
    return any(os.path.exists(p) for p in expected_paths(name))
