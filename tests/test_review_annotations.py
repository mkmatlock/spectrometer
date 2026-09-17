"""Review annotations persist separately from wavelength calibration."""

from concurrent.futures import Future
from copy import deepcopy
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pygame

from spectrometer.capture import save_capture
from spectrometer.ui import SpectrometerUI
from tests.spectrum_fixtures import spectrum_record


class ReviewAnnotationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.values = np.zeros(500, dtype=np.int32)
        self.values[150], self.values[350] = 1000, 800
        self.record = spectrum_record(spectrum_roi=(100, 0, 600, 2),
                                      spectrum_intensity=self.values)
        self.record['instrument_settings']['calibration_settings']['scale'] = {
            100: 700.0, 599: 400.0}
        self.path = save_capture(self.record, self.directory)
        self.original = self.path.read_bytes()
        self.ui = SpectrometerUI(review_directory=self.directory)
        self.addCleanup(self.ui.settings_view.close)
        self.addCleanup(self.ui.review.close)
        pygame.font.init()
        self.addCleanup(pygame.font.quit)
        self.ui._open_review()
        self.ui.review.selected = 0
        self.open_selected()

    def open_selected(self):
        self.ui._display_capture()
        self.ui.review.future.result(timeout=5)
        self.ui._poll_review()
        self.assertEqual(self.ui.mode, 'saved')

    def tap(self, position):
        self.ui._pointer_event('lcd', position, True)
        self.ui._pointer_event('lcd', position, False)

    def button(self, label):
        matches = [rect for name, rect, _ in self.ui.buttons if name == label]
        self.assertEqual(len(matches), 1, [b[0] for b in self.ui.buttons])
        self.tap(matches[0].center)

    def select(self, index=0):
        self.tap(tuple(self.ui._peaks.positions[index]))

    def await_annotation(self):
        self.ui._annotation_future.result(timeout=5)
        self.ui._poll_capture()

    def label(self, text, index=0):
        self.select(index)
        self.button('Label')
        self.assertEqual(self.ui.mode, 'annotation')
        for key in text:
            self.ui._capture_key('Space' if key == ' ' else key)
        self.button('Accept')
        self.await_annotation()

    def draw(self):
        surface = pygame.Surface((480, 320))
        self.ui.draw(surface, pygame.font.Font(None, 22))
        return surface

    def test_label_keyboard_accept_persists_without_changing_capture_or_calibration(self):
        calibration = deepcopy(self.ui.calibration_settings)
        self.select()
        self.assertEqual([b[0] for b in self.ui.buttons], ['Label', 'Cancel'])
        self.assertAlmostEqual(self.ui._plot.scale.wavelength(250), 609.8196392785571)
        self.assertEqual(self.ui._review_peak_label, '609.8 nm')
        self.button('Label')
        self.assertEqual(self.ui.mode, 'annotation')
        self.assertEqual(self.ui._capture_name, '')
        self.assertIn('q', [b[0] for b in self.ui.buttons])
        self.assertIn('Space', [
            'Space' if b[0] == '␣' else b[0] for b in self.ui.buttons])
        self.button('Accept')
        self.assertEqual(self.ui.mode, 'annotation')
        self.assertIsNone(self.ui._annotation_future)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.ui._capture_key('Shift')
        self.ui._capture_key('H')
        self.ui._capture_key('Shift')
        for key in ('e', 'Space', '2', 'Bksp', '1'):
            self.ui._capture_key(key)
        self.assertEqual(self.ui._capture_name, 'He 1')
        self.draw()
        self.button('Accept')
        self.await_annotation()
        self.assertEqual(self.ui.mode, 'saved')
        self.assertFalse(self.ui._peak_dialog)
        self.assertIsNone(self.ui._peaks.marker)
        self.assertEqual(self.ui._review_labels, {250: 'He 1'})
        self.assertEqual(self.ui.calibration_settings, calibration)
        expected = dict(self.record, peak_labels={250: 'He 1'})
        np.testing.assert_equal(pickle.loads(self.path.read_bytes()), expected)
        self.button('Back')
        self.open_selected()
        self.assertEqual(self.ui._review_labels, {250: 'He 1'})
        self.draw()
        self.assertEqual(set(self.ui._annotation_rects), {250})

    def test_cancel_dialog_and_keyboard_discard_marker_and_do_not_write(self):
        self.select()
        self.button('Cancel')
        self.assertEqual(self.ui.mode, 'saved')
        self.assertFalse(self.ui._peak_dialog)
        self.assertIsNone(self.ui._peaks.marker)
        self.assertIsNone(self.ui._review_peak_label)
        self.select()
        self.button('Label')
        self.ui._capture_key('x')
        self.button('Cancel')
        self.assertEqual(self.ui.mode, 'saved')
        self.assertFalse(self.ui._peak_dialog)
        self.assertIsNone(self.ui._peaks.marker)
        self.assertEqual(self.ui._review_labels, {})
        self.assertEqual(self.path.read_bytes(), self.original)
        self.button('Back')
        self.assertEqual(self.ui.mode, 'review')
        self.assertIsNone(self.ui._peaks.marker)

    def test_touching_another_peak_retargets_active_dialog(self):
        self.select(0)
        self.assertEqual(self.ui._annotation_pixel, 250)
        self.select(1)
        self.assertEqual(self.ui._annotation_pixel, 450)
        self.assertTrue(self.ui._peak_dialog)
        self.assertEqual([b[0] for b in self.ui.buttons], ['Label', 'Cancel'])
        self.button('Label')
        self.ui._capture_key('B')
        self.button('Accept')
        self.await_annotation()
        self.assertEqual(pickle.loads(self.path.read_bytes())['peak_labels'], {450: 'B'})

    def test_absorption_mode_labels_valley_using_native_sensor_pixel(self):
        self.button('Modes')
        self.button('Emission')
        self.button('Back')
        self.assertTrue(self.ui._absorption)
        np.testing.assert_equal(self.ui._peaks.indices, [350])
        self.label('valley')
        self.assertEqual(self.ui._review_labels, {350: 'valley'})
        self.assertEqual(pickle.loads(self.path.read_bytes())['peak_labels'], {350: 'valley'})

    def test_label_hitbox_delete_cancel_and_reopen_persistence(self):
        self.label('first')
        self.label('second', index=1)
        self.draw()
        self.assertEqual(set(self.ui._annotation_rects), {250, 450})
        saved = self.path.read_bytes()
        self.tap(self.ui._annotation_rects[250].center)
        self.assertEqual([b[0] for b in self.ui.buttons], ['Delete', 'Cancel'])
        self.assertEqual(self.ui._annotation_pixel, 250)
        self.button('Cancel')
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertIsNone(self.ui._peaks.marker)
        self.draw()
        self.tap(self.ui._annotation_rects[250].center)
        self.button('Delete')
        self.await_annotation()
        self.assertEqual(self.ui._review_labels, {450: 'second'})
        self.assertIsNone(self.ui._peaks.marker)
        self.draw()
        self.assertEqual(set(self.ui._annotation_rects), {450})
        self.button('Back')
        self.open_selected()
        self.assertEqual(self.ui._review_labels, {450: 'second'})
        expected = dict(self.record, peak_labels={450: 'second'})
        np.testing.assert_equal(pickle.loads(self.path.read_bytes()), expected)

    def test_pending_write_blocks_duplicate_accept_and_failure_can_retry(self):
        self.select()
        self.button('Label')
        self.ui._capture_key('x')
        pending = Future()
        with patch.object(self.ui.review, 'save_peak_label', return_value=pending) as save:
            self.button('Accept')
            self.assertEqual(self.ui.buttons, [])
            self.ui._accept_annotation()
            self.ui._cancel_annotation()
            self.ui._capture_key('y')
            self.assertEqual(self.ui._capture_name, 'x')
            self.assertEqual(self.ui.mode, 'annotation')
            save.assert_called_once_with(250, 'x')
            pending.set_exception(OSError('Disk full'))
            with self.assertLogs('spectrometer.ui', level='ERROR'):
                self.ui._poll_capture()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.ui.mode, 'annotation')
        self.assertIn('failed', self.ui._capture_message.lower())
        self.assertEqual(self.ui._review_labels, {})
        self.button('Accept')
        self.await_annotation()
        self.assertEqual(self.ui._review_labels, {250: 'x'})

    def test_labels_survive_channel_filtering_and_can_be_deleted_without_visible_peaks(self):
        self.label('line')
        self.button('Modes')
        self.button('Red')
        self.ui.review.filter_future.result(timeout=5)
        self.ui._poll_review()
        self.button('Back')
        self.assertEqual(len(self.ui._peaks.indices), 0)
        self.assertEqual(self.ui._review_labels, {250: 'line'})
        self.draw()
        self.tap(self.ui._annotation_rects[250].center)
        self.assertEqual([b[0] for b in self.ui.buttons], ['Delete', 'Cancel'])
        self.button('Delete')
        self.await_annotation()
        self.assertEqual(self.ui._review_labels, {})
        self.button('Modes')
        self.button('Red')
        self.button('Back')
        self.assertEqual(self.ui._review_labels, {})
        self.assertEqual(pickle.loads(self.path.read_bytes())['peak_labels'], {})

    def test_delete_failure_keeps_label_and_offers_retry_or_cancel(self):
        self.label('keep')
        self.draw()
        self.tap(self.ui._annotation_rects[250].center)
        saved = self.path.read_bytes()
        pending = Future()
        with patch.object(self.ui.review, 'save_peak_label', return_value=pending) as save:
            self.button('Delete')
            self.ui._delete_annotation()
            self.select(1)
            self.assertEqual(self.ui._annotation_pixel, 250)
            save.assert_called_once_with(250, None)
            pending.set_exception(OSError('Disk full'))
            with self.assertLogs('spectrometer.ui', level='ERROR'):
                self.ui._poll_capture()
        self.assertEqual(self.ui._review_labels, {250: 'keep'})
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertEqual([b[0] for b in self.ui.buttons], ['Delete', 'Cancel'])
        self.assertIn('failed', self.ui._peak_message.lower())
        self.button('Cancel')
        self.assertIsNone(self.ui._peaks.marker)
        self.draw()
        self.tap(self.ui._annotation_rects[250].center)
        self.button('Delete')
        self.await_annotation()
        self.assertEqual(self.ui._review_labels, {})

    def test_scale_dialog_retargets_without_creating_review_annotation(self):
        self.ui._scale_active = True
        self.select(0)
        self.assertEqual([b[0] for b in self.ui.buttons], ['Label', 'Back'])
        self.select(1)
        selected = self.ui._peaks.indices[self.ui._peaks.selected]
        self.assertEqual(selected, 450)
        self.assertEqual(self.ui._peak_message, 'Pixel 450')
        self.button('Back')
        self.assertIsNone(self.ui._peaks.marker)
        self.assertFalse(self.ui._peak_dialog)
        self.assertEqual(self.path.read_bytes(), self.original)
