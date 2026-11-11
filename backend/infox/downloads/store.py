import json
from datetime import date
from uuid import UUID, uuid4

from ..run_models import RunError, now
from .models import DownloadItem, DownloadRecord


def ensure_ready(pub, kind, *, automatic=False, retry=None):
    if retry is not None and (retry.publication_id != pub.id or retry.kind != kind):
        raise RunError('download_not_ready')
    origin = automatic or bool(retry and (retry.automatic or retry.automatic_origin))
    if (not pub.render_task_id or not pub.render_file_fingerprint
            or (kind == 'all' and not pub.uploaded_verified and not origin)):
        raise RunError('download_not_ready')
    return origin


class DownloadStore:
    def __init__(self, publications):
        self.publications = publications
        self.runs = publications.run_store
        with self.runs._transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS infox_downloads ('
                'id TEXT PRIMARY KEY, publication_id TEXT NOT NULL REFERENCES publications(id), '
                'automatic INTEGER NOT NULL, active INTEGER NOT NULL, record TEXT NOT NULL)')
            conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS download_one_active ON infox_downloads(publication_id) WHERE active=1')
            conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS download_one_auto ON infox_downloads(publication_id) WHERE automatic=1')
            conn.execute('CREATE TABLE IF NOT EXISTS infox_download_intents ('
                'intent_id TEXT PRIMARY KEY, digest TEXT NOT NULL, download_id TEXT NOT NULL REFERENCES infox_downloads(id))')

    @staticmethod
    def _decode(row):
        if row is None:
            raise RunError('download_not_found', 404)
        return DownloadRecord.model_validate_json(row['record'])

    def get(self, id):
        with self.runs._transaction() as conn:
            return self._decode(conn.execute('SELECT record FROM infox_downloads WHERE id=?', (id,)).fetchone())

    def history(self, publication_id):
        self.publications.get(publication_id)
        with self.runs._transaction() as conn:
            return [self._decode(row) for row in conn.execute(
                'SELECT record FROM infox_downloads WHERE publication_id=? ORDER BY rowid DESC', (publication_id,))]

    def create(self, publication_id, intent_id, kind, *, automatic=False, retry_of=None):
        intent_id = UUID(intent_id).hex
        digest = json.dumps([publication_id, kind, retry_of], separators=(',', ':'))
        with self.runs._transaction() as conn:
            from ..publishing.models import PublicationRecord
            pub_row = conn.execute('SELECT record FROM publications WHERE id=?',(publication_id,)).fetchone()
            if pub_row is None:
                raise RunError('publication_not_found',404)
            pub = PublicationRecord.model_validate_json(pub_row['record'])
            retry = self._decode(conn.execute('SELECT record FROM infox_downloads WHERE id=?',(retry_of,)).fetchone()) if retry_of else None
            origin = ensure_ready(pub,kind,automatic=automatic,retry=retry)
            alias = conn.execute('SELECT digest,download_id FROM infox_download_intents WHERE intent_id=?', (intent_id,)).fetchone()
            if alias:
                if alias['digest'] != digest:
                    raise RunError('intent_conflict')
                return self._decode(conn.execute('SELECT record FROM infox_downloads WHERE id=?', (alias['download_id'],)).fetchone()), False
            existing = conn.execute('SELECT record FROM infox_downloads WHERE publication_id=? AND '
                '(active=1 OR (automatic=1 AND ?=1)) ORDER BY rowid DESC LIMIT 1', (publication_id, int(automatic))).fetchone()
            existing = self._decode(existing) if existing else None
            queued = bool(automatic and existing and not existing.automatic)
            if existing and not queued:
                record, created = existing, False
            else:
                prefix = date.fromisoformat(pub.snapshot.market_date).strftime('%y%m%d')
                kinds = ('script', 'source', 'video') if kind == 'all' else ('video',)
                names = {'script':'대본.txt', 'source':'원본데이터.txt', 'video':'영상시황.mp4'}
                stamp = now()
                record = DownloadRecord(id=uuid4().hex, publication_id=publication_id, kind=kind,
                    automatic=automatic, automatic_origin=origin, created_at=stamp, updated_at=stamp,
                    state='queued' if queued else 'pending',
                    items=tuple(DownloadItem(kind=k, filename=f'{prefix}_{names[k]}') for k in kinds))
                conn.execute('INSERT INTO infox_downloads VALUES (?,?,?,?,?)',
                    (record.id, publication_id, int(automatic), int(not queued), record.model_dump_json()))
                created = True
            conn.execute('INSERT INTO infox_download_intents VALUES (?,?,?)', (intent_id, digest, record.id))
            return record, created

    def update(self, id, **changes):
        with self.runs._transaction() as conn:
            record = self._decode(conn.execute('SELECT record FROM infox_downloads WHERE id=?', (id,)).fetchone())
            updated = DownloadRecord.model_validate({**record.model_dump(), **changes,
                'version':record.version+1, 'updated_at':now()})
            conn.execute('UPDATE infox_downloads SET active=?,record=? WHERE id=?',
                (int(updated.state in ('pending','running')), updated.model_dump_json(), id))
            return updated

    def activate_queued(self, id):
        """Atomically claim a waiting automatic operation across database owners."""
        with self.runs._transaction() as conn:
            record = self._decode(conn.execute('SELECT record FROM infox_downloads WHERE id=?',(id,)).fetchone())
            if record.state != 'queued':
                return None
            if conn.execute('SELECT 1 FROM infox_downloads WHERE publication_id=? AND active=1',
                            (record.publication_id,)).fetchone():
                return False
            record = record.model_copy(update={'state':'pending','version':record.version+1,'updated_at':now()})
            conn.execute('UPDATE infox_downloads SET active=1,record=? WHERE id=?',(record.model_dump_json(),id))
            return True

    def interrupted(self):
        with self.runs._transaction() as conn:
            return [self._decode(row) for row in conn.execute(
                "SELECT record FROM infox_downloads WHERE active=1 OR json_extract(record,'$.state')='queued'")]
