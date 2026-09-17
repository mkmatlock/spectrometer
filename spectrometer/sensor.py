"""Small full-sensor preview and sensor-coordinate ROI selection."""

import pygame


class SensorSelection:
    def __init__(self, preview, roi):
        pixels, size, self.resolution = preview
        self.bounds = (0, 0, *self.resolution)
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
