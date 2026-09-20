"""从反汇编产物里提取原程序的 QSS 样式表与所有颜色常量。"""
import marshal
import os
import re
import sys

MAR_DIR = r"D:\AI\gtiartist-re\pyz_out"
TARGETS = ["gtiartist.ui", "gtiartist.timeline", "gtiartist.track_widgets",
           "gtiartist.library_widgets", "gtiartist.editing_widgets"]


def walk(co, path=""):
    qn = path or co.co_name
    yield qn, co
    for c in co.co_consts:
        if hasattr(c, "co_code"):
            yield from walk(c, qn + "." + c.co_name)


print("=" * 78)
print("QSS 样式表（长度 > 300 的字符串常量）")
print("=" * 78)
for name in TARGETS:
    fp = os.path.join(MAR_DIR, name + ".mar")
    if not os.path.exists(fp):
        continue
    co = marshal.loads(open(fp, "rb").read())
    for qn, c in walk(co):
        for const in c.co_consts:
            if isinstance(const, str) and len(const) > 300 and (
                    "{" in const and "}" in const):
                print("\n----- %s :: %s  (%d 字符) -----" % (name, qn, len(const)))
                print(const)

print()
print("=" * 78)
print("全部颜色常量（按出现次数）")
print("=" * 78)
from collections import Counter
colors = Counter()
where = {}
for name in TARGETS:
    fp = os.path.join(MAR_DIR, name + ".mar")
    if not os.path.exists(fp):
        continue
    co = marshal.loads(open(fp, "rb").read())
    for qn, c in walk(co):
        for const in c.co_consts:
            if isinstance(const, str):
                for m in re.finditer(r"#[0-9a-fA-F]{6}\b", const):
                    colors[m.group(0).lower()] += 1
                    where.setdefault(m.group(0).lower(), set()).add(name)
for color, count in colors.most_common(60):
    print("  %-10s x%-3d   %s" % (color, count, ", ".join(sorted(where[color]))))
