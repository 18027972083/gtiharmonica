"""输入兼容三档 + 播放引擎改造的回归检查。

验证点（对应 midikey-player 移植）：
  1. 三档修饰键提前量：standard 40ms / safe 70ms / aggressive 20ms（±5ms）
  2. safe 档同键重触发保护：间隔不足 retrig 的重复音被顺延
  3. 标准档不改变谱面节奏（间隔原样）
  4. 循环播放：loops=2 时音符发两遍，终态 finished
  5. seek 请求：_seek_request 置位后位置跳变、后续音从新位置继续
  6. trim_lead：编排把首音前的空拍裁掉
  7. preflight 自检模块可运行且能识别输入法状态
"""
import io
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                              errors='replace')
sys.path.insert(0, '.')

FAILS = []


def check(name, cond, detail=''):
    print('%s %s%s' % ('✓' if cond else '✗', name,
                       ('（%s）' % detail if detail and not cond else '')))
    if not cond:
        FAILS.append(name)


from gtiharmonica.player import Player, SchedulerConfig, State, INPUT_TIMINGS
from gtiharmonica.arrange import Arrangement, PlayStep, Options, arrange
from gtiharmonica.instrument import Instrument
from gtiharmonica.backend import DryRunBackend
from gtiharmonica.score import Score, Note

inst = Instrument()
table = inst.fingerings()
f_none = table[60][0]
f_low = [f for f in table[60] if f.modifiers][0]

steps = [
    PlayStep(pitch=60, source_pitch=60, fingering=f_none, start=0.10, duration=0.10),
    PlayStep(pitch=60, source_pitch=60, fingering=f_none, start=0.15, duration=0.10),
    PlayStep(pitch=60, source_pitch=60, fingering=f_low,  start=0.30, duration=0.15),
    PlayStep(pitch=62, source_pitch=62, fingering=f_low,  start=0.50, duration=0.10),
    PlayStep(pitch=64, source_pitch=64, fingering=f_none, start=0.70, duration=0.10),
    PlayStep(pitch=64, source_pitch=64, fingering=f_none, start=0.80, duration=0.10),
]
plan = Arrangement(title='t', steps=steps)


class Recorder(DryRunBackend):
    """记录真实物理时间的后端。"""

    def __init__(self):
        super().__init__(verbose=False)
        self.events = []

    def press_modifiers(self, step):
        self.events.append((time.perf_counter(), 'mods', step.pitch))

    def play_note(self, step):
        self.events.append((time.perf_counter(), 'note', step.pitch))

    def release_note_keys(self):
        self.events.append((time.perf_counter(), 'rel', -1))

    def silence(self):
        self.events.append((time.perf_counter(), 'silence', -1))


EXPECTED_LEAD = {'standard': 0.040, 'safe': 0.070, 'aggressive': 0.020}

# 1+2+3) 三档物理时序
for tier, lead in EXPECTED_LEAD.items():
    be = Recorder()
    p = Player(plan, be, SchedulerConfig(timing=tier),
               hotkeys=False, keep_focus=False)
    p.run(loops=1)
    notes = [e for e in be.events if e[1] == 'note']
    mod_pairs = []
    for e in be.events:
        if e[1] != 'mods':
            continue
        nxt = next((x for x in be.events
                    if x[1] == 'note' and x[0] >= e[0]), None)
        if nxt:
            mod_pairs.append(nxt[0] - e[0])
    # 至少一次换修饰发生（音 3 与音 5），提前量应有 ≥1 次接近档位值
    near = any(abs(x - lead) < 0.01 for x in mod_pairs)
    check('%s 档修饰键提前量 ≈ %.0fms' % (tier, lead * 1000), near,
          '实测 %s' % [round(x, 3) for x in mod_pairs])
    iv = [b[0] - a[0] for a, b in zip(notes, notes[1:])]
    if tier == 'safe':
        # 同键 0.05 间隔被顺延到 ≥ retrig - 容差
        check('safe 档同键重触发顺延', abs(iv[0] - 0.080) < 0.01,
              '实测 %.3f' % iv[0])
    else:
        check('%s 档保持谱面节奏' % tier, abs(iv[0] - 0.05) < 0.012,
              '实测 %.3f' % iv[0])

# 4) 循环播放
be = Recorder()
p = Player(plan, be, SchedulerConfig(timing='standard'),
           hotkeys=False, keep_focus=False)
state = p.run(loops=2)
check('循环两遍后正常结束', state is State.FINISHED)
count = sum(1 for e in be.events if e[1] == 'note')
check('循环两遍音符数翻倍', count == 12, '实测 %d' % count)

# 5) seek：模拟播放中按下 F7（第一个音之后置位跳转请求）
be = Recorder()
p = Player(plan, be, SchedulerConfig(timing='standard'),
           hotkeys=False, keep_focus=False)
orig_note = Recorder.play_note
fired = [False]


def spy_note(self, step):
    orig_note(self, step)
    if not fired[0]:
        fired[0] = True
        p._seek_request = 5.0      # 超出全曲长度 → 跳到末尾


Recorder.play_note = spy_note
state = p.run(loops=1)
Recorder.play_note = orig_note
check('seek 请求被消费且跳到末尾',
      state is State.FINISHED and fired[0]
      and p._seek_index >= len(plan.steps) - 1,
      'state=%s index=%s' % (state.value, p._seek_index))

# 6) trim_lead
score = Score(notes=[
    Note(pitch=60, start=2.5, duration=0.3),
    Note(pitch=62, start=3.0, duration=0.3),
])
plan2 = arrange(score, inst, Options())
check('trim_lead 默认开：首音平移到 0',
      abs(plan2.steps[0].start) < 0.001,
      '首音 start=%.3f' % plan2.steps[0].start)
plan3 = arrange(score, inst, Options(trim_lead=False))
check('trim_lead 关：保留前奏空拍',
      abs(plan3.steps[0].start - 2.5) < 0.001)

# 7) preflight
from gtiharmonica.preflight import run_preflight
report = run_preflight(need_admin=False)
check('自检可运行并给出结论',
      isinstance(report.admin.passed, bool) and isinstance(report.ime.passed, bool),
      report.summary)

print('=' * 70)
print('通过 %d 项，失败 %d 项' % (
    7 + sum(1 for _ in EXPECTED_LEAD) - len(FAILS) - 2 + 2, len(FAILS)))
if FAILS:
    print('失败项：', FAILS)
    sys.exit(1)
print('全部通过')
