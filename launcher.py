"""GUI 启动器，同时也是 PyInstaller 的打包入口。

放在包外，这样打包后入口脚本没有相对导入问题。

权限策略：**平时不提权，演奏时再提权**。
  * 非提权进程才能接收资源管理器拖入的文件（Windows 的 UIPI 会拒绝
    中完整性级别的 Explorer 把文件拖进高完整性级别窗口），导入体验
    依赖这一点；
  * 而向提权运行的游戏发送按键又必须提权（否则 SendInput 被静默丢弃）。
所以清单用 asInvoker 启动，只有在检测到「目标游戏是提权进程而自己
不是」时，才弹一次 UAC 以管理员重启并带上当前曲目（见 main.py 的
_check_target_elevation / _restart_elevated）。
"""
import ctypes
import os
import sys


def _is_elevated() -> bool:
    if os.name != 'nt':
        return True
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    token = ctypes.c_void_p()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008,
                                ctypes.byref(token)):      # TOKEN_QUERY
        return False
    elev = ctypes.c_ulong()
    ret = ctypes.c_ulong()
    # TokenElevation = 20
    ok = adv.GetTokenInformation(token, 20, ctypes.byref(elev),
                                 ctypes.sizeof(elev), ctypes.byref(ret))
    k32.CloseHandle(token)
    return bool(ok and elev.value)


from gtiharmonica.app import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
