"""Strict atomic recovery journal. Write errors propagate; corrupt data is retained."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from .tariff import ExportError


class RecoveryFile:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def read(self) -> dict | None:
        try:
            with self.path.open('r', encoding='utf-8') as handle:
                envelope = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise ExportError('Recovery file cannot be read. Keep it intact and recover from a backup; no new export is allowed.') from exc
        if not isinstance(envelope, dict) or envelope.get('storage_version') != 1:
            raise ExportError('Unsupported recovery-file format. Do not delete the file.')
        data = envelope.get('data')
        if not isinstance(data, dict) or 'snapshot' not in data or 'last_snapshot' not in data:
            raise ExportError('Recovery-file data is incomplete. Do not overwrite it.')
        return data

    def write(self, data: dict) -> None:
        # Serialise before creating/replacing anything; reject NaN/Infinity.
        payload = json.dumps({'storage_version': 1, 'data': data}, ensure_ascii=True,
                             allow_nan=False, separators=(',', ':'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + '.', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            # HA runs on Linux. Sync the rename as well as the file contents.
            if hasattr(os, 'O_DIRECTORY'):
                directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
