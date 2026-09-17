import unittest
from unittest.mock import patch

import pygame

from spectrometer.annotations import draw_peak_labels


class AnnotationLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.font.init()

    def setUp(self):
        self.surface = pygame.Surface((480, 320))
        self.font = pygame.font.Font(None, 18)
        self.area = pygame.Rect(9, 40, 462, 130)

    def draw(self, labels, intensity=None, origin=1000, area=None):
        return draw_peak_labels(
            self.surface, self.font, area or self.area,
            intensity if intensity is not None else [50] * 100,
            100, origin, labels,
        )

    def test_hit_targets_are_inside_graph_and_use_sensor_pixel_keys(self):
        rects = self.draw({1000: "Red line", 1099: "Blue line", 1100: "Outside"})
        self.assertEqual(set(rects), {1000, 1099})
        for rect in rects.values():
            self.assertTrue(self.area.contains(rect))
            self.assertGreaterEqual(rect.height, 22)
            self.assertTrue(rect.collidepoint(rect.center))
        self.assertGreater(rects[1000].centerx, rects[1099].centerx)

    def test_markers_follow_reversed_curve_and_clip_intensity(self):
        values = [-10] + [50] * 98 + [120]
        with patch("spectrometer.annotations.pygame.draw.circle", wraps=pygame.draw.circle) as circle:
            self.draw({1000: "Low", 1099: "High"}, intensity=values)
        points = [call.args[2] for call in circle.call_args_list]
        self.assertEqual(points, [(self.area.right - 1, self.area.bottom - 1),
                                  (self.area.left, self.area.top)])

    def test_roi_origin_does_not_change_annotation_geometry(self):
        original = self.draw({1002: "A", 1080: "B"})
        shifted = self.draw({402: "A", 480: "B"}, origin=400)
        self.assertEqual(original[1002], shifted[402])
        self.assertEqual(original[1080], shifted[480])

    def test_long_neighbouring_labels_are_bounded_and_separated(self):
        labels = {1048: "a" * 64, 1049: "b" * 64, 1050: "c" * 64}
        rects = self.draw(labels)
        self.assertEqual(set(rects), set(labels))
        for pixel, rect in rects.items():
            self.assertTrue(self.area.contains(rect))
            self.assertLessEqual(rect.width, 188)
            for other_pixel, other in rects.items():
                if pixel != other_pixel:
                    self.assertFalse(rect.colliderect(other))

    def test_layout_is_stable_regardless_of_label_insertion_order(self):
        labels = {1048: "First", 1049: "Second", 1099: "Third"}
        first = self.draw(labels)
        self.assertEqual(first, self.draw(dict(reversed(list(labels.items())))))
        self.assertEqual(first, self.draw(labels))

    def test_narrow_graph_and_existing_clip_are_respected(self):
        area = pygame.Rect(15, 30, 45, 24)
        self.surface.fill("black")
        original_clip = pygame.Rect(10, 20, 70, 70)
        self.surface.set_clip(original_clip)
        rects = self.draw({1000: "A long label"}, area=area)
        self.assertTrue(area.contains(rects[1000]))
        self.assertEqual(self.surface.get_clip(), original_clip)
        self.assertEqual(self.surface.get_at((14, 30)), pygame.Color("black"))

    def test_flat_values_and_valleys_keep_their_saved_annotations(self):
        values = [50] * 100
        values[60] = 0
        self.assertEqual(set(self.draw({1040: "Flat", 1060: "Valley"}, intensity=values)),
                         {1040, 1060})

    def test_empty_curve_has_no_targets(self):
        self.assertEqual(self.draw({1000: "Line"}, intensity=[]), {})


if __name__ == "__main__":
    unittest.main()
