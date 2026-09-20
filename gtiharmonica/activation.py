"""卡密激活：输入一次卡密，激活状态绑定本机机器码。

设计取舍：

* **卡密只存哈希**。源码里没有明文卡密，比对的是
  ``sha256(SALT + 输入)`` —— 反编译也读不出卡密本身。
* **凭证绑定机器码**。机器码取自 Windows 的 MachineGuid（重装系统才变），
  凭证 = ``sha256(SALT + 机器码 + 卡密哈希)``。把程序目录整个拷到另一台
  机器，凭证对不上机器码，需要在新机器上重新激活。
* **完全离线**。不发请求、不存服务器，激活一次本机永久可用。
* 便携模式（程序目录有 portable.txt）时凭证跟着程序目录走，否则放
  ``%APPDATA%\\GTIHarmonica``，与曲库/配置的去向保持一致。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

SALT = 'gtiharmonica::license::v1'

#: 卡密的 sha256(SALT + 卡密)。这里是哈希，不是卡密本身。
KEY_HASH = 'e00d3aeea36d8e27522d223a5dbe24efc87240084aed7bb904759ec2f7c4a741'

LICENSE_FILE = '.license.json'


def _hash(text: str) -> str:
    return hashlib.sha256((SALT + text).encode('utf8')).hexdigest()


# ---------------------------------------------------------------------------
# 机器码
# ---------------------------------------------------------------------------

def _machine_guid() -> str:
    """Windows MachineGuid；读不到时退回 MAC + 计算机名。"""
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r'SOFTWARE\Microsoft\Cryptography') as key:
                value, _ = winreg.QueryValueEx(key, 'MachineGuid')
            if value:
                return str(value)
        except OSError:
            pass
    import uuid
    return '%s-%s' % (uuid.getnode(), os.environ.get('COMPUTERNAME', '?'))


def machine_id() -> str:
    """本机机器码（16 位十六进制摘要）。"""
    return _hash(_machine_guid())[:16]


def _expected_token() -> str:
    """本机应持有的凭证摘要：机器码与卡密哈希绑在一起。"""
    return _hash(machine_id() + KEY_HASH)


# ---------------------------------------------------------------------------
# 卡密校验
# ---------------------------------------------------------------------------

def verify_key(text: str) -> bool:
    """卡密是否正确。空串、带空格都能正确处理。"""
    return bool(text) and _hash(text.strip()) == KEY_HASH


# ---------------------------------------------------------------------------
# 凭证读写
# ---------------------------------------------------------------------------

def license_path() -> str:
    """凭证文件路径：便携模式跟程序目录，否则跟用户数据目录。"""
    from .app import app_root, user_data_dir

    root = app_root()
    if os.path.exists(os.path.join(root, 'portable.txt')):
        base = root
    else:
        base = user_data_dir()
    return os.path.join(base, LICENSE_FILE)


def is_activated() -> bool:
    """本机是否已激活。环境变量 GTIHARMONICA_SKIP_ACTIVATION=1 时直接放行
    （开发与自动化测试用，正式环境不设这个变量）。"""
    if os.environ.get('GTIHARMONICA_SKIP_ACTIVATION') == '1':
        return True
    try:
        with open(license_path(), encoding='utf8') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return data.get('token') == _expected_token()


def activate(text: str) -> bool:
    """校验卡密并写入本机凭证。返回是否成功。"""
    if not verify_key(text):
        return False
    path = license_path()
    data = {
        'token': _expected_token(),
        'machine': machine_id(),
        'activated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return True
