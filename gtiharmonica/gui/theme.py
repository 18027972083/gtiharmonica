"""大肥鲸洲琴工具包 双主题：浅色（默认，白+蓝）与深色（高雅黑+金）。

配色体系：高雅黑 × 香槟金 / 典雅白 × 深金。
底色全部压到近中性的石墨灰（黑金配的高级感来自低饱和底色 +
高饱和点缀的强对比），强调色族统一走金 —— 品牌、按钮、卷帘音符、
键位高亮、选中态全部同源，任何一处跳色都会破坏整体感。

配色以调色板对象 C 为中心：所有 token 都挂在同一个对象上，
load() 就地替换属性 —— 已经 import 过 C 的自绘控件在下一帧重绘时
自动拿到新主题色，不需要重建窗口。QSS 则是 $TOKEN 模板按当前
调色板展开，切换时整张重设。
"""
from __future__ import annotations

import math
import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPalette, QPen, QPixmap, QPolygonF)

#: 主题模式：dark / light / auto（跟随系统，Qt 6.5+）
MODES = ('dark', 'light', 'auto')
MODE_NAMES = {'dark': '深色', 'light': '浅色', 'auto': '跟随系统'}

DARK = {
    'WINDOW': '#0e0f12', 'SIDEBAR': '#131418', 'CARD': '#181920',
    'INPUT': '#101116', 'POPUP': '#1c1d24',
    'BORDER': '#2a2b33', 'BORDER_SOFT': '#242529', 'FIELD_BORDER': '#34353f',
    'BRAND': '#e8c57e', 'ACCENT': '#e8c57e', 'ACCENT_HOVER': '#f2d494',
    'ACCENT_DEEP': '#2e2415', 'TRACK_FILL': '#d9b36a', 'KNOB': '#f2dca6',
    'SELECT_BG': '#382e18', 'SELECT_FG': '#f0d9a0',
    'TEXT': '#eaecef', 'MUTED': '#9b9ca4', 'DISABLED': '#71727a',
    'STATUS': '#e8cf96',
    'BTN': '#242630', 'BTN_BORDER': '#33343f', 'BTN_HOVER': '#31333f',
    'BTN_HOVER_BORDER': '#8a7440', 'BTN_PRESSED': '#1a1b22',
    'HOVER': '#22242c', 'CONTROL_HOVER': '#1e2028', 'CONTROL_HOVER_2': '#282a34',
    'SEG_CHECKED_BG': '#2a2b34', 'SEG_CHECKED_FG': '#eccf8e',
    'BTN_DISABLED_BG': '#1e1f26', 'BTN_DISABLED_BORDER': '#272831',
    'PRIMARY_TEXT': '#241a05', 'PRIMARY_SOLID': '#e8c57e',
    'PRIMARY_SOLID_H': '#f0d190', 'PRIMARY_PRESSED': '#cfa654',
    'PRIMARY_TOP': '#f0d494',
    'PRIMARY_BOTTOM': '#d9b36a', 'PRIMARY_TOP_H': '#f6dda2',
    'PRIMARY_BOTTOM_H': '#e2bd77',
    'PRIMARY_DISABLED': '#6b675c', 'PRIMARY_DISABLED_BG': '#232429',
    'DANGER': '#f5c1bd', 'DANGER_BG': '#4a3038', 'PANEL_GREY': '#33343e',
    'WARN_BG': '#2a2318', 'WARN_BORDER': '#6b5330', 'WARN_FG': '#eec989',
    'STOP_HOVER_BG': '#3a2a30', 'STOP_HOVER_BORDER': '#7a4a52',
    'COMBO_SEL': '#4a3d1e', 'MENU_SEL': '#33342e',
    'SLIDER_GROOVE': '#2c2d36', 'SLIDER_HANDLE': '#f0d190',
    'SLIDER_HANDLE_BORDER': '#3d3115', 'SLIDER_PRESSED': '#ffffff',
    'PROGRESS_BG': '#2c2d36',
    'SCROLL': '#31323c', 'SCROLL_HOVER': '#43454f',
    'TOOLTIP_BG': '#26272f', 'TOOLTIP_BORDER': '#4a4b55', 'TOOLTIP_FG': '#f0f0f2',
    'SPLITTER': '#1a1b21',
    'ROLL_BG': '#121318', 'ROLL_GRID': '#23242c', 'ROLL_GRID_STRONG': '#26272f',
    'ROLL_LABEL': '#8b8c96', 'ROLL_NOTE': '#f2ddab', 'ROLL_NOTE_DIM': '#d9b36a',
    'ROLL_NOTE_MUTED': '#4a4128', 'ROLL_PLAYHEAD': '#eec989',
    'ROLL_CURSOR': '#f0f0f2',
    # 乐谱编辑器（编辑视图网格与选区）
    'EDIT_BAR': '#383947', 'EDIT_BAR_NUM': '#7d7e88', 'EDIT_BEAT': '#252630',
    'EDIT_TIE': '#96979f', 'EDIT_RANGE_FILL': '#e88f88',
    'EDIT_RANGE_EDGE': '#b0574f',
    # 键位条（8 个音阶键的可视化）
    'KEY_BG': '#212228', 'KEY_BORDER': '#2a2b33', 'KEY_ACTIVE': '#e8c57e',
    'KEY_TEXT': '#eceef2', 'KEY_ACTIVE_DEEP': '#241a05', 'KEY_HINT_ON': '#4a3c1a',
}

