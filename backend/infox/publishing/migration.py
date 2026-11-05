"""Offline, backed-up migration; preserve IDs and every inbound foreign key."""
import sqlite3

from ..run_models import RunError
from .models import PublicationRecord, TERMINAL


def create_publications(conn, name='publications'):
    if name not in ('publications', 'publications_rebuild'):
        raise ValueError('invalid table')
    conn.execute(f'CREATE TABLE IF NOT EXISTS {name} (id TEXT PRIMARY KEY, '
        'run_id TEXT NOT NULL REFERENCES runs(id), video_id TEXT NOT NULL, '
        'record TEXT NOT NULL, attempt_number INTEGER NOT NULL, '
        'previous_publication_id TEXT REFERENCES publications(id), '
        'active INTEGER NOT NULL CHECK(active IN (0,1)), UNIQUE(video_id,attempt_number))')


def migrate_publications(root):
    base = root / '.runtime'
    path = base / 'infox-runs.sqlite3'
    if not path.exists():
        return
    if base.resolve() != base or path.is_symlink() or path.is_junction():
        raise RunError('storage_error', 503)
    try:
        with sqlite3.connect(path, timeout=5, isolation_level=None) as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(publications)')}
            if not columns or 'attempt_number' in columns:
                return
            # SQLite's documented table rebuild procedure requires FK enforcement
            # to be disabled outside the transaction, before any service requests.
            backup = base / 'infox-runs.before-publication-attempts.sqlite3'
            if backup.is_symlink() or backup.is_junction():
                raise RunError('storage_error', 503)
            if not backup.exists():
                with sqlite3.connect(backup) as snapshot:
                    conn.backup(snapshot)
            conn.execute('PRAGMA foreign_keys=OFF')
            conn.execute('BEGIN IMMEDIATE')
            try:
                # Another startup may have completed the migration while we waited.
                if 'attempt_number' in {r[1] for r in conn.execute('PRAGMA table_info(publications)')}:
                    conn.rollback()
                    return
                create_publications(conn, 'publications_rebuild')
                for id, run_id, video_id, raw in conn.execute('SELECT id,run_id,video_id,record FROM publications'):
                    record = PublicationRecord.model_validate_json(raw)
                    if (record.id, record.run_id, record.video_id) != (id, run_id, video_id):
                        raise ValueError('invalid identity')
                    conn.execute('INSERT INTO publications_rebuild VALUES (?,?,?,?,?,?,?)',
                        (id, run_id, video_id, raw, 1, None, int(record.stage not in TERMINAL)))
                conn.execute('DROP TABLE publications')
                conn.execute('ALTER TABLE publications_rebuild RENAME TO publications')
                conn.execute('CREATE UNIQUE INDEX publications_one_active ON publications(video_id) WHERE active=1')
                if conn.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('foreign key mismatch')
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.execute('PRAGMA foreign_keys=ON')
    except (sqlite3.Error, OSError, ValueError):
        raise RunError('publication_migration_failed', 503) from None
