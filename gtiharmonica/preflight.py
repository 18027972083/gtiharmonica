"""播放前环境自检（移植自 midikey-player 的 PreflightCheck 思路）。

三角洲以管理员运行时，不提权的进程 SendInput 会被静默拒绝；
中文输入法开着时会截走按键 —— 这两种坑的表现都是「点了播放没声音」，
用户很难自查。播放前跑一遍这里的检查，把问题提前亮出来。

三态结论：PASS 通过 / WARN 提醒（能弹但可能有问题）/ FAIL 不通过。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass

#: CJK 语言 ID（低位 16 位）：简中/繁中/日语/韩语 —— 只有这些布局才有输入法截键问题
_CJK_LANGS = {0x0804, 0x0404, 0x0411, 0x0412}
_IME_CMODE_NATIVE = 0x0001
_WM_IME_CONTROL = 0x0283
_IMC_GETOPENSTATUS = 0x0005
_IMC_GETCONVERSIONMODE = 0x0006

user32 = ctypes.windll.user32
imm32 = ctypes.windll.imm32


@dataclass
class Check:
    """一项检查结论。"""

    name: str
    passed: bool
    detail: str = ''
    blocking: bool = False      # True = 不解决就别弹


@dataclass
class Report:
    """自检报告。"""

    admin: Check
    ime: Check

    @property
    def ok(self) -> bool:
        return self.admin.passed and self.ime.passed

    @property
    def summary(self) -> str:
        marks = []
        for c in (self.admin, self.ime):
            mark = '✓' if c.passed else ('✗' if c.blocking else '!')
            text = c.name + (' ' + c.detail if c.detail else '')
            marks.append('%s %s' % (mark, text))
        return '   '.join(marks)


def is_admin() -> bool:
    """当前进程是否以管理员运行。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return sys.platform != 'win32'


def _foreground_thread() -> tuple:
    """前台窗口句柄与其所在线程 id。"""
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD(0)
    tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return hwnd, tid


def check_ime() -> Check:
    """输入法检查：目标窗口线程的键盘布局与 IME 开合状态。"""
    try:
        hwnd, tid = _foreground_thread()
        hkl = user32.GetKeyboardLayout(tid)
        lang = hkl & 0xFFFF
        if lang not in _CJK_LANGS:
            return Check('输入法', True, '英文键盘')

        # CJK 布局：再确认 IME 是否开着、是否处于中文模式。
        # 跨进程拿不到 HIMC，改用默认 IME 窗口的 WM_IME_CONTROL 查询。
        ime_wnd = imm32.ImmGetDefaultIMEWnd(hwnd)
        if ime_wnd:
            open_status = user32.SendMessageW(
                ime_wnd, _WM_IME_CONTROL, _IMC_GETOPENSTATUS, 0)
            conv = user32.SendMessageW(
                ime_wnd, _WM_IME_CONTROL, _IMC_GETCONVERSIONMODE, 0)
            if not open_status:
                return Check('输入法', True, '中文布局但输入法已关闭')
            if conv & _IME_CMODE_NATIVE:
                return Check('输入法', False,
                             '中文模式会截走按键，请切到英文（Shift）', True)
            return Check('输入法', True, '中文布局，英文模式')

        # 查询失败：保守提醒，不算失败
        return Check('输入法', True, '中文布局（状态未知，建议切英文）')
    except Exception:
        return Check('输入法', True, '无法检测')


def run_preflight(need_admin: bool = True) -> Report:
    """跑一遍自检。need_admin：目标程序是否以管理员运行（由调用方判断）。"""
    if need_admin and not is_admin():
        admin = Check('管理员', False, '目标程序已提权，本程序需要管理员', True)
    else:
        admin = Check('管理员', True, '' if not need_admin else '已提权')
    return Report(admin=admin, ime=check_ime())


def is_target_elevated(hwnd: int) -> bool:
    """判断目标窗口的进程是否以管理员运行（OpenProcess 查 token）。

    读不到（权限不足时连自己的提权状态都能比对）就返回 True，
    保守处理：宁可白提权也不让 SendInput 静默失败。
    """
    try:
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return True
        try:
            TOKEN_QUERY = 0x0008
            TokenElevation = 20
            token = wintypes.HANDLE()
            if kernel32.OpenProcessToken(handle, TOKEN_QUERY,
                                         ctypes.byref(token)):
                try:
                    elev = wintypes.DWORD(0)
                    ret_len = wintypes.DWORD(0)
                    advapi32 = ctypes.windll.advapi32
                    ok = advapi32.GetTokenInformation(
                        token, TokenElevation, ctypes.byref(elev),
                        ctypes.sizeof(elev), ctypes.byref(ret_len))
                    return bool(ok and elev.value)
                finally:
                    kernel32.CloseHandle(token)
            return True
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return True