LIGHT = {
    # 白 + 蓝：与图标（蓝发少女）相映。底色是偏冷的干净白，
    # 强调色取图标发色的同源蓝，按钮、卷帘音符、键位高亮整体统一。
    'WINDOW': '#f2f4f9', 'SIDEBAR': '#e9edf4', 'CARD': '#ffffff',
    'INPUT': '#f9fafd', 'POPUP': '#ffffff',
    'BORDER': '#cfd6e2', 'BORDER_SOFT': '#dde3ec', 'FIELD_BORDER': '#b8c3d6',
    'BRAND': '#2f68d0', 'ACCENT': '#2f68d0', 'ACCENT_HOVER': '#275bb8',
    'ACCENT_DEEP': '#e2ecfb', 'TRACK_FILL': '#4a82e0', 'KNOB': '#24509e',
    'SELECT_BG': '#e1ebfc', 'SELECT_FG': '#1d4ea6',
    'TEXT': '#1b2230', 'MUTED': '#66748a', 'DISABLED': '#9fabbd',
    'STATUS': '#24509e',
    'BTN': '#ffffff', 'BTN_BORDER': '#c9d1df', 'BTN_HOVER': '#f0f4fb',
    'BTN_HOVER_BORDER': '#8aa6d4', 'BTN_PRESSED': '#e2e8f3',
    'HOVER': '#edf2fa', 'CONTROL_HOVER': '#eff4fb', 'CONTROL_HOVER_2': '#e0e7f3',
    'SEG_CHECKED_BG': '#e1ebfc', 'SEG_CHECKED_FG': '#1d4ea6',
    'BTN_DISABLED_BG': '#eff2f7', 'BTN_DISABLED_BORDER': '#d8dee9',
    'PRIMARY_TEXT': '#ffffff', 'PRIMARY_SOLID': '#2f68d0',
    'PRIMARY_SOLID_H': '#2a5cbd', 'PRIMARY_PRESSED': '#234e9f',
    'PRIMARY_TOP': '#4a82e0',
    'PRIMARY_BOTTOM': '#2f68d0', 'PRIMARY_TOP_H': '#5b90e8',
    'PRIMARY_BOTTOM_H': '#2a5cbd',
    'PRIMARY_DISABLED': '#a7b2c4', 'PRIMARY_DISABLED_BG': '#e8ecf3',
    'DANGER': '#b23f3a', 'DANGER_BG': '#fbe9e8', 'PANEL_GREY': '#dbe2ec',
    'WARN_BG': '#fdf3e0', 'WARN_BORDER': '#d9b878', 'WARN_FG': '#9a6b0d',
    'STOP_HOVER_BG': '#f7e9e9', 'STOP_HOVER_BORDER': '#cf9a9d',
    'COMBO_SEL': '#e1ebfc', 'MENU_SEL': '#ecf2fd',
    'SLIDER_GROOVE': '#d7dde8', 'SLIDER_HANDLE': '#ffffff',
    'SLIDER_HANDLE_BORDER': '#24509e', 'SLIDER_PRESSED': '#24509e',
    'PROGRESS_BG': '#dde3ee',
    'SCROLL': '#c6cedd', 'SCROLL_HOVER': '#adb8cb',
    'TOOLTIP_BG': '#ffffff', 'TOOLTIP_BORDER': '#bfc9da', 'TOOLTIP_FG': '#1b2230',
    'SPLITTER': '#dfe4ee',
    'ROLL_BG': '#fcfdff', 'ROLL_GRID': '#e9eef7', 'ROLL_GRID_STRONG': '#dee5f1',
    'ROLL_LABEL': '#7d8ba0', 'ROLL_NOTE': '#2f68d0', 'ROLL_NOTE_DIM': '#9dbcec',
    'ROLL_NOTE_MUTED': '#d9e3f4', 'ROLL_PLAYHEAD': '#e0940f',
    'ROLL_CURSOR': '#1b2230',
    'EDIT_BAR': '#c6cfdf', 'EDIT_BAR_NUM': '#7d8ba0', 'EDIT_BEAT': '#e9eef7',
    'EDIT_TIE': '#93a1b6', 'EDIT_RANGE_FILL': '#f2c4c0',
    'EDIT_RANGE_EDGE': '#c2645d',
    'KEY_BG': '#ffffff', 'KEY_BORDER': '#c9d1df', 'KEY_ACTIVE': '#2f68d0',
    'KEY_TEXT': '#1b2230', 'KEY_ACTIVE_DEEP': '#ffffff', 'KEY_HINT_ON': '#e2ecfb',
}
TABLES = {'dark': DARK, 'light': LIGHT}


