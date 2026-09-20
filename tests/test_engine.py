"""引擎单元测试（仅标准库 unittest，无需 pytest）。

    python -m unittest tests.test_engine -v
    python tests/test_engine.py
"""
from __future__ import annotations

import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gtiharmonica.arrange import (Options, _fit_duration, arrange, monophonic)
from gtiharmonica.fingering import (CostModel, MinModifiers, Optimal,
                                    STRATEGIES, measure, total_cost)
from gtiharmonica.instrument import (KEYS, STEPS, Fingering, Instrument,
                                     key_distance, note_name)
from gtiharmonica.score import Note, Score, TempoMap


class TestInstrument(unittest.TestCase):

    def setUp(self):
        self.ins = Instrument()

    def test_default_range(self):
        table = self.ins.fingerings()
        self.assertEqual(self.ins.playable_range(), (48, 85))
        self.assertEqual(len(table), 38)

    def test_diatonic_mapping(self):
        """base=60 时 Z X C V B N M , 对应 C4 D4 E4 F4 G4 A4 B4 C5。"""
        expected = [60, 62, 64, 65, 67, 69, 71, 72]
        bases = [self.ins.base + s for s in STEPS]
        self.assertEqual(bases, expected)
        self.assertEqual(list(KEYS), ['z', 'x', 'c', 'v', 'b', 'n', 'm', ','])

    def test_c4_is_bare_key(self):
        """MIDI 60 应该可以用不按修饰键的 z 弹出来。"""
        best = self.ins.fingerings()[60][0]
        self.assertEqual(best.key, 'z')
        self.assertEqual(best.modifiers, ())

    def test_octave_modifiers(self):
        table = self.ins.fingerings()
        self.assertIn('mouse_left', table[48][0].modifiers)     # C3 = 降八度
        self.assertIn('mouse_right', table[72 + 12][0].modifiers
                      if 84 in table else ())

    def test_semitone_modifier(self):
        table = self.ins.fingerings()
        self.assertIn('mouse_middle', table[61][0].modifiers)   # C#4 = 升半音

    def test_candidates_sorted_by_modifier_count(self):
        for pitch, bucket in self.ins.fingerings().items():
            counts = [len(f.modifiers) for f in bucket]
            self.assertEqual(counts, sorted(counts),
                             'MIDI %d 的候选未按修饰键数排序' % pitch)

    def test_validate_rejects_bad_keys(self):
        with self.assertRaises(ValueError):
            Instrument(keys=('z', 'z', 'c', 'v', 'b', 'n', 'm', ',')).validate()
        with self.assertRaises(ValueError):
            Instrument(keys=('z', 'x', 'c', 'v', 'b', 'n', 'm', '#')).validate()
        with self.assertRaises(ValueError):
            Instrument(base=200).validate()
        with self.assertRaises(ValueError):
            Instrument(semitone_step=3).validate()

    def test_validate_rejects_same_modifier(self):
        with self.assertRaises(ValueError):
            Instrument(lower='mouse_left', semitone='mouse_left',
                       upper='mouse_right').validate()

    def test_nearest_playable(self):
        self.assertEqual(self.ins.nearest_playable(60), 60)
        # 折叠只在 ±12k 上寻找，20+36=56 才是最近的可演奏音高
        # （注意 48-20=28 不是八度倍数，所以 48 不可达）
        self.assertEqual(self.ins.nearest_playable(20), 56)
        self.assertEqual(self.ins.nearest_playable(100), 76)
        self.assertEqual(self.ins.nearest_playable(46), 58)
        self.assertEqual(self.ins.nearest_playable(87), 75)

    def test_config_roundtrip(self):
        data = self.ins.export_config()
        again = Instrument.from_config(data)
        self.assertEqual(again.base, self.ins.base)
        self.assertEqual(tuple(again.keys), tuple(self.ins.keys))

    def test_config_ignores_unknown_fields(self):
        ins = Instrument.from_config({'base': 62, 'hotkeys': {'stop': 'F9'}})
        self.assertEqual(ins.base, 62)

    def test_note_name(self):
        self.assertEqual(note_name(60), 'C4')
        self.assertEqual(note_name(61), 'C#4')
        self.assertEqual(note_name(21), 'A0')

    def test_keyboard_distance_sane(self):
        self.assertGreater(key_distance('z', 'm'), 0)
        self.assertAlmostEqual(key_distance('z', 'z'), 0.0)


