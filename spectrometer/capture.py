"""Write complete capture files without overwriting an existing spectrum."""

import os
import logging
from pathlib import Path
import pickle
import tempfile


LOGGER = logging.getLogger(__name__)


def save_capture(record, directory):
    directory = Path(directory)
    filename = record["timestamp"].strftime("spectrum-%Y-%m-%d-%H-%M-%S.pkl")
    destination = directory / filename
    # Publish only after serialization finishes. An existing same-second capture
    # is preserved, and a failed write never leaves a partial .pkl file behind.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".spectrum-", delete=False) as output:
            temporary = Path(output.name)
            pickle.dump(record, output, protocol=pickle.HIGHEST_PROTOCOL)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, destination)
        try:
            from .catalog import SpectrumCatalog
            SpectrumCatalog(directory).upsert_record(destination, record)
        except Exception:
            # The pickle is authoritative and was already published. A later
            # reconciliation repairs the rebuildable catalog.
            LOGGER.exception("Saved %s but could not update its metadata index", destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink()


def rename_capture(path, name):
    """Atomically replace only the display name, preserving spectrum identity."""
    path = Path(path)
    name = name.strip()
    if not name:
        raise ValueError('Enter a name')
    with path.open('rb') as source:
        record = pickle.load(source)
    record['name'] = name
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.spectrum-', delete=False) as output:
            temporary = Path(output.name)
            pickle.dump(record, output, protocol=pickle.HIGHEST_PROTOCOL)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
        try:
            from .catalog import SpectrumCatalog
            catalog = SpectrumCatalog(path.parent)
            try:
                catalog.upsert_record(path, record)
            finally:
                catalog.close()
        except Exception:
            LOGGER.exception('Renamed %s but could not update its metadata index', path)
        return name
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