class _Palette:
    """当前主题的调色板 token 集合。属性随 load() 就地替换。"""

    mode = 'dark'

    def load(self, table: dict, mode: str) -> None:
        self.mode = mode
        self.__dict__.update(table)


C = _Palette()
C.load(DARK, 'dark')


def system_scheme() -> str:
    """读 Windows 系统应用主题（注册表，不受 Qt 内部状态影响）。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            value, _ = winreg.QueryValueEx(key, 'AppsUseLightTheme')
        return 'light' if value else 'dark'
    except Exception:
        return 'dark'


def resolve_mode(mode: str) -> str:
    """auto -> 按 Windows 当前应用主题判定；其余原样返回。"""
    if mode != 'auto':
        return mode if mode in TABLES else 'dark'
    return system_scheme()


def build_qss() -> str:
    out = QSS_TEMPLATE
    # 必须按 token 名长度降序替换：$BTN 是 $BTN_HOVER 的前缀，
    # 顺序错了会把长名替换成 '#263247_HOVER' 这种非法颜色 —— 而 Qt 的
    # QSS 解析器碰到非法值会直接中止，整张表的后半部分全部静默失效
    # （表现为主按钮、ghost、分段控件全部退回默认样式）。
    for key in sorted(DARK, key=len, reverse=True):
        out = out.replace('$' + key, getattr(C, key))
    leftover = out[out.find('$'):]
    if '$' in out:
        raise ValueError('QSS 模板里存在未定义的 token: %s' % leftover[:30])
    return out


def build_palette() -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(C.WINDOW))
    pal.setColor(QPalette.WindowText, QColor(C.TEXT))
    pal.setColor(QPalette.Base, QColor(C.INPUT))
    pal.setColor(QPalette.AlternateBase, QColor(C.CARD))
    pal.setColor(QPalette.Text, QColor(C.TEXT))
    pal.setColor(QPalette.Button, QColor(C.BTN))
    pal.setColor(QPalette.ButtonText, QColor(C.TEXT))
    pal.setColor(QPalette.BrightText, QColor(C.DANGER))
    pal.setColor(QPalette.Highlight, QColor(C.SELECT_BG))
    pal.setColor(QPalette.HighlightedText, QColor(C.SELECT_FG))
    pal.setColor(QPalette.ToolTipBase, QColor(C.POPUP))
    pal.setColor(QPalette.ToolTipText, QColor(C.TEXT))
    pal.setColor(QPalette.PlaceholderText, QColor(C.DISABLED))
    pal.setColor(QPalette.Link, QColor(C.ACCENT))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(C.DISABLED))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(C.DISABLED))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(C.DISABLED))
    return pal


def apply_theme(app, mode: str) -> str:
    """按模式应用主题，返回实际生效的模式（auto 解析后）。"""
    effective = resolve_mode(mode)
    C.load(TABLES[effective], effective)
    app.setStyleSheet(build_qss())
    app.setPalette(build_palette())
    return effective


def dark_palette():
    """兼容旧调用（app.py 历史入口），等同 build_palette。"""
    return build_palette()


QSS_TEMPLATE = """
QWidget {
    color: $TEXT;
    font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif;
    font-size: 13px;
}
QMainWindow, QDialog { background: $WINDOW; }

QFrame#sidebar { background: $SIDEBAR; border-right: 1px solid $BORDER_SOFT; }
QFrame#card { background: $CARD; border: 1px solid $BORDER; border-radius: 14px; }
QFrame#controlBar { background: $SIDEBAR; border-top: 1px solid $BORDER_SOFT; }
QFrame#separator { background: $BORDER_SOFT; max-height: 1px; border: 0; }
QFrame#vsep { background: $BORDER_SOFT; max-width: 1px; border: 0; }

QLabel#brand { font-size: 20px; font-weight: 700; color: $BRAND; }
QLabel#eyebrow { color: $ACCENT; font-size: 11px; font-weight: 600; }
QLabel#title { font-size: 31px; font-weight: 700; color: $TEXT; }
QLabel#muted { color: $MUTED; }
QLabel#section { font-weight: 600; font-size: 15px; color: $TEXT; }
QLabel#status { color: $STATUS; }
QLabel#keyHint { font-size: 11px; color: $MUTED; }
QLabel#mono { font-family: Consolas, monospace; color: $MUTED; }

/* 兼容旧命名。注意：Qt 的 QSS 只认这样的块注释，行首裸 # 会被
   当成 ID 选择器吞掉下一条规则并中止整张表解析 —— 全部后续样式静默失效。 */
QLabel#panelTitle { font-weight: 600; font-size: 15px; color: $TEXT; }
QLabel#dim { color: $MUTED; }
QLabel#paramValue { color: $ACCENT; font-family: Consolas, monospace; }
QLabel#infoBar {
    background: $INPUT; border: 1px solid $BORDER; border-radius: 9px;
    padding: 9px 12px; color: $MUTED;
}
QLabel#warnBox {
    background: $WARN_BG; border: 1px solid $WARN_BORDER; border-radius: 8px;
    padding: 10px 12px; color: $WARN_FG;
}

