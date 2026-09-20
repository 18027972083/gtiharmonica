"""生成压力测试用例：音域跨度大、频繁跳进，强制产生修饰键切换。"""
import os
import sys

sys.path.insert(0, r"D:\AI\gtiartist-re\libs")
import mido

OUT = r"D:\AI\gti-harmonica\tests"
os.makedirs(OUT, exist_ok=True)


def write(name, pitches, ticks=240, tempo=500000, tracks=None):
    mid = mido.MidiFile()
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    for p in pitches:
        tr.append(mido.Message("note_on", note=p, velocity=80, time=0))
        tr.append(mido.Message("note_off", note=p, velocity=0, time=ticks))
    path = os.path.join(OUT, name)
    mid.save(path)
    print("wrote %-24s %d notes  range=%d..%d" %
          (name, len(pitches), min(pitches), max(pitches)))
    return path


# 1) 宽音域大跳：每次都在低音区和高音区之间反复，修饰键必须跟着切
write("wide_jump.mid", [48, 84, 60, 85, 49, 83, 61, 72, 50, 82,
                        62, 73, 51, 81, 63, 74, 52, 80, 64, 75])

# 2) 半音阶爬升：大量需要「中键升半音」的音，考验修饰键复用
write("chromatic.mid", list(range(48, 86)))

# 3) 相邻大二度级进：尽量留在同一八度，考验「少换键」
write("stepwise.mid", [60, 62, 64, 65, 67, 69, 71, 72, 71, 69,
                       67, 65, 64, 62, 60, 62, 64, 60, 62, 60])

# 4) 长曲：把宽音域片段重复 30 遍，测策略的可扩展性
write("long.mid", [48, 84, 60, 85, 49, 83, 61, 72] * 30)

print("\nOK ->", OUT)
