"""Small, clickable spectrum annotations rendered directly with pygame."""

import pygame


ANNOTATION_COLOR = "#ffd166"
ANNOTATION_BACKGROUND = "#1b2632"


def _fit_text(font, text, width):
    if font.size(text)[0] <= width:
        return text
    suffix = "..."
    while suffix and font.size(suffix)[0] > width:
        suffix = suffix[:-1]
    while text and font.size(text + suffix)[0] > width:
        text = text[:-1]
    return text + suffix


def draw_peak_labels(surface, font, area, intensity, maximum, pixel_origin, labels):
    """Draw saved labels and return their touch targets in surface coordinates.

    Label keys retain sensor pixel coordinates. Like ``SpectrumPlot``, drawing
    reverses the X direction without changing the stored data. Labels remain
    visible when channel or emission/absorption modes change the curve.
    """
    area = pygame.Rect(area)
    if not len(intensity) or area.width < 1 or area.height < 1 or maximum <= 0:
        return {}

    padding = 4
    text_width = max(0, min(180, area.width - 2 * padding))
    height = min(area.height, max(22, font.get_height() + 2 * padding))
    rows = list(range(area.top, area.bottom - height + 1, height + 2))
    occupied = []
    annotations = []
    for sensor_pixel, label in sorted(labels.items()):
        pixel = sensor_pixel - pixel_origin
        if not 0 <= pixel < len(intensity):
            continue
        x = area.right - 1 - round(pixel * (area.width - 1) / max(1, len(intensity) - 1))
        y = area.bottom - 1 - round(
            min(maximum, max(0, intensity[pixel])) * (area.height - 1) / maximum
        )
        text = font.render(_fit_text(font, label, text_width), True, ANNOTATION_COLOR)
        width = min(area.width, max(22, text.get_width() + 2 * padding))
        candidates = []
        for top in rows:
            rect = pygame.Rect(0, top, width, height)
            rect.centerx = x
            rect.clamp_ip(area)
            overlap = sum(rect.clip(other).width * rect.clip(other).height for other in occupied)
            candidates.append((overlap, rect))
            if not overlap:
                break
        rect = min(candidates, key=lambda item: item[0])[1]
        occupied.append(rect)
        annotations.append((sensor_pixel, (x, y), text, rect))

    original_clip = surface.get_clip()
    surface.set_clip(original_clip.clip(area))
    try:
        # Paint leaders before boxes so another label's line never crosses text.
        for _, point, _, rect in annotations:
            endpoint = rect.midbottom if point[1] >= rect.centery else rect.midtop
            pygame.draw.line(surface, ANNOTATION_COLOR, point, endpoint)
            pygame.draw.circle(surface, ANNOTATION_COLOR, point, 4, 1)
        for _, _, text, rect in annotations:
            pygame.draw.rect(surface, ANNOTATION_BACKGROUND, rect, border_radius=3)
            pygame.draw.rect(surface, ANNOTATION_COLOR, rect, 1, border_radius=3)
            surface.blit(text, text.get_rect(center=rect.center))
    finally:
        surface.set_clip(original_clip)
    return {pixel: rect for pixel, _, _, rect in annotations}