QPushButton {
    background: $BTN; border: 1px solid $BTN_BORDER;
    border-radius: 10px; padding: 9px 15px; color: $TEXT;
}
QPushButton:hover { background: $BTN_HOVER; border-color: $BTN_HOVER_BORDER; }
QPushButton:pressed { background: $BTN_PRESSED; }
QPushButton:disabled { color: $DISABLED; background: $BTN_DISABLED_BG; border-color: $BTN_DISABLED_BORDER; }

/* 主按钮：纯色扁平。渐变是上一代的做法 —— 现代界面靠颜色分量和
   面积对比来确立「这是主操作」，不再靠立体感。 */
QPushButton#primary {
    color: $PRIMARY_TEXT; font-weight: 700; border: 0; border-radius: 11px;
    background: $PRIMARY_SOLID;
}
QPushButton#primary:hover { background: $PRIMARY_SOLID_H; }
QPushButton#primary:pressed { background: $PRIMARY_PRESSED; }
QPushButton#primary:disabled { color: $PRIMARY_DISABLED; background: $PRIMARY_DISABLED_BG; }

QPushButton#stop { color: $DANGER; }
QPushButton#stop:hover { background: $STOP_HOVER_BG; border-color: $STOP_HOVER_BORDER; }

/* 次级动作：安静的「幽灵」按钮 —— 无底无边，悬停才浮出底色，
   一排按钮时视线自动落在有底色的那个上。 */
QPushButton#ghost { background: transparent; border-color: transparent; color: $MUTED; }
QPushButton#ghost:hover { background: $HOVER; border-color: transparent; color: $TEXT; }
QPushButton#ghost:pressed { background: $BTN_PRESSED; color: $TEXT; }

/* 顶部动作条：按钮收小一号，和主区分层级 */
QFrame#topBar { background: transparent; }
QFrame#topBar QPushButton { padding: 8px 13px; border-radius: 9px; font-size: 12px; }

/* 顶栏图标按钮（主题切换 / 设置齿轮） */
QToolButton#iconBtn {
    background: transparent; border: 1px solid transparent;
    border-radius: 9px; padding: 5px 7px;
}
QToolButton#iconBtn:hover { background: $HOVER; border-color: $BORDER; }
QToolButton#iconBtn:pressed { background: $BTN_PRESSED; }
QToolButton#iconBtn::menu-indicator { image: none; width: 0; height: 0; }

/* 编辑器工具行的动作按钮很密，用小一号的内边距，一排才放得下 */
QFrame#editBar QPushButton { padding: 7px 11px; }

/* ---- 分段控件：编排预览 / 乐谱编辑 ---- */
QFrame#segGroup {
    background: $SIDEBAR; border: 1px solid $BORDER_SOFT; border-radius: 11px;
}
QPushButton#seg {
    background: transparent; border: 0; border-radius: 8px;
    padding: 6px 16px; color: $MUTED; font-weight: 600;
}
QPushButton#seg:hover { color: $TEXT; background: $HOVER; }
QPushButton#seg:checked { background: $BTN; color: $SEG_CHECKED_FG; font-weight: 700; }

/* ---- 曲库筛选小按钮：全部 / 收藏 ---- */
QPushButton#chip {
    background: transparent; border: 1px solid $BORDER_SOFT; border-radius: 7px;
    padding: 2px 10px; font-size: 11px; color: $MUTED; font-weight: 600;
}
QPushButton#chip:hover { color: $TEXT; background: $HOVER; border-color: $BORDER; }
QPushButton#chip:checked {
    background: $BTN; color: $SEG_CHECKED_FG;
    border-color: $BORDER; font-weight: 700;
}

/* ---- 输入控件：纯色块，无渐变无阴影 ---- */
QLineEdit, QPlainTextEdit, QTextBrowser {
    background: $INPUT; border: 1px solid $FIELD_BORDER;
    border-radius: 8px; padding: 8px 10px;
}
QComboBox, QSpinBox, QDoubleSpinBox {
    background: $INPUT; border: 1px solid $FIELD_BORDER;
    border-radius: 8px; padding: 7px 10px;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextBrowser:focus,
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: $ACCENT;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
QDoubleSpinBox:disabled { color: $DISABLED; }

QComboBox::drop-down {
    subcontrol-origin: border; subcontrol-position: top right;
    width: 26px; border: 0; background: transparent;
}
/* 必须把 down-arrow 也显式声明掉（image: none）。只改 ::drop-down
   而不动 ::down-arrow 的话，Qt 会判定这个控件没被完全样式化，
   于是整个回退到原生绘制，background 也就跟着被忽略 —— 表现为
   下拉框在深色界面里是一块浅灰渐变，怎么改 background 都没用。 */
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QComboBox::down-arrow:on { image: none; }
QComboBox::drop-down:hover { background: $CONTROL_HOVER; }
QComboBox QAbstractItemView {
    background: $POPUP; border: 1px solid $FIELD_BORDER; border-radius: 8px;
    selection-background-color: $COMBO_SEL; selection-color: $SELECT_FG; outline: 0;
}
QSpinBox::up-button, QSpinBox::down-button {
    subcontrol-origin: border; width: 20px; border: 0; background: $CONTROL_HOVER;
}
QSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: 8px; }
QSpinBox::down-button { subcontrol-position: bottom right; border-bottom-right-radius: 8px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: $CONTROL_HOVER_2; }

