"""对比内置 MIDI 解析器与 mido 的结果，确保零依赖路径可靠。"""
import os
import sys

sys.path.insert(0, r"D:\AI\gti-harmonica")
sys.path.insert(0, r"D:\AI\gtiartist-re\libs")

from gtiharmonica import score as S

CASES = [
    r"D:\AI\gti-harmonica\tests\wide_jump.mid",
    r"D:\AI\gti-harmonica\tests\chromatic.mid",
    r"D:\AI\gti-harmonica\tests\stepwise.mid",
    r"D:\AI\gti-harmonica\tests\long.mid",
    r"D:\AI\gtiartist-re\test_star.mid",
]

print('%-16s %-28s %-28s %s' % ('file', 'builtin', 'mido', 'verdict'))
print('-' * 96)

all_ok = True
for path in CASES:
    if not os.path.exists(path):
        print('%-16s (missing)' % os.path.basename(path))
        continue
    a = S._load_midi_builtin(path)
    b = S._load_midi_mido(path)

    sa = [(round(n.start, 4), round(n.duration, 4), n.pitch, n.track) for n in a.notes]
    sb = [(round(n.start, 4), round(n.duration, 4), n.pitch, n.track) for n in b.notes]

    same = sa == sb
    if not same:
        all_ok = False
        diffs = [(x, y) for x, y in zip(sa, sb) if x != y][:3]
    else:
        diffs = []

    print('%-16s %-28s %-28s %s' % (
        os.path.basename(path),
        '%d notes, %.2fs' % (len(sa), a.duration),
        '%d notes, %.2fs' % (len(sb), b.duration),
        'IDENTICAL' if same else 'DIFF x%d' % len(diffs)))
    for x, y in diffs:
        print('     builtin=%s  mido=%s' % (x, y))

print('-' * 96)
print('ALL IDENTICAL' if all_ok else 'MISMATCH FOUND')

# 再验证：把 mido 从 sys.modules 里摘掉，确认 load_midi 能回落
print('\n--- fallback check (hide mido) ---')
import builtins
real_import = builtins.__import__


def blocked(name, *args, **kwargs):
    if name == 'mido':
        raise ImportError('blocked for test')
    return real_import(name, *args, **kwargs)


builtins.__import__ = blocked
try:
    sc = S.load_midi(CASES[0])
    print('load_midi without mido -> OK, %d notes, %.2fs' % (len(sc.notes), sc.duration))
except Exception as exc:
    print('load_midi without mido -> FAILED: %r' % exc)
finally:
    builtins.__import__ = real_import
