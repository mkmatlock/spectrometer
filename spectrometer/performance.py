"""Low-volume rolling performance counters enabled by --performance-debug."""

import logging
import threading
import time


LOGGER = logging.getLogger(__name__)


class PerformanceMetrics:
    def __init__(self, component, interval=5.0):
        self.component = component
        self.interval = interval
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._values = {}

    def add(self, name, value=1.0):
        if not LOGGER.isEnabledFor(logging.DEBUG):
            return
        now = time.monotonic()
        with self._lock:
            total, count, maximum = self._values.get(name, (0.0, 0, 0.0))
            self._values[name] = (total + value, count + 1, max(maximum, value))
            if now - self._started < self.interval:
                return
            elapsed = now - self._started
            values, self._values = self._values, {}
            self._started = now
        fields = []
        for metric, (total, count, maximum) in sorted(values.items()):
            if metric.endswith('_ms'):
                fields.append(f'{metric}=avg {total / count:.1f}, max {maximum:.1f}')
            else:
                fields.append(f'{metric}={total / elapsed:.2f}/s')
        LOGGER.debug('%s performance: %s', self.component, '; '.join(fields))
