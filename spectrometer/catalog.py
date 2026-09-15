"""Rebuildable SQLite metadata index for spectrum pickle files."""

from contextlib import contextmanager
from datetime import datetime, timezone
import logging
from pathlib import Path
import pickle
import sqlite3
import threading
import time

from .performance import PerformanceMetrics


LOGGER = logging.getLogger(__name__)
CATALOG_NAME = '.spectrometer_index.sqlite3'
SCHEMA_VERSION = 1
DEEP_SCAN_INTERVAL = 3600
METRICS = PerformanceMetrics('catalog')


def timestamp_id(timestamp):
    """Return an exact integer millisecond ID without floating-point rounding."""
    if timestamp.tzinfo is None:
        timestamp = timestamp.astimezone()
    elapsed = timestamp.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (elapsed.days * 86400 + elapsed.seconds) * 1000 + elapsed.microseconds // 1000


def display_name(record):
    name = record.get('name', '')
    return name.strip() if isinstance(name, str) and name.strip() else 'Unnamed spectrum'


def record_timestamp(path, record):
    """Use the saved timestamp, falling back to the legacy filename format."""
    timestamp = record.get('timestamp')
    if isinstance(timestamp, datetime):
        return timestamp
    try:
        return datetime.strptime(path.stem, 'spectrum-%Y-%m-%d-%H-%M-%S').astimezone()
    except ValueError as exc:
        raise ValueError('Spectrum has no valid timestamp') from exc


def metadata(path, record, stat=None):
    timestamp = record_timestamp(path, record)
    stat = stat or path.stat()
    return {
        'filename': path.name,
        'spectrum_id': timestamp_id(timestamp),
        'name': display_name(record),
        'display_timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
        'size': stat.st_size,
        'mtime_ns': stat.st_mtime_ns,
        'readable': 1,
    }


class DuplicateSpectrumID(ValueError):
    pass


