"""一键构建脚本。

    python build.py                # 文件夹版（默认，启动快）
    python build.py --onefile      # 单文件版
    python build.py --clean        # 构建前清掉旧产物
    python build.py --full         # 不做裁剪，保留全部 Qt 组件

产物：
    dist/大肥鲸洲琴工具包/大肥鲸洲琴工具包.exe   （文件夹版）
    dist/大肥鲸洲琴工具包.exe                （单文件版）

体积优化说明
------------
PySide6 的 hook 会把整个 Qt 运行时塞进来，其中大部分本项目用不到。
这里通过生成的 spec 文件过滤二进制与数据文件，把一个纯 QtWidgets 应用
不需要的东西全部剔除，实测可从 ~91 MB 压到 ~60 MB：

  * opengl32sw.dll      软件 OpenGL 渲染器（19.7 MB）—— 只有 QtQuick/OpenGL 才需要
  * libcrypto/libssl    OpenSSL（7.2 MB）—— 本程序不联网
  * Qt6Network          Qt 网络模块 —— 同上
  * Qt6Svg / Qt6Pdf     未使用的格式支持
  * imageformats/*      除 jpeg 外的图片格式插件（PNG 是 Qt 内置的，不需要插件）
  * translations/*      Qt 自带翻译，界面文字全部由本程序自己提供

想保留全部组件（例如以后要用 QtQuick）就加 --full。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(ROOT, 'build')
#: 产物目录。默认 dist/；打包时若旧版本正在运行（里面的 DLL 被占用，
#: 清理必然失败），用 GTI_DIST 指到别处构建，不用去关用户正在用的程序。
DIST = os.path.abspath(os.environ.get('GTI_DIST')
                       or os.path.join(ROOT, 'dist'))
ICON = os.path.join(BUILD, 'app.ico')
SPEC = os.path.join(BUILD, 'DeltaHarp.spec')
NAME = '大肥鲸洲琴工具包'

# 用不到的 Qt Python 模块
QT_EXCLUDES = [
    'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuick3D',
    'PySide6.QtQuickWidgets', 'PySide6.QtQuickControls2',
    'PySide6.QtNetwork', 'PySide6.QtNetworkAuth', 'PySide6.QtWebSockets',
    'PySide6.QtWebChannel', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
    'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
    'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
    'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
    'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtPositioning',
    'PySide6.QtSerialPort', 'PySide6.QtSerialBus', 'PySide6.QtRemoteObjects',
    'PySide6.QtScxml', 'PySide6.QtSensors', 'PySide6.QtSpatialAudio',
    'PySide6.QtStateMachine', 'PySide6.QtTextToSpeech', 'PySide6.QtUiTools',
    'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtOpenGL',
    'PySide6.QtOpenGLWidgets', 'PySide6.Qt3DCore', 'PySide6.Qt3DRender',
    'PySide6.Qt3DInput', 'PySide6.Qt3DLogic', 'PySide6.Qt3DAnimation',
    'PySide6.Qt3DExtras',
]
OTHER_EXCLUDES = [
    'tkinter', 'unittest', 'pydoc', 'doctest', 'email', 'html', 'http',
    'xmlrpc', 'pdb', 'difflib', 'lib2to3',
    'pip', 'PIL', 'matplotlib', 'scipy', 'pandas',
    'test', 'idlelib', 'turtledemo', 'ensurepip', 'venv',
    # 音频转曲谱（内置 GAME ONNX）要用来解码与算矩阵：
    # numpy / soundfile / cffi 保留。miniaudio 不用。
    'miniaudio',
    # 这些是「本机装过但产品用不到」的大家伙：不排掉的话 PyInstaller 会把
    # 它们整个拖进产物（实测 949MB → 排掉后回到 80MB 级）。
    # torch / torchaudio / demucs 是命令行分离人声用的；librosa / numba /
    # llvmlite / sklearn / cv2 / transformers / onnx 是它们的依赖或同生态包。
    'torch', 'torchaudio', 'torchvision', 'demucs', 'librosa', 'numba',
    'llvmlite', 'sklearn', 'cv2', 'transformers', 'onnx', 'onnx2torch',
    'lightning', 'pytorch_lightning', 'torchmetrics', 'julius', 'dora',
    'einops', 'openunmix', 'lameenc', 'samplerate', 'resampy', 'audioread',
    'pooch', 'soxr', 'joblib', 'threadpoolctl',
    # 注意：distutils / setuptools **不能**排除。
    # Python 3.12+ 移除了标准库 distutils，PyInstaller 的 hook-distutils
    # 会把 setuptools._distutils 别名成 distutils；一旦这两个被排除，
    # 分析阶段就会抛 ValueError 直接构建失败。
]

# 需要从产物里剔除的文件（fnmatch 通配，路径已统一成 / 分隔）
SKIP_PATTERNS = [
    '*opengl32sw.dll',
    '*libcrypto-3*.dll',
    '*libssl-3*.dll',
    '*Qt6Network.dll',
    '*Qt6Svg.dll',
    '*Qt6Pdf.dll',
    '*Qt6Qml*.dll',
    '*Qt6Quick*.dll',
    '*QtNetwork.pyd',
    '*QtSvg*.pyd',
    '*QtPdf*.pyd',
    '*QtQml*.pyd',
    '*QtQuick*.pyd',
    '*translations/*',
    '*qml/*',
    '*plugins/imageformats/qtiff.dll',
    '*plugins/imageformats/qwebp.dll',
    '*plugins/imageformats/qtga.dll',
    '*plugins/imageformats/qicns.dll',
    '*plugins/imageformats/qwbmp.dll',
    '*plugins/imageformats/qpdf.dll',
    '*plugins/imageformats/qsvg.dll',
    '*plugins/iconengines/*',
    '*plugins/generic/*',
    '*plugins/tls/*',
    '*plugins/networkinformation/*',
    '*plugins/platforminputcontexts/*',
    '*plugins/platforms/qdirect2d.dll',
    '*plugins/platforms/qminimal.dll',
    '*plugins/platforms/qoffscreen.dll',
    '*plugins/styles/*',
    '*plugins/multimedia/*',
    '*plugins/printsupport/*',
]


#: 曲库认这些后缀（与 gtiharmonica.app.LIBRARY_EXTS 保持一致）
LIBRARY_EXTS = ('.json', '.mid', '.midi')


def _is_song(name: str) -> bool:
    """是不是一份曲目文件（曲库目录里的元数据是点开头的隐藏文件）。"""
    return not name.startswith('.') and name.lower().endswith(LIBRARY_EXTS)


def library_manifest(songs_dir: str) -> dict:
    """曲库快照：文件名 -> 字节数。用于构建前后的比对。

    只统计真正的曲目文件：曲库目录里还躺着 .deleted.txt 之类的元数据
    （删除名单、收藏名单、内置曲目安装标记），它们不该被算成曲目，
    更不该被「救出 / 恢复」环节当成用户曲目搬来搬去。
    """
    out = {}
    if not os.path.isdir(songs_dir):
        return out
    try:
        names = sorted(os.listdir(songs_dir))
    except OSError:
        return out
    for name in names:
        if not _is_song(name):
            continue
        path = os.path.join(songs_dir, name)
        try:
            if os.path.isfile(path):
                out[name] = os.path.getsize(path)
        except OSError:
            pass
    return out


#: 救出来的用户曲目在磁盘上的落脚点。放在 build/ 下（不是 dist/），
#: 所以不会被 PyInstaller 的重建删掉。
KEEP_DIR = os.path.join(BUILD, 'songs-keep')


def rescue_user_songs(prod_dir: str) -> dict:
    """把产物曲库里「开发曲库之外」的文件读进内存，并落盘备份。

    PyInstaller 的文件夹模式重建时会先整个删掉 dist/<NAME>
    （日志里的 "Removing dir ..."），而便携模式的曲库就在那里面 ——
    用户自己转谱 / 导入的曲目会连目录一起被删。所以构建前必须先把
    这些文件救出来，构建后再合并回去。

    除了产物目录，这里还会把 build/songs-keep/ 里历次救出来的文件
    一并纳入 —— 万一某一次恢复环节出了岔子，备份还在磁盘上，不会
    因为「这次没扫到」就把曲子彻底弄丢。
    """
    songs = os.path.join(prod_dir, 'songs')
    dev = os.path.join(ROOT, 'songs')
    dev_names = set(os.listdir(dev)) if os.path.isdir(dev) else set()

    out = {}

    # 1) 产物曲库里、开发曲库没有的（= 用户自己加的）
    if os.path.isdir(songs):
        for name in sorted(os.listdir(songs)):
            if not _is_song(name):
                continue
            path = os.path.join(songs, name)
            if not os.path.isfile(path) or name in dev_names:
                continue
            try:
                with open(path, 'rb') as fh:
                    out[name] = fh.read()
            except OSError:
                pass

    # 2) 历次构建留下的磁盘备份
    if os.path.isdir(KEEP_DIR):
        for name in sorted(os.listdir(KEEP_DIR)):
            if name in out or not _is_song(name):
                continue
            path = os.path.join(KEEP_DIR, name)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, 'rb') as fh:
                    out[name] = fh.read()
            except OSError:
                pass

    # 3) 立刻落盘：后面恢复环节就算出错，文件也还在
    if out:
        try:
            os.makedirs(KEEP_DIR, exist_ok=True)
            for name, data in out.items():
                with open(os.path.join(KEEP_DIR, name), 'wb') as fh:
                    fh.write(data)
        except OSError as exc:
            print('  ! 用户曲目备份写入失败：%s' % exc)

    return out


def verify_library(before: dict, after_dir: str, rescue: dict) -> list:
    """构建后校验曲库：构建前存在的曲子必须一首不少。

    返回缺失的文件名列表，空列表 = 通过。宁可让构建报错，也不要
    静默丢掉用户的曲子 —— 上一版就是日志写着「已恢复」而文件不在。
    """
    after = library_manifest(after_dir)
    missing = set()
    for name in before:
        if name not in after:
            missing.add(name)
    for name in rescue:
        if name not in after:
            missing.add(name)
    return sorted(missing)


def ensure_icon() -> str:
    """用 Pillow 把 大肥鲸洲琴工具包 品牌图标渲染成 .ico。

    设计与 gtiharmonica.gui.theme.make_icon（QPainter 版）逐参数对齐：
    黑金瓷砖底 + 香槟金口琴簧格 + 金色饰条；小尺寸减少簧格数量。
    """
    os.makedirs(BUILD, exist_ok=True)
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print('  ! 没有 Pillow，跳过图标（exe 将使用默认图标）')
        return ''

    # 用户素材优先：gtiharmonica/assets/app_icon.png（抠图 PNG）。
    # 裁切规则与 theme.make_icon 对齐：小尺寸只取头脸区域，居中补成正方形。
    asset = os.path.join(ROOT, 'gtiharmonica', 'assets', 'app_icon.png')
    if os.path.isfile(asset):
        base = Image.open(asset).convert('RGBA')
        sizes = [256, 128, 64, 48, 32, 24, 16]
        frames = []
        for s in sizes:
            im = base
            if s <= 32:
                w, h = base.size
                im = base.crop((0, 0, w, int(h * 0.72)))
            w, h = im.size
            side = max(w, h)
            canvas = Image.new('RGBA', (side, side), (0, 0, 0, 0))
            canvas.paste(im, ((side - w) // 2, (side - h) // 2))
            frames.append(canvas.resize((s, s), Image.LANCZOS))
        frames[0].save(ICON, format='ICO',
                       sizes=[(im.width, im.height) for im in frames],
                       append_images=frames[1:])
        print('  图标已生成（用户素材 app_icon.png）')
        return ICON

    def lerp(a, b, t):
        return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(4))

    def vgrad(w, h, top, bottom):
        img = Image.new('RGBA', (w, h))
        px = img.load()
        for y in range(h):
            c = lerp(top, bottom, y / max(h - 1, 1))
            for x in range(w):
                px[x, y] = c
        return img

    def render(base: int) -> Image.Image:
        small = base <= 48
        s = base / 256.0
        mask = Image.new('L', (base, base), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [int(2 * s), int(2 * s), int(base - 2 * s), int(base - 2 * s)],
            radius=int(base * 0.225), fill=255)
        img = Image.new('RGBA', (base, base), (0, 0, 0, 0))
        img.paste(vgrad(base, base, (36, 36, 40, 255), (14, 15, 18, 255)),
                  (0, 0), mask)
        d = ImageDraw.Draw(img)
        if not small:
            d.rounded_rectangle(
                [int(2 * s), int(2 * s), int(base - 2 * s), int(base - 2 * s)],
                radius=int(base * 0.225),
                outline=(232, 197, 126, 70), width=max(int(2 * s), 1))

        bx0, bx1 = int(34 * s), int(222 * s)
        by0, by1 = ((88 * s, 160 * s) if small else (82 * s, 154 * s))
        body = vgrad(bx1 - bx0, int(by1 - by0),
                     (240, 209, 144, 255), (201, 166, 84, 255))
        bmask = Image.new('L', (bx1 - bx0, int(by1 - by0)), 0)
        ImageDraw.Draw(bmask).rounded_rectangle(
            [0, 0, bx1 - bx0 - 1, int(by1 - by0) - 1],
            radius=int(16 * s), fill=255)
        img.paste(body, (bx0, int(by0)), bmask)
        d = ImageDraw.Draw(img)

        n = 5 if small else 8
        sw = (202 * s - 54 * s) / n
        slot_w = sw * (0.62 if small else 0.52)
        sy0, sy1 = ((102 * s, 146 * s) if small else (100 * s, 136 * s))
        for i in range(n):
            cx = 54 * s + sw * (i + 0.5)
            d.rounded_rectangle([cx - slot_w / 2, sy0, cx + slot_w / 2, sy1],
                                radius=int((5 if small else 6) * s),
                                fill=(18, 18, 22, 255))
        if not small:
            d.rounded_rectangle([48 * s, 89 * s, 208 * s, 92 * s],
                                radius=int(2 * s), fill=(255, 255, 255, 95))

        gy0, gy1 = (178 * s, 196 * s) if small else (172 * s, 186 * s)
        d.rounded_rectangle([34 * s, gy0, 222 * s, gy1],
                            radius=int((9 if small else 7) * s),
                            fill=(238, 201, 137, 255))
        return img

    sizes = [256, 128, 64, 48, 32, 16]
    render(256).save(ICON, sizes=[(n, n) for n in sizes])
    print('  图标已生成')
    return ICON


def app_version() -> str:
    """程序版本号。唯一来源是 gtiharmonica/__init__.py 的 __version__。"""
    import re
    src = os.path.join(ROOT, 'gtiharmonica', '__init__.py')
    try:
        with open(src, encoding='utf8') as fh:
            m = re.search(r"__version__\s*=\s*'([^']+)'", fh.read())
        return m.group(1) if m else '0.0'
    except OSError:
        return '0.0'


def write_version_file() -> str:
    path = os.path.join(BUILD, 'version_info.txt')
    ver = app_version()
    # Windows 的版本资源要 4 段整数：'1.10' -> (1, 10, 0, 0) / '1.10.0.0'
    parts = [p if p.isdigit() else '0'
             for p in (ver.split('.') + ['0', '0', '0', '0'])[:4]]
    quad = ', '.join(parts)
    dotted = '.'.join(parts)
    with open(path, 'w', encoding='utf8') as fh:
        fh.write("""
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(%s), prodvers=(%s),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('CompanyName', '大肥鲸洲琴工具包'),
        StringStruct('FileDescription', '三角洲行动口琴自动演奏'),
        StringStruct('FileVersion', '%s'),
        StringStruct('InternalName', '大肥鲸洲琴工具包'),
        StringStruct('LegalCopyright', 'Free for personal use'),
        StringStruct('OriginalFilename', '大肥鲸洲琴工具包.exe'),
        StringStruct('ProductName', '大肥鲸洲琴工具包'),
        StringStruct('ProductVersion', '%s')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""" % (quad, quad, dotted, dotted))
    return path


def write_spec(onefile: bool, console: bool, icon: str, version: str,
               full: bool) -> str:
    """生成 spec，并在其中过滤掉不需要的 Qt 组件。"""
    excludes = '[]' if full else repr(QT_EXCLUDES + OTHER_EXCLUDES)
    skip = '[]' if full else repr(SKIP_PATTERNS)
    icon_line = "icon=%r," % icon if icon else ''
    version_line = "version=%r," % version if version and os.path.exists(version) else ''
    launcher = os.path.join(ROOT, 'launcher.py')

    asset_dir = os.path.join(ROOT, 'gtiharmonica', 'assets')
    common = """# -*- mode: python ; coding: utf-8 -*-