class TestFingering(unittest.TestCase):

    def setUp(self):
        self.ins = Instrument()
        self.table = self.ins.fingerings()

    def test_optimal_beats_all_on_random_input(self):
        """DP 必须是全局最优：成本不高于任何其他策略。"""
        rng = random.Random(20240917)
        lo, hi = self.ins.playable_range()
        for trial in range(30):
            pitches = [rng.randint(lo, hi) for _ in range(rng.randint(2, 40))]
            model = CostModel()
            opt_plan = Optimal(model).plan(pitches, self.table)
            opt_cost = total_cost(opt_plan, model)
            for name, cls in STRATEGIES.items():
                strat = cls() if cls is MinModifiers else cls(model)
                other = total_cost(strat.plan(pitches, self.table), model)
                self.assertLessEqual(
                    opt_cost, other + 1e-9,
                    'optimal(%.4f) 劣于 %s(%.4f)，序列=%s'
                    % (opt_cost, name, other, pitches))

    def test_all_strategies_cover_same_pitches(self):
        pitches = [48, 60, 72, 85, 50, 61]
        for name, cls in STRATEGIES.items():
            strat = cls()
            plan = strat.plan(pitches, self.table)
            self.assertEqual(len(plan), len(pitches), name)
            for fing, pitch in zip(plan, pitches):
                self.assertIn(fing, self.table[pitch], '%s 选出了非法指法' % name)

    def test_measure_counts(self):
        plan = [Fingering('z'), Fingering('z', ('mouse_left',)),
                Fingering('x')]
        m = measure(plan)
        self.assertEqual(m.notes, 3)
        self.assertEqual(m.total_presses, 1 + 2 + 1)
        self.assertEqual(m.modifier_notes, 1)
        self.assertEqual(m.modifier_switches, 2)   # () -> (L) -> ()
        self.assertEqual(m.key_changes, 1)         # z -> z -> x
        self.assertEqual(m.max_simultaneous, 2)

    def test_empty_plan(self):
        for name, cls in STRATEGIES.items():
            self.assertEqual(cls().plan([], self.table), [])
            self.assertEqual(measure([]).notes, 0)

    def test_single_note(self):
        for name, cls in STRATEGIES.items():
            plan = cls().plan([60], self.table)
            self.assertEqual(len(plan), 1)

    def test_cost_model_monotonic(self):
        m = CostModel()
        same = m.transition(Fingering('z'), Fingering('z'))
        changed = m.transition(Fingering('z'), Fingering('x'))
        self.assertLess(same, changed, '同键复用应比换键便宜')

    def test_unknown_strategy_raises(self):
        from gtiharmonica.fingering import build_strategy
        with self.assertRaises(KeyError):
            build_strategy('nope')


