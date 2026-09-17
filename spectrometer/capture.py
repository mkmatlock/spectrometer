"""Write complete capture files without overwriting an existing spectrum."""

import os
import logging
from copy import deepcopy
from pathlib import Path
import pickle
import tempfile
import threading


LOGGER = logging.getLogger(__name__)
# UI calibration and REST rename/delete operations share the same files.
_MUTATION_LOCK = threading.RLock()


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
    name = name.strip()
    if not name:
        raise ValueError('Enter a name')
    def update(record):
        record['name'] = name
    _update_capture(path, update)
    return name


def update_scale_calibration(path, labels):
    """Replace only this capture's wavelength labels, retaining its other calibration."""
    labels = deepcopy(labels)
    def update(record):
        record['instrument_settings']['calibration_settings']['scale'] = labels
    _update_capture(path, update)
    return labels


def update_peak_label(path, pixel, label):
    """Atomically add or remove one user annotation, preserving the saved spectrum."""
    from .review import record_peak_labels, record_roi

    if type(pixel) is not int:
        raise ValueError('Peak pixel must be an integer')
    if label is not None:
        if not isinstance(label, str):
            raise ValueError('Peak label must be text')
        label = label.strip()
        if not 1 <= len(label) <= 64:
            raise ValueError('Enter a label of 1 to 64 characters')
    saved_labels = {}

    def update(record):
        roi = record_roi(record)
        if not roi[0] <= pixel < roi[2]:
            raise ValueError('Peak pixel is outside the spectrum')
        labels = record_peak_labels(record)
        if label is None:
            labels.pop(pixel, None)
        else:
            labels[pixel] = label
        record['peak_labels'] = labels
        saved_labels.update(labels)

    _update_capture(path, update)
    return deepcopy(saved_labels)


def delete_capture(path):
    """Serialize deletion with metadata edits so a pending edit cannot restore a file."""
    with _MUTATION_LOCK:
        Path(path).unlink()


def _update_capture(path, update):
    with _MUTATION_LOCK:
        _replace_capture(Path(path), update)


def _replace_capture(path, update):
    with path.open('rb') as source:
        record = pickle.load(source)
    update(record)
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
            LOGGER.exception('Updated %s but could not update its metadata index', path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