# 本文件由 build.py 自动生成，请勿手工修改。
import fnmatch

a = Analysis(
    [r'{launcher}'],
    pathex=[r'{root}'],
    datas=[(r'{asset_dir}', 'gtiharmonica/assets')],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes={excludes},
    noarchive=False,
    optimize=0,
)

_SKIP = {skip}


def _keep(name):
    n = name.replace('\\\\', '/')
    base = n.rsplit('/', 1)[-1]
    for pat in _SKIP:
        if fnmatch.fnmatch(n, pat) or fnmatch.fnmatch(base, pat):
            return False
    return True


_dropped = [x[0] for x in a.binaries + a.datas if not _keep(x[0])]
a.binaries = [x for x in a.binaries if _keep(x[0])]
a.datas = [x for x in a.datas if _keep(x[0])]
print('裁剪掉 %d 个文件' % len(_dropped))

pyz = PYZ(a.pure)
""".format(launcher=launcher, root=ROOT, excludes=excludes, skip=skip,
           asset_dir=asset_dir)

    if onefile:
        body = """
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='{name}',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    runtime_tmpdir=None, console={console}, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None,
    entitlements_file=None, uac_admin=False, {icon}{version}
)
""".format(name=NAME, console=console, icon=icon_line, version=version_line,
                  asset_dir=asset_dir)
    else:
        body = """
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='{name}',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console={console}, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None,
    entitlements_file=None, uac_admin=False, {icon}{version}
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name='{name}',
)
""".format(name=NAME, console=console, icon=icon_line, version=version_line,
                  asset_dir=asset_dir)

    with open(SPEC, 'w', encoding='utf8') as fh:
        fh.write(common + body)
    return SPEC


def print_tree(path: str, limit: int = 12) -> None:
    if not os.path.isdir(path):
        return
    items = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        size = os.path.getsize(full) if os.path.isfile(full) else 0
        items.append((size, name, os.path.isdir(full)))
    items.sort(reverse=True)
    print('  目录内容:')
    for size, name, isdir in items[:limit]:
        print('    %s%-38s %s' % ('DIR ' if isdir else '    ', name,
                                  '%.1f MB' % (size / 1048576) if size else ''))


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description='构建 大肥鲸洲琴工具包')
    ap.add_argument('--onefile', action='store_true', help='产出单个 exe')
    ap.add_argument('--clean', action='store_true', help='构建前清理旧产物')
    ap.add_argument('--console', action='store_true', help='保留控制台窗口（排查用）')
    ap.add_argument('--full', action='store_true', help='不做裁剪，保留全部 Qt 组件')
    ap.add_argument('--no-songs', action='store_true',
                    help='不同步开发曲库（对外分发时用，避免带上第三方编曲）')
    args = ap.parse_args()

    if args.clean:
        shutil.rmtree(os.path.join(BUILD, 'pyi'), ignore_errors=True)
        shutil.rmtree(DIST, ignore_errors=True)
        print('已清理旧产物')

    os.makedirs(BUILD, exist_ok=True)
    icon = ensure_icon()
    version = write_version_file()
    spec = write_spec(args.onefile, args.console, icon, version, args.full)

    # PyInstaller 会重建整个产物目录，先把用户自己加进便携曲库的曲目救出来
    prod_songs = os.path.join(DIST, NAME, 'songs')
    before = library_manifest(prod_songs)
    if before:
        print('  构建前曲库快照：%d 首' % len(before))
    rescue = rescue_user_songs(os.path.join(DIST, NAME))
    if rescue:
        print('  检测到产物曲库里 %d 个用户曲目，构建后自动恢复：%s'
              % (len(rescue), '、'.join(sorted(rescue)[:3])
                 + ('…' if len(rescue) > 3 else '')))

    print('\n开始构建：%s%s'
          % ('单文件' if args.onefile else '文件夹',
             '（未裁剪）' if args.full else '（已裁剪 Qt 组件）'))
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
           '--distpath', DIST, '--workpath', os.path.join(BUILD, 'pyi'), spec]
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print('\n构建失败，退出码 %d' % result.returncode)
        return result.returncode

    target_dir = DIST if args.onefile else os.path.join(DIST, NAME)
    os.makedirs(target_dir, exist_ok=True)
    for doc in ('README.md', '使用说明.txt', '创建快捷方式.bat'):
        src = os.path.join(ROOT, doc)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(target_dir, doc))

    # 把开发曲库同步到产物旁边，这样重新构建不会丢曲子。
    # 准备对外分发时加 --no-songs，产物里就只会有运行时自动生成的内置曲目。
    lib_src = os.path.join(ROOT, 'songs')
    if args.no_songs:
        print('  跳过曲库同步（--no-songs）')
    elif os.path.isdir(lib_src):
        lib_dst = os.path.join(target_dir, 'songs')
        shutil.copytree(lib_src, lib_dst, dirs_exist_ok=True)
        count = len([n for n in os.listdir(lib_dst) if _is_song(n)])
        print('  已同步曲库：%d 首 → %s' % (count, lib_dst))

    # 把构建前救出来的用户曲目合并回去（只在开发曲库里没有同名文件时才写）
    if rescue:
        lib_dst = os.path.join(target_dir, 'songs')
        os.makedirs(lib_dst, exist_ok=True)
        restored = []
        for name, data in rescue.items():
            path = os.path.join(lib_dst, name)
            if os.path.exists(path):
                continue
            try:
                with open(path, 'wb') as fh:
                    fh.write(data)
                restored.append(name)
            except OSError:
                pass
        if restored:
            print('  已恢复用户曲目 %d 个：%s'
                  % (len(restored), '、'.join(sorted(restored)[:3])
                     + ('…' if len(restored) > 3 else '')))
        else:
            print('  用户曲目都已在开发曲库中，无需恢复')

    # 校验：构建前存在的曲子，构建后必须一首不少。宁可构建失败，
    # 也不要让「日志说恢复了、其实文件不在」再发生一次。
    if not args.no_songs:
        missing = verify_library(before, os.path.join(target_dir, 'songs'),
                                 rescue)
        if missing:
            print('\n! 曲库校验未通过：%d 首曲子丢失' % len(missing))
            for name in missing[:20]:
                print('    - %s' % name)
            if len(missing) > 20:
                print('    …还有 %d 首' % (len(missing) - 20))
            print('  备份位置：%s' % KEEP_DIR)
            print('  请先把上列文件补回 %s 再重新构建。'
                  % os.path.join(target_dir, 'songs'))
            return 1
        after_count = len(library_manifest(os.path.join(target_dir, 'songs')))
        print('  曲库校验通过：%d 首，构建前 %d 首全部在位'
              % (after_count, len(before)))

    print('\n构建完成')
    if args.onefile:
        exe = os.path.join(DIST, NAME + '.exe')
        if os.path.exists(exe):
            print('  产物：%s  (%.1f MB)' % (exe, os.path.getsize(exe) / 1048576))
    else:
        folder = os.path.join(DIST, NAME)
        print('  产物：%s  (合计 %.1f MB)' % (folder, dir_size(folder) / 1048576))
        print_tree(folder)
    return 0


if __name__ == '__main__':
    sys.exit(main())