/* ---- 滑块：细轨道 + 描边圆柄。描边是为了让浅色柄在亮暗两种
   背景上都有一圈边界；按下去变白给一个明确的「抓稳了」反馈。 ---- */
QSlider { background: transparent; }
QSlider::groove:horizontal { height: 5px; border-radius: 2px; background: $SLIDER_GROOVE; }
QSlider::sub-page:horizontal { background: $TRACK_FILL; border-radius: 2px; }
QSlider::handle:horizontal {
    background: $SLIDER_HANDLE; width: 15px; margin: -5px 0; border-radius: 8px;
    border: 1px solid $SLIDER_HANDLE_BORDER;
}
QSlider::handle:horizontal:hover { background: $ROLL_NOTE; }
QSlider::handle:horizontal:pressed { background: $SLIDER_PRESSED; }

QGroupBox {
    border: 1px solid $BORDER; border-radius: 12px;
    margin-top: 12px; padding: 14px 10px 10px 10px;
    font-weight: 600; color: $MUTED; background: $CARD;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }

QListWidget { background: transparent; border: 0; outline: 0; }
QListWidget::item { border-radius: 9px; padding: 15px 12px; margin-bottom: 5px; }
QListWidget::item:selected { background: $SELECT_BG; color: $SELECT_FG; }
QListWidget::item:hover { background: $HOVER; }

QTableWidget {
    background: $INPUT; border: 1px solid $BORDER; border-radius: 9px;
    gridline-color: $HOVER; outline: 0;
}
QTableWidget::item { padding: 5px 7px; }
QTableWidget::item:selected { background: $SELECT_BG; color: $SELECT_FG; }
QHeaderView::section {
    background: $POPUP; color: $MUTED; border: 0;
    border-right: 1px solid $HOVER; border-bottom: 1px solid $BORDER;
    padding: 8px; font-weight: 600;
}

QProgressBar {
    background: $PROGRESS_BG; border: 0; border-radius: 3px;
    min-height: 6px; max-height: 6px;
}
QProgressBar::chunk { background: $SEG_CHECKED_FG; border-radius: 3px; }

QCheckBox { spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px; border: 1px solid $BTN_BORDER;
    border-radius: 4px; background: $INPUT;
}
QCheckBox::indicator:checked { background: $ACCENT; border-color: $ACCENT; }
QCheckBox::indicator:hover { border-color: $BTN_HOVER_BORDER; }

QToolBar {
    background: $SIDEBAR; border-bottom: 1px solid $BORDER_SOFT; padding: 6px 10px; spacing: 4px;
}
QToolBar QToolButton {
    background: transparent; border: 1px solid transparent;
    border-radius: 8px; padding: 7px 13px; color: $TEXT;
}
QToolBar QToolButton:hover { background: $HOVER; border-color: $BTN_BORDER; }
QToolBar QToolButton:pressed { background: $BTN_PRESSED; }
QToolBar QToolButton:disabled { color: $DISABLED; }
QToolBar::separator { background: $BORDER_SOFT; width: 1px; margin: 5px 7px; }

QStatusBar { background: $SIDEBAR; border-top: 1px solid $BORDER_SOFT; color: $MUTED;
    padding: 4px 10px 8px; min-height: 30px; }
QStatusBar::item { border: none; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: $SCROLL; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $SCROLL_HOVER; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: $SCROLL; border-radius: 5px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: $SCROLL_HOVER; }

QSplitter::handle { background: $SPLITTER; }
QSplitter::handle:horizontal { width: 1px; }

QToolTip {
    background: $TOOLTIP_BG; border: 1px solid $TOOLTIP_BORDER; color: $TOOLTIP_FG;
    padding: 6px 8px; border-radius: 6px;
}
/* 「⋯」下拉菜单：不写的话 Qt 给的是白底黑字原生菜单，
   是整个深色界面里最破功的一块。 */
QMenu {
    background: $POPUP; border: 1px solid $FIELD_BORDER; border-radius: 10px;
    padding: 6px 5px;
}
QMenu::item {
    background: transparent; color: $TEXT;
    padding: 8px 26px 8px 14px; border-radius: 7px; margin: 1px 3px;
}
QMenu::item:selected { background: $MENU_SEL; color: $SELECT_FG; }
QMenu::item:disabled { color: $DISABLED; }
QMenu::separator { height: 1px; background: $BORDER_SOFT; margin: 5px 9px; }
QMenu::icon { margin-left: 6px; }
QMessageBox { background: $CARD; }
QDialogButtonBox QPushButton { min-width: 80px; }
QScrollArea { background: transparent; border: 0; }
QScrollArea > QWidget > QWidget { background: transparent; }
"""

def combo_style() -> str:
    """下拉框的控件级样式表（按当前主题取色）。

    为什么不能只靠全局 QSS：PySide6 上 QComboBox 即使写了 background，
    仍会回落到原生渐变绘制，而同一条规则里的 QSpinBox 却是好的。
    挂在控件自身才有足够优先级。切主题后需要重新 setStyleSheet。
    """
    return """