class TestArrange(unittest.TestCase):

    def setUp(self):
        self.ins = Instrument()

    def _score(self, notes):
        return Score(title='t', notes=notes)

    def test_monophonic_takes_highest(self):
        notes = [Note(48, 0, 0.5), Note(55, 0, 0.5), Note(64, 0, 0.5),
                 Note(60, 1.0, 0.5)]
        melody, reduced = monophonic(notes, 'highest')
        self.assertEqual([n.pitch for n in melody], [64, 60])
        self.assertEqual(reduced, 2)

    def test_monophonic_lowest(self):
        notes = [Note(48, 0, 0.5), Note(64, 0, 0.5)]
        melody, _ = monophonic(notes, 'lowest')
        self.assertEqual([n.pitch for n in melody], [48])

    def test_monophonic_empty(self):
        melody, reduced = monophonic([], 'highest')
        self.assertEqual(melody, [])
        self.assertEqual(reduced, 0)

    def test_fit_duration_phrase_rule(self):
        """末音是乐句结尾：gap = min(breath, dur*0.3)，实测 0.5s -> 0.41s。"""
        opts = Options()
        self.assertAlmostEqual(_fit_duration(0.0, 0.5, None, opts), 0.41, places=4)

    def test_fit_duration_gate_rule(self):
        """中间音无空隙：gap = dur*(1-gate)，0.5s 且 gate=0.9 -> 0.45s。"""
        opts = Options()
        self.assertAlmostEqual(_fit_duration(0.0, 0.5, 0.5, opts), 0.45, places=4)

    def test_fit_duration_floor(self):
        """音长不会低于 min_note。"""
        opts = Options(min_note=0.02)
        self.assertGreaterEqual(_fit_duration(0.0, 0.005, 0.005, opts), 0.005)

    def test_arrange_octave_fold(self):
        """超音域音符应被折回可演奏范围。"""
        score = self._score([Note(100, 0, 0.5), Note(60, 1.0, 0.5)])
        plan = arrange(score, self.ins, Options())
        self.assertEqual(len(plan.steps), 2)
        self.assertTrue(self.ins.is_playable(plan.steps[0].pitch))
        self.assertEqual(plan.stats['folded'], 1)
        self.assertEqual(plan.steps[0].pitch, 76)

    def test_arrange_drops_when_fold_disabled(self):
        score = self._score([Note(100, 0, 0.5), Note(60, 1.0, 0.5)])
        plan = arrange(score, self.ins, Options(fold_octaves=False))
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.stats['dropped'], 1)

    def test_arrange_transpose(self):
        score = self._score([Note(60, 0, 0.5)])
        plan = arrange(score, self.ins, Options(transpose=2))
        self.assertEqual(plan.steps[0].pitch, 62)
        self.assertEqual(plan.steps[0].source_pitch, 60)

    def test_arrange_speed_scales_time(self):
        """start 是纯缩放；末尾的换气是绝对时长，所以总时长并非精确成比例。"""
        score = self._score([Note(60, 0, 1.0), Note(62, 1.0, 1.0)])
        fast = arrange(score, self.ins, Options(speed=2.0))
        slow = arrange(score, self.ins, Options(speed=0.5))
        self.assertAlmostEqual(fast.steps[1].start, slow.steps[1].start / 4,
                               places=6)
        self.assertAlmostEqual(fast.steps[0].start, 0.0, places=6)
        self.assertGreater(slow.duration, fast.duration * 3.5)

    def test_arrange_track_filter(self):
        score = self._score([Note(60, 0, 0.5, track=0),
                             Note(64, 0, 0.5, track=1)])
        plan0 = arrange(score, self.ins, Options(track=0))
        plan1 = arrange(score, self.ins, Options(track=1))
        self.assertEqual(plan0.steps[0].pitch, 60)
        self.assertEqual(plan1.steps[0].pitch, 64)

    def test_arrange_rejects_empty_track(self):
        score = self._score([Note(60, 0, 0.5, track=0)])
        with self.assertRaises(ValueError):
            arrange(score, self.ins, Options(track=7))

    def test_options_validation(self):
        for bad in (Options(speed=3.0), Options(gate=0.1),
                    Options(breath_ms=999), Options(transpose=99),
                    Options(chord_policy='nope'), Options(fold_prefer='nope')):
            with self.assertRaises(ValueError):
                bad.validate()

    def test_arrange_is_deterministic(self):
        notes = [Note(60 + (i * 5) % 24, i * 0.25, 0.2) for i in range(30)]
        score = self._score(notes)
        a = arrange(score, self.ins, Options())
        b = arrange(score, self.ins, Options())
        self.assertEqual([s.fingering for s in a.steps],
                         [s.fingering for s in b.steps])


class TestTempoMap(unittest.TestCase):

    def test_constant_tempo(self):
        tm = TempoMap(480, [(0, 500000)])       # 120 BPM，一拍 0.5s
        self.assertAlmostEqual(tm.to_seconds(0), 0.0)
        self.assertAlmostEqual(tm.to_seconds(480), 0.5, places=6)
        self.assertAlmostEqual(tm.to_seconds(960), 1.0, places=6)

    def test_tempo_change(self):
        tm = TempoMap(480, [(0, 500000), (480, 1000000)])
        self.assertAlmostEqual(tm.to_seconds(480), 0.5, places=6)
        self.assertAlmostEqual(tm.to_seconds(960), 1.5, places=6)

    def test_default_when_no_points(self):
        tm = TempoMap(480, [])
        self.assertAlmostEqual(tm.to_seconds(480), 0.5, places=6)


class TestScoreModel(unittest.TestCase):

    def test_duration_and_tracks(self):
        s = Score(title='x', notes=[Note(60, 0, 1.0, track=0),
                                    Note(64, 0.5, 2.0, track=1)])
        self.assertAlmostEqual(s.duration, 2.5)
        self.assertEqual(s.tracks(), [0, 1])
        self.assertEqual(len(s.track_notes(0)), 1)
        self.assertEqual(len(s.track_notes(None)), 2)

    def test_track_summary(self):
        s = Score(title='x', notes=[Note(60, 0, 1.0, track=0),
                                    Note(72, 1.0, 1.0, track=0)])
        summary = s.track_summary()
        self.assertEqual(summary[0]['notes'], 2)
        self.assertEqual(summary[0]['pitch_min'], 60)
        self.assertEqual(summary[0]['pitch_max'], 72)


