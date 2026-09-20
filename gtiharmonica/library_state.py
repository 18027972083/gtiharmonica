# -*- coding: utf-8 -*-
"""曲库目录里的两份元数据：删除名单（墓碑）与收藏名单。

为什么是 .txt 而不是 .json：曲库扫描按 .json / .mid / .midi 后缀认曲目
（app.LIBRARY_EXTS、panels.AUDIO_EXT），元数据文件换一个后缀才不会自己
出现在曲目列表里。

两份文件都落在曲库目录自身，跟着曲库走 —— 便携模式（exe 旁的
portable.txt）或以后换目录都一致，不需要用户数据目录参与。

删除名单存在的理由：程序目录旁边（打包后是 exe 所在的 songs/）可能有一份
曲库，启动时会做一次「补齐」把用户曲库里没有的曲子拷进来。用户删掉的曲子
如果在那份源曲库里还在，下次启动就会被拷回来（用户报的「删除后重启又出现」）。
删过一次的名字记进墓碑，补齐与内置曲目安装都会跳过它。
"""
from __future__ import annotations

import os
from typing import List

#: 曲库目录内的元数据文件名（刻意不带曲库认的后缀）
DELETED_NAME = '.deleted.txt'
FAVORITES_NAME = '.favorites.txt'


def _read_names(path: str) -> List[str]:
    try:
        with open(path, encoding='utf8') as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    names = []
    for line in lines:
        name = line.strip()
        if name and not name.startswith('#'):
            names.append(name)
    return names


def _write_names(path: str, names: List[str]) -> None:
    try:
        with open(path, 'w', encoding='utf8') as fh:
            fh.write(''.join(n + '\n' for n in names))
    except OSError:
        pass  # 元数据写不进去（只读目录等）绝不能影响主流程


# ---------------------------------------------------------------------------
# 删除名单（墓碑）
# ---------------------------------------------------------------------------

def deleted_path(directory: str) -> str:
    return os.path.join(directory, DELETED_NAME)


def read_deleted(directory: str) -> List[str]:
    return _read_names(deleted_path(directory))


def is_deleted(directory: str, name: str) -> bool:
    low = name.lower()
    return any(n.lower() == low for n in read_deleted(directory))


def mark_deleted(directory: str, name: str) -> None:
    """把曲目记进删除名单：之后的自动补齐、内置曲目安装都会跳过它。"""
    names = read_deleted(directory)
    low = name.lower()
    if any(n.lower() == low for n in names):
        return
    names.append(name)
    _write_names(deleted_path(directory), names)


def forget_deleted(directory: str, name: str) -> None:
    """撤回墓碑。用户主动把同名文件重新导入 / 拖回来时调用 —— 那是明确的
    「我要它」的意思，不该继续被当成已删除。"""
    names = read_deleted(directory)
    low = name.lower()
    kept = [n for n in names if n.lower() != low]
    if len(kept) != len(names):
        _write_names(deleted_path(directory), kept)


# ---------------------------------------------------------------------------
# 收藏名单
# ---------------------------------------------------------------------------

def favorites_path(directory: str) -> str:
    return os.path.join(directory, FAVORITES_NAME)


def read_favorites(directory: str) -> List[str]:
    return _read_names(favorites_path(directory))


def write_favorites(directory: str, names: List[str]) -> None:
    _write_names(favorites_path(directory), names)


def is_favorite(directory: str, name: str) -> bool:
    low = name.lower()
    return any(n.lower() == low for n in read_favorites(directory))


def set_favorite(directory: str, name: str, favorite: bool) -> List[str]:
    """设置收藏状态，返回写入后的名单（保持收藏先后顺序）。"""
    names = read_favorites(directory)
    low = name.lower()
    if favorite:
        if not any(n.lower() == low for n in names):
            names.append(name)
    else:
        names = [n for n in names if n.lower() != low]
    write_favorites(directory, names)
    return names
