import json
import os
from pathlib import Path

import pytest

from pwe_under_test.recovery import RecoveryFile
from pwe_under_test.tariff import ExportError


def test_missing_file_is_not_a_pending_session(tmp_path):
    assert RecoveryFile(tmp_path/'recovery.json').read() is None


def test_atomic_roundtrip_and_private_permissions(tmp_path):
    store = RecoveryFile(tmp_path/'recovery.json')
    data = {'snapshot': {'some': 'example'}, 'last_snapshot': None}
    store.write(data)
    assert store.read() == data
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob('*.tmp'))


def test_corrupt_json_is_not_deleted_or_treated_as_empty(tmp_path):
    store = RecoveryFile(tmp_path/'recovery.json')
    store.path.write_text('{incomplete')
    with pytest.raises(ExportError, match='cannot be read'):
        store.read()
    assert store.path.read_text() == '{incomplete'


def test_wrong_storage_version_is_rejected(tmp_path):
    store = RecoveryFile(tmp_path/'recovery.json')
    store.path.write_text(json.dumps({'storage_version': 99, 'data': {}}))
    with pytest.raises(ExportError, match='Unsupported'):
        store.read()


def test_failed_rename_preserves_previous_journal(tmp_path, monkeypatch):
    store = RecoveryFile(tmp_path/'recovery.json')
    old = {'snapshot': None, 'last_snapshot': None}
    store.write(old)
    def fail(*args):
        raise OSError('Synthetic disk failure')
    monkeypatch.setattr(os,'replace',fail)
    with pytest.raises(OSError):
        store.write({'snapshot': {'pending': True}, 'last_snapshot': None})
    assert store.read() == old
    assert not list(tmp_path.glob('*.tmp'))


def test_bad_serialization_never_replaces_journal(tmp_path):
    store = RecoveryFile(tmp_path/'recovery.json')
    old = {'snapshot': None, 'last_snapshot': None}
    store.write(old)
    with pytest.raises(ValueError):
        store.write({'snapshot': {'number': float('nan')}, 'last_snapshot': None})
    assert store.read() == old
