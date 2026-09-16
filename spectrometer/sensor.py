"""Small full-sensor preview and sensor-coordinate ROI selection."""

import pickle
from pathlib import Path

import cv2
import numpy as np
import pygame

from .camera import PACKED_12_FORMATS


def load_sensor(path):
    with Path(path).open('rb') as source:
        record = pickle.load(source)
    config = record.get('instrument_settings', {}).get(
        'raw_camera_format', record.get('raw_camera_format', {}))
    width, height = config['size']
    averaged = record.get('averaged_camera_output')
    if averaged is not None:
        from .review import raw_rgb, record_roi
        rgb = raw_rgb(record)
        bounds = record_roi(record)
        scale = min(480 / rgb.shape[1], 256 / rgb.shape[0])
        size = (round(rgb.shape[1] * scale), round(rgb.shape[0] * scale))
        small = cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)
        return small.tobytes(), size, (width, height), bounds
    fmt = config['format']
    if fmt not in PACKED_12_FORMATS or width % 2 or height % 2:
        raise ValueError('Unsupported raw camera format')
    raw = np.asarray(record['raw_camera_output'])
    stride = config.get('stride', width * 3 // 2)
    if raw.dtype != np.uint8 or raw.size != height * stride:
        raise ValueError('Invalid packed camera buffer')
    packed = raw.reshape(height, stride)[:, :width * 3 // 2]
    scale = min(480 / width, 256 / height)
    size = (round(width * scale), round(height * scale))
    # Resize each Bayer plane directly from its packed high bytes. Avoid a
    # full-resolution RGB allocation on the Pi; low four bits are preview-only.
    channels = {}
    for index, color in enumerate(fmt[1:5]):
        plane = packed[index // 2::2, index % 2::3]
        small = cv2.resize(plane, size, interpolation=cv2.INTER_AREA)
        if color in channels:
            small = cv2.addWeighted(channels[color], 0.5, small, 0.5, 0)
        channels[color] = small
    rgb = np.dstack([channels[color] for color in 'RGB'])
    return rgb.tobytes(), size, (width, height), (0, 0, width, height)


class SensorSelection:
    def __init__(self, preview, roi):
        if len(preview) == 3:
            pixels, size, self.resolution = preview
            self.bounds = (0, 0, *self.resolution)
        else:
            pixels, size, self.resolution, self.bounds = preview
        self.image = pygame.transform.flip(pygame.image.frombuffer(pixels, size, 'RGB'), True, False)
        self.rect = self.image.get_rect(center=(240, 128))
        self.roi = tuple(roi)
        self.start = None
        self.message = ''

    def point(self, position):
        x = min(1, max(0, (position[0] - self.rect.left) / self.rect.width))
        y = min(1, max(0, (position[1] - self.rect.top) / self.rect.height))
        x0, y0, x1, y1 = self.bounds
        return (x0 + round((1 - x) * (x1 - x0) / 2) * 2,
                y0 + round(y * (y1 - y0) / 2) * 2)

    def drag(self, position):
        end = self.point(position)
        self.roi = (min(self.start[0], end[0]), min(self.start[1], end[1]),
                    max(self.start[0], end[0]), max(self.start[1], end[1]))
        self.message = ''

    def draw(self, surface, font):
        surface.blit(self.image, self.rect)
        x0, y0, x1, y1 = self.roi
        bx0, by0, bx1, by1 = self.bounds
        width, height = bx1 - bx0, by1 - by0
        left = self.rect.left + round((bx1 - x1) * self.rect.width / width)
        top = self.rect.top + round((y0 - by0) * self.rect.height / height)
        right = self.rect.left + round((bx1 - x0) * self.rect.width / width)
        bottom = self.rect.top + round((y1 - by0) * self.rect.height / height)
        pygame.draw.rect(surface, '#ffd166', (left, top, max(1, right-left), max(1, bottom-top)), 2)
        if self.message:
            text = font.render(self.message, True, '#ffd166', '#111820')
            surface.blit(text, (8, 8))
