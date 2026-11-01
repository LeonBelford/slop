import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import qr  # noqa: E402


class QrTests(unittest.TestCase):
    def test_sizes_grow_with_text(self):
        self.assertEqual(len(qr.encode("a")), 21)
        self.assertEqual(len(qr.encode("x" * 40)), 29)
        self.assertGreater(len(qr.encode("http://192.168.0.10:8765/?token=" + "a" * 43)), 33)

    def test_finder_patterns_and_svg(self):
        m = qr.encode("http://192.168.0.10:8765/?token=abc")
        n = len(m)
        for ox, oy in ((0, 0), (n - 7, 0), (0, n - 7)):
            self.assertTrue(all(m[oy][ox + i] for i in range(7)))
            self.assertTrue(m[oy + 3][ox + 3])
        s = qr.svg("hello")
        self.assertTrue(s.startswith("<svg") and "<path" in s)

    def test_too_long_is_rejected(self):
        with self.assertRaises(ValueError):
            qr.encode("x" * 300)


if __name__ == "__main__":
    unittest.main()
