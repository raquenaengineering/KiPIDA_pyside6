"""GeometryExtractor tests using small kipy-style (IPC API) fake board objects."""
import math
import unittest

from kipida.kicad.extractor import GeometryExtractor, TRACK_SAFETY_BUFFER_MM


class FakeNet:
    def __init__(self, name):
        self.name = name


class FakeVector:
    """kipy Vector2: coordinates in nanometres."""
    def __init__(self, x, y):
        self.x, self.y = x, y


class FakeTrack:
    def __init__(self, net, layer, start, end, width):
        self.net = FakeNet(net)
        self.layer = layer
        self.start = FakeVector(*start)
        self.end = FakeVector(*end)
        self.width = width


class FakeBoard:
    def __init__(self):
        self.tracks = []
        self.vias = []
        self.footprints = []
        self.zones = []


class TestGeometryExtractor(unittest.TestCase):
    def setUp(self):
        self.board = FakeBoard()
        self.extractor = GeometryExtractor(self.board, debug=False, log_callback=None)
        # No live KiCad: use the built-in 2-layer default stackup (F.Cu=0, B.Cu=31)
        self.extractor._stackup_cache = self.extractor._get_stackup_defaults()

    def test_stackup_defaults(self):
        stackup = self.extractor.get_board_stackup()
        self.assertIn('copper', stackup)
        self.assertIn(0, stackup['copper'])  # F.Cu
        self.assertEqual(stackup['copper'][0]['thickness_mm'], 0.035)

    def test_track_extraction(self):
        # 0.5 mm wide track from (0,0) to (10 mm, 0) on F.Cu
        self.board.tracks.append(FakeTrack("TestNet", 0, (0, 0), (10_000_000, 0), 500_000))

        geo = self.extractor.get_net_geometry("TestNet")

        self.assertEqual(list(geo), [0])
        # Rectangle plus round end caps, widened by the safety buffer on each side
        half_width = 0.25 + TRACK_SAFETY_BUFFER_MM
        expected = 10 * (2 * half_width) + math.pi * half_width ** 2
        self.assertAlmostEqual(geo[0].area, expected, delta=0.01)

    def test_other_nets_ignored(self):
        self.board.tracks.append(FakeTrack("OtherNet", 0, (0, 0), (10_000_000, 0), 500_000))
        self.assertEqual(self.extractor.get_net_geometry("TestNet"), {})


if __name__ == '__main__':
    unittest.main()