QComboBox {
    background: %(INPUT)s; border: 1px solid %(FIELD_BORDER)s;
    border-radius: 8px; padding: 7px 10px; color: %(TEXT)s;
}
QComboBox:hover { border-color: %(BTN_HOVER_BORDER)s; }
QComboBox:focus { border-color: %(ACCENT)s; }
QComboBox::drop-down { border: 0; width: 26px; background: transparent; }
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QComboBox QAbstractItemView {
    background: %(POPUP)s; border: 1px solid %(FIELD_BORDER)s; border-radius: 8px;
    selection-background-color: %(COMBO_SEL)s; selection-color: %(SELECT_FG)s; outline: 0;
}
""" % {'INPUT': C.INPUT, 'FIELD_BORDER': C.FIELD_BORDER, 'TEXT': C.TEXT,
       'BTN_HOVER_BORDER': C.BTN_HOVER_BORDER, 'ACCENT': C.ACCENT,
       'POPUP': C.POPUP, 'COMBO_SEL': C.COMBO_SEL, 'SELECT_FG': C.SELECT_FG}


#: 用户提供的图标素材（抠图 PNG，带透明通道）
ICON_ASSET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'assets', 'app_icon.png')


def _asset_icon_pixmap(size: int):
    """素材图标：居中补方 + 小尺寸裁头部。裁切规则与 build.ensure_icon 对齐。"""
    src = QPixmap(ICON_ASSET)
    if src.isNull():
        return None
    if size <= 32:
        # 16/24/32 只取头脸区域，娃娃脸挤进小瓷砖才认得出
        src = src.copy(0, 0, src.width(), int(src.height() * 0.72))
    side = max(src.width(), src.height())
    canvas = QPixmap(side, side)
    canvas.fill(Qt.transparent)
    p = QPainter(canvas)
    p.drawPixmap((side - src.width()) // 2, (side - src.height()) // 2, src)
    p.end()
    return canvas.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def make_icon(size: int = 256) -> QIcon:
    """应用图标：优先用 gtiharmonica/assets/app_icon.png（用户素材），
    缺素材时回落到内置绘制的黑金口琴瓷砖。

    build.ensure_icon 的 Pillow 版本遵循同一套裁切规则，保证开发态窗口
    图标和打包 exe 图标是同一个形象。
    """
    if os.path.isfile(ICON_ASSET):
        pm = _asset_icon_pixmap(size)
        if pm is not None:
            return QIcon(pm)

    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = size / 256.0
    small = size <= 48

    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0.0, QColor('#242428'))
    grad.setColorAt(1.0, QColor('#0e0f12'))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(QRectF(2 * s, 2 * s, size - 4 * s, size - 4 * s),
                      size * 0.225, size * 0.225)
    if not small:
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(232, 197, 126, 70), max(2 * s, 1)))
        p.drawRoundedRect(QRectF(2 * s, 2 * s, size - 4 * s, size - 4 * s),
                          size * 0.225, size * 0.225)
        p.setPen(Qt.NoPen)

    by0, by1 = (88 * s, 160 * s) if small else (82 * s, 154 * s)
    body_grad = QLinearGradient(0, by0, 0, by1)
    body_grad.setColorAt(0.0, QColor('#f0d190'))
    body_grad.setColorAt(1.0, QColor('#c9a654'))
    p.setBrush(QBrush(body_grad))
    p.drawRoundedRect(QRectF(34 * s, by0, 188 * s, by1 - by0), 16 * s, 16 * s)

    n = 5 if small else 8
    sw = (202 * s - 54 * s) / n
    slot_w = sw * (0.62 if small else 0.52)
    sy0, sy1 = (102 * s, 146 * s) if small else (100 * s, 136 * s)
    p.setBrush(QColor('#141418'))
    for i in range(n):
        cx = 54 * s + sw * (i + 0.5)
        p.drawRoundedRect(QRectF(cx - slot_w / 2, sy0, slot_w, sy1 - sy0),
                          (5 if small else 6) * s, (5 if small else 6) * s)

    if not small:
        p.setBrush(QColor(255, 255, 255, 95))
        p.drawRoundedRect(QRectF(48 * s, 89 * s, 160 * s, 3 * s), 2 * s, 2 * s)

    gy0, gy1 = (178 * s, 196 * s) if small else (172 * s, 186 * s)
    p.setBrush(QColor(238, 201, 137))
    p.drawRoundedRect(QRectF(34 * s, gy0, 188 * s, gy1 - gy0),
                      (9 if small else 7) * s, (9 if small else 7) * s)
    p.end()
    return QIcon(pix)


# ---- 顶栏小图标：现画不引资源，颜色跟随主题（切主题后要重新取） ----

def _icon_canvas(size: int):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    return pm, p


def icon_moon(size: int = 18, color: str = '') -> QIcon:
    """新月：大圆减掉一个向右上偏移的同心圆。"""
    pm, p = _icon_canvas(size)
    full = QPainterPath()
    full.addEllipse(2, 2, size - 4, size - 4)
    cut = QPainterPath()
    cut.addEllipse(size * 0.36, size * 0.10, size - 4, size - 4)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color) if color else QColor(C.MUTED))
    p.drawPath(full.subtracted(cut))
    p.end()
    return QIcon(pm)


def icon_sun(size: int = 18, color: str = '') -> QIcon:
    """太阳：实心圆 + 八条圆头光芒。"""
    pm, p = _icon_canvas(size)
    col = QColor(color) if color else QColor(C.MUTED)
    c = size / 2.0
    r = size * 0.26
    p.setPen(Qt.NoPen)
    p.setBrush(col)
    p.drawEllipse(QPointF(c, c), r, r)
    pen = QPen(col, max(1.4, size * 0.085))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    for k in range(8):
        a = math.pi / 4 * k
        p.drawLine(QPointF(c + math.cos(a) * r * 1.45, c + math.sin(a) * r * 1.45),
                   QPointF(c + math.cos(a) * r * 1.90, c + math.sin(a) * r * 1.90))
    p.end()
    return QIcon(pm)


def icon_gear(size: int = 18, color: str = '') -> QIcon:
    """齿轮：8 根圆头辐条 + 主体圆，中孔用 Clear 模板抠成透明。"""
    pm, p = _icon_canvas(size)
    col = QColor(color) if color else QColor(C.MUTED)
    c = size / 2.0
    p.setPen(Qt.NoPen)
    p.setBrush(col)
    p.save()
    p.translate(c, c)
    w = size * 0.14
    for k in range(8):
        p.save()
        p.rotate(45 * k)
        p.drawRoundedRect(QRectF(-w / 2, -size * 0.47, w, size * 0.62),
                          w / 2, w / 2)
        p.restore()
    p.restore()
    p.drawEllipse(QPointF(c, c), size * 0.33, size * 0.33)
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    p.drawEllipse(QPointF(c, c), size * 0.145, size * 0.145)
    p.end()
    return QIcon(pm)


# ---- 编辑器工具条图标：18px 设计稿按比例缩放到请求尺寸 ----

def _stroke_icon(size: int, draw, color: str = ''):
    pm, p = _icon_canvas(size)
    col = QColor(color) if color else QColor(C.TEXT)
    pen = QPen(col, max(1.3, size * 0.095))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    draw(p, size / 18.0, col)
    p.end()
    return QIcon(pm)


def icon_merge(size: int = 18, color: str = ''):
    """合并长音：两块短条 → 一块长条。"""
    def draw(p, u, col):
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(3 * u, 3.5 * u, 5 * u, 3.2 * u), 1.6 * u, 1.6 * u)
        p.drawRoundedRect(QRectF(10 * u, 3.5 * u, 5 * u, 3.2 * u), 1.6 * u, 1.6 * u)
        p.drawRoundedRect(QRectF(3 * u, 11 * u, 12 * u, 3.2 * u), 1.6 * u, 1.6 * u)
    return _stroke_icon(size, draw, color)


def icon_quantize(size: int = 18, color: str = ''):
    """量化：网格线 + 吸附到线上的点。"""
    def draw(p, u, col):
        for x in (4, 9, 14):
            p.drawLine(QPointF(x * u, 3 * u), QPointF(x * u, 15 * u))
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        for y in (6, 12):
            p.drawEllipse(QPointF(9 * u, y * u), 1.7 * u, 1.7 * u)
    return _stroke_icon(size, draw, color)


def icon_snap_scale(size: int = 18, color: str = ''):
    """调内吸附：上行音阶的点。"""
    def draw(p, u, col):
        p.drawLine(QPointF(3 * u, 15.5 * u), QPointF(15 * u, 15.5 * u))
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        for x, y in ((4, 12.5), (7.3, 9.7), (10.6, 6.9), (14, 4.1)):
            p.drawEllipse(QPointF(x * u, y * u), 1.6 * u, 1.6 * u)
    return _stroke_icon(size, draw, color)


def icon_overlap(size: int = 18, color: str = ''):
    """清理重叠：两个重叠的条，重叠区实心。"""
    def draw(p, u, col):
        p.drawRoundedRect(QRectF(3 * u, 4 * u, 8 * u, 6 * u), 1.5 * u, 1.5 * u)
        p.drawRoundedRect(QRectF(7 * u, 8 * u, 8 * u, 6 * u), 1.5 * u, 1.5 * u)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(7 * u, 8 * u, 4 * u, 2 * u), 1 * u, 1 * u)
    return _stroke_icon(size, draw, color)


def icon_delete(size: int = 18, color: str = ''):
    """删除：垃圾桶。"""
    def draw(p, u, col):
        p.drawLine(QPointF(4 * u, 5 * u), QPointF(14 * u, 5 * u))
        p.drawLine(QPointF(7 * u, 2.8 * u), QPointF(11 * u, 2.8 * u))
        p.drawLine(QPointF(7 * u, 2.8 * u), QPointF(7 * u, 5 * u))
        p.drawLine(QPointF(11 * u, 2.8 * u), QPointF(11 * u, 5 * u))
        p.drawPolyline(QPolygonF([
            QPointF(5 * u, 5 * u), QPointF(5.8 * u, 15.4 * u),
            QPointF(12.2 * u, 15.4 * u), QPointF(13 * u, 5 * u)]))
        p.drawLine(QPointF(7.6 * u, 8 * u), QPointF(7.9 * u, 12.6 * u))
        p.drawLine(QPointF(10.4 * u, 8 * u), QPointF(10.1 * u, 12.6 * u))
    return _stroke_icon(size, draw, color)


def icon_erase_range(size: int = 18, color: str = ''):
    """整段删除：[ X ]。"""
    def draw(p, u, col):
        p.drawPolyline(QPolygonF([
            QPointF(5 * u, 3.5 * u), QPointF(3 * u, 3.5 * u),
            QPointF(3 * u, 14.5 * u), QPointF(5 * u, 14.5 * u)]))
        p.drawPolyline(QPolygonF([
            QPointF(13 * u, 3.5 * u), QPointF(15 * u, 3.5 * u),
            QPointF(15 * u, 14.5 * u), QPointF(13 * u, 14.5 * u)]))
        p.drawLine(QPointF(7.4 * u, 6.7 * u), QPointF(10.9 * u, 11.3 * u))
        p.drawLine(QPointF(10.9 * u, 6.7 * u), QPointF(7.4 * u, 11.3 * u))
    return _stroke_icon(size, draw, color)


def icon_cut(size: int = 18, color: str = ''):
    """剪掉时间：剪刀。"""
    def draw(p, u, col):
        p.drawEllipse(QPointF(5 * u, 5.5 * u), 2 * u, 2 * u)
        p.drawEllipse(QPointF(5 * u, 12.5 * u), 2 * u, 2 * u)
        p.drawLine(QPointF(6.7 * u, 6.4 * u), QPointF(14.5 * u, 13.6 * u))
        p.drawLine(QPointF(6.7 * u, 11.6 * u), QPointF(14.5 * u, 4.4 * u))
    return _stroke_icon(size, draw, color)


def icon_insert_gap(size: int = 18, color: str = ''):
    """插入空白：时间轴中间撑开一段（左右箭头朝外）。"""
    def draw(p, u, col):
        p.drawLine(QPointF(2.5 * u, 9 * u), QPointF(7 * u, 9 * u))
        p.drawLine(QPointF(11 * u, 9 * u), QPointF(15.5 * u, 9 * u))
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([
            QPointF(2.5 * u, 9 * u), QPointF(6 * u, 6.9 * u),
            QPointF(6 * u, 11.1 * u)]))
        p.drawPolygon(QPolygonF([
            QPointF(15.5 * u, 9 * u), QPointF(12 * u, 6.9 * u),
            QPointF(12 * u, 11.1 * u)]))
    return _stroke_icon(size, draw, color)


def icon_insert_scale(size: int = 18, color: str = ''):
    """插入音阶：上行阶梯条。"""
    def draw(p, u, col):
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        for x, h in ((3.2, 4), (6.7, 7), (10.2, 10), (13.7, 13)):
            p.drawRoundedRect(QRectF(x * u, (15 - h) * u, 2.6 * u, h * u),
                              1.1 * u, 1.1 * u)
    return _stroke_icon(size, draw, color)


def icon_undo(size: int = 18, color: str = ''):
    """撤销：越顶向左下的弧线 + 箭头。"""
    def draw(p, u, col):
        p.drawArc(QRectF(4.5 * u, 5 * u, 9.5 * u, 9 * u), 20 * 16, 125 * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([
            QPointF(4.0 * u, 9.2 * u), QPointF(8.4 * u, 8.2 * u),
            QPointF(5.6 * u, 12.4 * u)]))
    return _stroke_icon(size, draw, color)


def icon_redo(size: int = 18, color: str = ''):
    """重做：撤销的水平镜像。"""
    def draw(p, u, col):
        p.translate(18 * u, 0)
        p.scale(-1, 1)
        p.drawArc(QRectF(4.5 * u, 5 * u, 9.5 * u, 9 * u), 20 * 16, 125 * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([
            QPointF(4.0 * u, 9.2 * u), QPointF(8.4 * u, 8.2 * u),
            QPointF(5.6 * u, 12.4 * u)]))
    return _stroke_icon(size, draw, color)


def icon_save(size: int = 18, color: str = ''):
    """保存：软盘。"""
    def draw(p, u, col):
        p.drawRoundedRect(QRectF(3 * u, 3 * u, 12 * u, 12 * u), 1.6 * u, 1.6 * u)
        p.drawPolyline(QPolygonF([
            QPointF(6 * u, 3 * u), QPointF(6 * u, 7 * u),
            QPointF(12 * u, 7 * u), QPointF(12 * u, 3 * u)]))
        p.drawRoundedRect(QRectF(5.6 * u, 10 * u, 6.8 * u, 5 * u),
                          0.8 * u, 0.8 * u)
    return _stroke_icon(size, draw, color)
