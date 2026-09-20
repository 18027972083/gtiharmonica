# tools 说明

开发与自检脚本。**正式自检**是随每次改动都要跑的；**归档**里的是当时排查
具体问题用的一次性脚本，留着是为了可追溯，平时不用跑。

## 正式自检（改动后必跑）

| 脚本 | 项数 | 覆盖 |
|---|---|---|
| `_check_library.py` | 21 | 曲库位置迁移、幂等、构建前后清单校验 |
| `_check_edit.py` | 88 | 编辑器数据层：编辑命令、撤销、吸附、碎片合并、往返存盘、**不二次移调** |
| `_check_editor_ui.py` | 51 | 编辑器界面：进程内驱动 Qt + `grab()` 读真实像素，验证确实画出来了 |
| `_check_integration.py` | 41 | 主窗口接线：视图切换、编排重算、存回 JSON、键位联动、换曲作废 |
| `_check_notation.py` | 32 | 乐谱层：小节线、拍号推断、延音线、时值名称 |
| `_check_reattack.py` | 8 场景 | 长音完整性（重新起音判据） |
| `_check_layout.py` | — | 工具栏与卡片标题行的几何：无重叠、无溢出（含分段控件） |
| `_check_icon.py` | — | 图标渲染 |

```powershell
python tools\_check_edit.py    # 依此类推
```

## 发布验证

| 脚本 | 说明 |
|---|---|
| `_verify_release.py` | **真机**验证：产物能启动、曲库确实迁到 `%APPDATA%`、源文件保留、重启动幂等。会启动 exe，验证完自动关闭；加 `--keep-running` 可留着不关 |

```powershell
python tools\_verify_release.py
```

## 开发辅助

| 脚本 | 用途 |
|---|---|
| `eval_transcribe.py` | 转录精度验收：用已知 ground-truth 的合成音频测准确率 |
| `transcribe_file.py` | 命令行转谱工具，也是真实音乐验收的入口 |
| `diag_notes.py` | 诊断：识别结果为什么有「时间点缺失」 |
| `diag_transcribe.py` | 诊断：有伴奏时显著性面板把票投给了谁 |
| `shot_ui.py` | 抓主界面与转录对话框截图，比对 UI 风格 |
| `shot_roll.py` | 抓长曲子的旋律预览，验证「固定时间窗 + 随播放滚动」 |
| `extract_theme.py` | 从反汇编产物里提取原程序的 QSS 与颜色常量 |

## diag/ — 归档的一次性诊断

这些脚本是排查「长音被切碎 / 音高被改坏」那几轮问题时写的。它们把当时的
推理过程变成了可复现的数字，所以留着 —— 但**平时不需要跑**。

| 脚本 | 当时回答的问题 |
|---|---|
| `_diag_dip.py` | 测「重新起音」判据的中间量，用真实数字决定阈值 |
| `_diag_vibrato.py` | 颤音长音为什么被切碎：是能量判据放行，还是音高轨迹在跳 |
| `_diag_merge.py` | round-trip 后音符数减少，是正常单声部化还是过度合并 |
| `_why_split.py` | 真实曲谱里的无缝重复音为什么能被断开 |
| `_cmp_pitch.py` | 新旧判据下的音高序列对比 —— **就是它抓到了音高回归** |
| `_cmp_real.py` | 同一对比，跑在真实音频上 |
| `_roundtrip_long.py` | 曲谱 → 渲染成音频 → 转录回来，看长音是否还在 |
| `_sample_colors.py` | 采样参数卡控件的实际渲染颜色，确认「灰底」到底是什么色 |

它们在 `tools/diag/` 里，路径已相应修正（ROOT 用三层 `dirname`，
对 `tools/` 本目录的引用用两层）。其中 `_diag_vibrato.py` 与 `_why_split.py`
依赖 `_check_reattack`，所以运行时需要 `tools/` 在 `sys.path` 上 ——
脚本里已经处理好了。

```powershell
python tools\diag\_cmp_pitch.py
```