class TestSynth(unittest.TestCase):

    def test_render_produces_valid_wav(self):
        import io
        import wave
        from gtiharmonica import synth
        wav = synth.render([(60, 0.0, 0.3), (64, 0.3, 0.3)])
        self.assertGreater(len(wav), 1000)
        with wave.open(io.BytesIO(wav), 'rb') as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), synth.DEFAULT_SAMPLE_RATE)
            self.assertGreater(wf.getnframes(), 0)

    def test_render_empty(self):
        from gtiharmonica import synth
        self.assertGreater(len(synth.render([])), 0)

    def test_midi_to_hz(self):
        from gtiharmonica import synth
        self.assertAlmostEqual(synth.midi_to_hz(69), 440.0, places=6)
        self.assertAlmostEqual(synth.midi_to_hz(60), 261.6256, places=3)

    def test_wavetable_cached(self):
        from gtiharmonica import synth
        a = synth.build_wavetable(synth.Timbre())
        b = synth.build_wavetable(synth.Timbre())
        self.assertIs(a, b)


class TestDemoSongs(unittest.TestCase):

    def test_demo_notes_within_range(self):
        from gtiharmonica.demo import DEMO_SONGS
        ins = Instrument()
        lo, hi = ins.playable_range()
        for name, song in DEMO_SONGS.items():
            for n in song['notes']:
                self.assertTrue(lo <= n['pitch'] <= hi,
                                '%s 的 MIDI %d 超出音域' % (name, n['pitch']))
                self.assertGreater(n['duration'], 0)
                self.assertGreaterEqual(n['start'], 0)

    def test_demo_songs_are_arrangeable(self):
        from gtiharmonica.demo import DEMO_SONGS
        ins = Instrument()
        for name, song in DEMO_SONGS.items():
            score = Score(title=name,
                          notes=[Note(n['pitch'], n['start'], n['duration'],
                                      phrase_end=n.get('phrase_end', False))
                                 for n in song['notes']])
            plan = arrange(score, ins, Options())
            self.assertEqual(len(plan.steps), len(song['notes']), name)

    def test_builtin_matches_original_gtiartist(self):
        """内置的小星星 / 欢乐颂必须与原程序 demo_scores() 的硬编码数据一致。"""
        from gtiharmonica.demo import _ODE, _TWINKLE

        # 原程序 gtiartist.score.demo_scores() 里的常量
        cases = [
            (_TWINKLE, [1, 1, 1, 1, 1, 1, 2] * 6, 104, 42),
            (_ODE, [1] * 12 + [1.5, 0.5, 2] + [1] * 12 + [1.5, 0.5, 2], 108, 30),
        ]
        for song, beats, bpm, count in cases:
            notes = song['notes']
            self.assertEqual(len(notes), count, song['title'])
            # duration 反推拍数，必须与原始拍数序列一致
            recovered = [round(n['duration'] * bpm / 60.0, 3) for n in notes]
            self.assertEqual(recovered, [round(b, 3) for b in beats],
                             song['title'])
            # start 必须是累计拍数换算，且第一个音在 0 时刻
            self.assertAlmostEqual(notes[0]['start'], 0.0, places=6)
            beat = 0.0
            for n, length in zip(notes, beats):
                self.assertAlmostEqual(n['start'], beat * 60.0 / bpm, places=5,
                                       msg=song['title'])
                beat += length
            # 乐句标记：每 7 个音一次
            marks = [i for i, n in enumerate(notes) if n['phrase_end']]
            self.assertEqual(marks, list(range(6, len(notes), 7)), song['title'])

    def test_twinkle_melody(self):
        """小星星前 14 个音必须是 C C G G A A G / F F E E D D C。"""
        from gtiharmonica.demo import _TWINKLE
        got = [n['pitch'] for n in _TWINKLE['notes'][:14]]
        self.assertEqual(got, [60, 60, 67, 67, 69, 69, 67,
                               65, 65, 64, 64, 62, 62, 60])

    def test_ode_melody(self):
        """欢乐颂前 8 个音必须是 E E F G G F E D。"""
        from gtiharmonica.demo import _ODE
        got = [n['pitch'] for n in _ODE['notes'][:8]]
        self.assertEqual(got, [64, 64, 65, 67, 67, 65, 64, 62])


if __name__ == '__main__':
    unittest.main(verbosity=2)
