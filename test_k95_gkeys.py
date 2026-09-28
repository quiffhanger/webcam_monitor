import ctypes
import unittest
from k95_gkeys import Edges, g_mask, chord_events, valid_colors, Input

def report(mask):
    raw = bytearray(64)
    raw[0], raw[16] = 3, mask
    return bytes(raw)

class Tests(unittest.TestCase):
    def test_all_six_positions(self):
        for i in range(6):
            self.assertEqual(Edges().update(report(1 << i)), [i+1])

    def test_held_no_repeat_and_release(self):
        edges = Edges()
        self.assertEqual(edges.update(report(5)), [1, 3])
        self.assertEqual(edges.update(report(5)), [])
        self.assertEqual(edges.update(report(0)), [])
        self.assertEqual(edges.update(report(4)), [3])

    def test_unrelated_reports_ignored(self):
        self.assertIsNone(g_mask(bytes(64)))
        self.assertIsNone(g_mask(report(1)[:17]))
        self.assertEqual(g_mask(bytes([0]) + report(63)), 63)
        self.assertEqual(g_mask(report(0xC0)), 0)

    def test_chords_balanced(self):
        for i in range(1, 7):
            events, cleanup = chord_events(i, lambda _: False)
            self.assertEqual(events[3:5], [(0x30+i, 0), (0x30+i, 2)])
            self.assertEqual(len(events), 8)
            self.assertEqual({k for k,f in events if f == 0}, {k for k,f in events if f == 2})
            self.assertTrue(all(flags == 2 for _,flags in cleanup))

    def test_physical_modifiers_not_released(self):
        events, cleanup = chord_events(3, lambda key: key in (0x10, 0x12))
        self.assertFalse(any(key in (0x10, 0x12) for key,_ in events+cleanup))
        self.assertEqual(len(events), 4)

    def test_frame_validation(self):
        self.assertTrue(valid_colors(['00ffAA'] * 139))
        self.assertFalse(valid_colors(['00ffAA'] * 138))
        self.assertFalse(valid_colors(['red'] * 139))

    def test_windows_input_layout(self):
        self.assertEqual(ctypes.sizeof(Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)

if __name__ == '__main__':
    unittest.main()

