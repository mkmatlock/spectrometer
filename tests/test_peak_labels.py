import unittest

import numpy as np
import pygame

from spectrometer.ui import SpectrometerUI


class PeakLabelTests(unittest.TestCase):
    def setUp(self):
        self.settings = {}
        self.ui = SpectrometerUI(calibration_settings=self.settings)
        self.ui.mode = 'saved'
        self.ui._scale_active = True
        values = np.zeros(3500, np.int32)
        values[1000], values[2500] = 50000, 40000
        self.ui._plot.update(values)
        self.ui._reset_peaks(values)

    def tearDown(self):
        self.ui.review.close()
        self.ui.settings_view.close()
        pygame.font.quit()

    def open_label(self, index):
        self.ui._select_peak(tuple(self.ui._peaks.positions[index]))
        self.ui._label_peak()

    def test_accept_multiple_labels_and_draw_all_after_selection_cleared(self):
        self.open_label(0)
        self.assertEqual([b[0] for b in self.ui.buttons],
                         list('123456789.0') + ['Bksp', 'Accept', 'Cancel'])
        for key in '532.1':
            self.ui._label_key(key)
        self.ui._accept_label()
        self.assertEqual(self.settings, {'scale': {1000: 532.1}})
        self.assertFalse(self.ui._keypad_open)
        self.assertFalse(self.ui._peak_dialog)
        self.assertIsNone(self.ui._peaks.marker)
        self.open_label(1)
        for key in '650':
            self.ui._label_key(key)
        self.ui._accept_label()
        self.assertEqual(self.settings['scale'], {1000: 532.1, 2500: 650.0})
        pygame.font.init()
        surface = pygame.Surface((480, 320))
        self.ui.draw(surface, pygame.font.Font(None, 22))
        for position in self.ui._peaks.positions:
            x, y = np.rint(position).astype(int)
            self.assertEqual(surface.get_at((x + 3, y))[:3], (255, 209, 102))

    def test_cancel_and_invalid_input_leave_settings_unchanged(self):
        self.open_label(0)
        self.ui._accept_label()
        self.assertTrue(self.ui._keypad_open)
        self.assertEqual(self.settings['scale'], {})
        self.ui._label_key('.')
        self.ui._label_key('.')
        self.ui._label_key('5')
        self.assertEqual(self.ui._label_input, '0.5')
        self.ui._label_key('Bksp')
        self.assertEqual(self.ui._label_input, '0.')
        self.ui._cancel_label()
        self.assertEqual(self.settings['scale'], {})
        self.assertTrue(self.ui._peak_dialog)
        self.assertEqual([b[0] for b in self.ui.buttons], ['Label', 'Back'])
        self.settings['scale'][1000] = 532.1
        self.ui._label_peak()
        self.assertEqual(self.ui._label_input, '532.1')
        self.ui._label_key('Bksp')
        self.ui._cancel_label()
        self.assertEqual(self.settings['scale'][1000], 532.1)

    def test_labeled_peak_modify_cancel_delete_and_back(self):
        self.settings['scale'].update({1000: 532.1, 2500: 650.0})
        self.ui._select_peak(tuple(self.ui._peaks.positions[0]))
        self.assertEqual([b[0] for b in self.ui.buttons], ['Modify', 'Delete', 'Back'])
        self.ui.buttons[0][2]()
        self.assertEqual(self.ui._label_input, '532.1')
        self.ui._label_key('Bksp')
        self.ui._cancel_label()
        self.assertEqual([b[0] for b in self.ui.buttons], ['Modify', 'Delete', 'Back'])
        self.assertEqual(self.settings['scale'][1000], 532.1)
        self.ui.buttons[0][2]()
        self.ui._label_key('Bksp')
        self.ui._label_key('2')
        self.ui._accept_label()
        self.assertEqual(self.settings['scale'][1000], 532.2)
        self.ui._select_peak(tuple(self.ui._peaks.positions[0]))
        self.ui.buttons[2][2]()
        self.assertEqual(self.settings['scale'][1000], 532.2)
        self.ui._select_peak(tuple(self.ui._peaks.positions[0]))
        self.ui.buttons[1][2]()
        self.assertEqual(self.settings['scale'], {2500: 650.0})
        self.assertFalse(self.ui._peak_dialog)
        self.assertIsNone(self.ui._peaks.marker)
        self.ui._select_peak(tuple(self.ui._peaks.positions[0]))
        self.assertEqual([b[0] for b in self.ui.buttons], ['Label', 'Back'])