class SpectrumCatalog:
    """Open a connection per operation so UI/API/background threads can share it."""

    def __init__(self, directory=None, persistent=False):
        self.directory = Path.home() if directory is None else Path(directory)
        self.path = self.directory / CATALOG_NAME
        self._schema_lock = threading.Lock()
        self._db_lock = threading.RLock()
        self._persistent = persistent
        self._connection = None
        self._reconcile_lock = threading.Lock()
        self._last_deep_scan = time.monotonic()
        self._fallback_rows = {}
        self.available = True
        try:
            self._ensure_schema()
        except sqlite3.DatabaseError as exc:
            if not any(text in str(exc).lower() for text in
                       ('malformed', 'not a database', 'encrypted')):
                self.available = False
                LOGGER.error('Spectrum metadata index unavailable: %s', exc)
                return
            # Preserve a damaged cache for diagnosis. Pickles remain the source
            # of truth and rebuild the replacement on the next reconciliation.
            backup = self.path.with_name(self.path.name + '.corrupt')
            self.close()
            try:
                self.path.replace(backup)
            except OSError as backup_error:
                self.available = False
                LOGGER.error('Cannot preserve corrupt metadata index: %s', backup_error)
                return
            LOGGER.exception('Rebuilding corrupt spectrum metadata index')
            self._ensure_schema()
        except OSError as exc:
            self.available = False
            LOGGER.error('Spectrum metadata index unavailable: %s', exc)

    @contextmanager
    def _connect(self):
        if self._persistent:
            lock = self._db_lock
            lock.acquire()
            if self._connection is None:
                self._connection = sqlite3.connect(self.path, timeout=5,
                                                   check_same_thread=False)
                self._connection.row_factory = sqlite3.Row
            connection = self._connection
        else:
            lock = None
            connection = sqlite3.connect(self.path, timeout=5)
            connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA busy_timeout=5000')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            if self._persistent:
                lock.release()
            else:
                connection.close()

    def close(self):
        with self._db_lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _ensure_schema(self):
        with self._schema_lock, self._connect() as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise RuntimeError(f'Unsupported spectrum catalog version {version}')
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS spectra (
                    filename TEXT PRIMARY KEY,
                    spectrum_id INTEGER,
                    name TEXT,
                    display_timestamp TEXT,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    readable INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS spectra_id ON spectra(spectrum_id);
                CREATE INDEX IF NOT EXISTS spectra_time ON spectra(spectrum_id DESC);
            ''')
            connection.execute(f'PRAGMA user_version={SCHEMA_VERSION}')

    def _upsert_values(self, values):
        if not self.available:
            self._fallback_rows[values['filename']] = dict(values)
            return
        self._upsert_many((values,))

    def _upsert_many(self, values):
        if not self.available:
            self._fallback_rows.update((value['filename'], dict(value)) for value in values)
            return
        with self._connect() as connection:
            connection.executemany('''
                INSERT INTO spectra(filename, spectrum_id, name, display_timestamp,
                                    size, mtime_ns, readable)
                VALUES(:filename, :spectrum_id, :name, :display_timestamp,
                       :size, :mtime_ns, :readable)
                ON CONFLICT(filename) DO UPDATE SET
                    spectrum_id=excluded.spectrum_id,
                    name=excluded.name,
                    display_timestamp=excluded.display_timestamp,
                    size=excluded.size,
                    mtime_ns=excluded.mtime_ns,
                    readable=excluded.readable
            ''', values)

    def upsert_record(self, path, record):
        path = Path(path)
        self._upsert_values(metadata(path, record))

    def _mark_unreadable(self, path, stat):
        self._upsert_values({
            'filename': path.name, 'spectrum_id': None,
            'name': 'Unreadable spectrum', 'display_timestamp': '',
            'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'readable': 0,
        })

    def reconcile_paths(self, paths):
        """Unpickle only files whose size/mtime signature is absent or changed."""
        started = time.monotonic()
        paths = [Path(path) for path in paths]
        if self.available:
            with self._connect() as connection:
                known = {row['filename']: (row['size'], row['mtime_ns']) for row in
                         connection.execute('SELECT filename,size,mtime_ns FROM spectra')}
        else:
            known = {name: (row['size'], row['mtime_ns'])
                     for name, row in self._fallback_rows.items()}
        updates = []
        removals = []
        misses = 0
        for path in paths:
            try:
                stat = path.stat()
            except FileNotFoundError:
                removals.append(path.name)
                continue
            signature = (stat.st_size, stat.st_mtime_ns)
            if known.get(path.name) == signature:
                continue
            misses += 1
            try:
                with path.open('rb') as source:
                    record = pickle.load(source)
                updates.append(metadata(path, record, stat))
            except Exception:
                LOGGER.exception('Cannot index spectrum %s', path.name)
                updates.append({
                    'filename': path.name, 'spectrum_id': None,
                    'name': 'Unreadable spectrum', 'display_timestamp': '',
                    'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'readable': 0,
                })
        if updates:
            self._upsert_many(updates)
        if removals:
            if self.available:
                with self._connect() as connection:
                    connection.executemany('DELETE FROM spectra WHERE filename=?',
                                           [(name,) for name in removals])
            else:
                for name in removals:
                    self._fallback_rows.pop(name, None)
        METRICS.add('files_scanned', len(paths))
        METRICS.add('pickle_reads', misses)
        METRICS.add('reconcile_ms', (time.monotonic() - started) * 1000)

    def reconcile_all(self, force=False):
        # Normal requests compare cheap directory entries and inspect only new
        # files. A periodic signature audit detects files modified in place.
        with self._reconcile_lock:
            now = time.monotonic()
            paths = list(self.directory.glob('spectrum-*.pkl'))
            present = {path.name for path in paths}
            if self.available:
                with self._connect() as connection:
                    indexed = [row[0] for row in connection.execute('SELECT filename FROM spectra')]
            else:
                indexed = list(self._fallback_rows)
            unknown = [path for path in paths if path.name not in indexed and path.is_file()]
            if unknown:
                self.reconcile_paths(unknown)
            if force or now - self._last_deep_scan >= DEEP_SCAN_INTERVAL:
                self.reconcile_paths(paths)
                self._last_deep_scan = time.monotonic()
            removed = [name for name in indexed if name not in present]
            if removed and self.available:
                with self._connect() as connection:
                    connection.executemany('DELETE FROM spectra WHERE filename=?',
                                           [(name,) for name in removed])
            elif removed:
                for name in removed:
                    self._fallback_rows.pop(name, None)

    def rows(self):
        if not self.available:
            return sorted(self._fallback_rows.values(),
                          key=lambda row: (row['readable'], row['spectrum_id'] or -1,
                                           row['filename']), reverse=True)
        with self._connect() as connection:
            return [dict(row) for row in connection.execute('''
                SELECT filename,spectrum_id,name,display_timestamp,size,mtime_ns,readable
                FROM spectra ORDER BY readable DESC, spectrum_id DESC, filename DESC
            ''')]

    def list_api(self):
        if not self.available:
            return [{'id': row['spectrum_id'], 'name': row['name'],
                     'timestamp': row['display_timestamp']} for row in self.rows()
                    if row['readable']]
        with self._connect() as connection:
            return [{'id': row['spectrum_id'], 'name': row['name'],
                     'timestamp': row['display_timestamp']} for row in connection.execute('''
                SELECT spectrum_id,name,display_timestamp FROM spectra
                WHERE readable=1 ORDER BY spectrum_id DESC, filename DESC
            ''')]

    def find(self, spectrum_id):
        if self.available:
            with self._connect() as connection:
                rows = connection.execute('''
                    SELECT filename FROM spectra WHERE readable=1 AND spectrum_id=?
                    ORDER BY filename
                ''', (int(spectrum_id),)).fetchall()
        else:
            rows = [row for row in self.rows()
                    if row['readable'] and row['spectrum_id'] == int(spectrum_id)]
        if len(rows) > 1:
            raise DuplicateSpectrumID(f'Multiple spectra have ID {spectrum_id}')
        return self.directory / rows[0]['filename'] if rows else None

    def remove(self, filename):
        filename = Path(filename).name
        if not self.available:
            self._fallback_rows.pop(filename, None)
            return
        with self._connect() as connection:
            connection.execute('DELETE FROM spectra WHERE filename=?', (filename,))

    def row_map(self):
        return {row['filename']: row for row in self.rows()}
