"""Current saved-spectrum fixtures shared by application tests."""

from copy import deepcopy
from datetime import datetime, timezone

import numpy as np

from spectrometer.camera import BAR_SIZE
from spectrometer.config import DEFAULTS


def spectrum_record(**overrides):
    roi = tuple(overrides.get('spectrum_roi', DEFAULTS['calibration']['sensor_area']))
    width, height = roi[2] - roi[0], roi[3] - roi[1]
    calibration = deepcopy(DEFAULTS['calibration'])
    calibration['sensor_area'] = roi
    record = {
        'timestamp': datetime(2026, 9, 12, 12, tzinfo=timezone.utc),
        'name': 'Test spectrum',
        'instrument_settings': {
            'exposure_time_us': 1000,
            'raw_camera_format': {'size': (4056, 3040), 'stride': 6112,
                                  'format': 'SBGGR12_CSI2P'},
            'averaged_camera_format': {'format': 'BGR888', 'size': (width, height),
                                       'origin': roi[:2], 'sensor_size': (4056, 3040)},
            'frame_averaging': 3,
            'intensity_calculation': 'rgb_channel_sum',
            'calibration_settings': calibration,
        },
        'averaged_camera_output': np.zeros((height, width, 3), np.uint8),
        'spectrum_roi': roi,
        'spectrum_intensity': np.zeros(width, np.int32),
        'spectrum_bar': bytes(BAR_SIZE[0] * BAR_SIZE[1] * 3),
        'spectrum_maximum': height * 255 * 3,
    }
    record.update(overrides)
    return record
