import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'studio.sqlite3'
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, count INTEGER NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, error TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
                    position INTEGER NOT NULL, concept TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'planned', image_path TEXT DEFAULT '',
                    error TEXT DEFAULT '', review TEXT DEFAULT 'pending',
                    similarity TEXT DEFAULT '', attempts INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS items_batch ON items(batch_id, position);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT,
                    message TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(items)')}
            for name, default in {
                'stage': 'queued', 'progress_message': 'Đang chờ lượt tạo ảnh.',
                'started_at': '', 'finished_at': '', 'stage_changed_at': '',
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE items ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
            # Legacy completed/failed rows have no reliable elapsed time; leave timestamps unknown.
            db.execute("UPDATE items SET stage=status, progress_message='' WHERE stage='queued' AND status IN ('completed','failed','generating')")

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE items SET status='failed', stage='failed', progress_message='Tiến trình bị ngắt. Cần kiểm tra trước khi thử lại.', finished_at=?, stage_changed_at=?, error='Tiến trình bị ngắt khi đang tạo ảnh. Kiểm tra trước khi thử lại để tránh tạo trùng.' WHERE status='generating'", (now(), now()))
            db.execute("UPDATE batches SET status='paused', error='Ứng dụng vừa khởi động lại. Có thể tiếp tục các mục chưa chạy.' WHERE status IN ('queued','planning','generating','pausing')")

    def create(self, count):
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1000:
            raise ValueError('Số lượng phải là số nguyên từ 1 đến 1.000.')
        batch_id = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO batches(id,count,status,created_at) VALUES(?,?,?,?)', (batch_id, count, 'queued', now()))
        self.event(batch_id, f'Đã tạo batch {count} ảnh. Context sẽ được lập tự động.')
        return self.batch(batch_id)

    def batch(self, batch_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
            if row is None:
                raise KeyError('Không tìm thấy batch.')
            result = dict(row)
            counts = dict(db.execute('SELECT status, count(*) FROM items WHERE batch_id=? GROUP BY status', (batch_id,)).fetchall())
            result.update(planned=sum(counts.values()), completed=counts.get('completed', 0), failed=counts.get('failed', 0), generating=counts.get('generating', 0), waiting=counts.get('planned', 0))
            return result

    def batches(self):
        with self.db() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM batches ORDER BY created_at DESC, rowid DESC')]
        return [self.batch(i) for i in ids]

    def set_batch(self, batch_id, status, error=''):
        with self.db() as db:
            db.execute('UPDATE batches SET status=?,error=? WHERE id=?', (status, error, batch_id))

    def event(self, batch_id, message):
        with self.db() as db:
            db.execute('INSERT INTO events(batch_id,message,created_at) VALUES(?,?,?)', (batch_id, message, now()))

    def events(self, batch_id):
        with self.db() as db:
            rows = db.execute('SELECT message,created_at FROM events WHERE batch_id=? ORDER BY id DESC LIMIT 40', (batch_id,)).fetchall()
            return [dict(r) for r in rows]

    def history(self):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT concept FROM items ORDER BY rowid')]

    def add_concepts(self, batch_id, concepts):
        with self.db() as db:
            size = db.execute('SELECT count(*) FROM items WHERE batch_id=?', (batch_id,)).fetchone()[0]
            target = db.execute('SELECT count FROM batches WHERE id=?', (batch_id,)).fetchone()[0]
            if size + len(concepts) > target:
                raise ValueError('Số context vượt số lượng batch.')
            for offset, concept in enumerate(concepts):
                db.execute('INSERT INTO items(id,batch_id,position,concept) VALUES(?,?,?,?)', (uuid.uuid4().hex, batch_id, size + offset, json.dumps(concept, ensure_ascii=False)))

    @staticmethod
    def unpack(row):
        result = dict(row)
        concept = json.loads(result.pop('concept'))
        result.update(concept)
        result['image_url'] = f"/images/{result['id']}.jpg" if result['image_path'] else None
        return result

    def items(self, batch_id):
        with self.db() as db:
            return [self.unpack(r) for r in db.execute('SELECT * FROM items WHERE batch_id=? ORDER BY position', (batch_id,))]

    def completed_items(self):
        with self.db() as db:
            return [self.unpack(row) for row in db.execute("SELECT * FROM items WHERE status='completed' AND image_path != ''")]

    def item(self, item_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
            if row is None:
                raise KeyError('Không tìm thấy ảnh.')
            return self.unpack(row)

    def set_prompt(self, item_id, prompt):
        """Persist the actual provider prompt, keeping the original plan for audit."""
        with self.db() as db:
            row = db.execute('SELECT concept FROM items WHERE id=?', (item_id,)).fetchone()
            if row is None:
                raise KeyError('Không tìm thấy ảnh.')
            concept = json.loads(row[0])
            concept.setdefault('planned_prompt', concept['prompt'])
            concept['prompt'] = prompt
            db.execute('UPDATE items SET concept=? WHERE id=?', (json.dumps(concept, ensure_ascii=False), item_id))

    def set_item(self, item_id, **fields):
        allowed = {'status', 'image_path', 'error', 'review', 'similarity', 'attempts', 'stage', 'progress_message', 'started_at', 'finished_at', 'stage_changed_at'}
        if not fields or not set(fields) <= allowed:
            raise ValueError('Trường cập nhật không hợp lệ.')
        with self.db() as db:
            db.execute(f"UPDATE items SET {','.join(k+'=?' for k in fields)} WHERE id=?", (*fields.values(), item_id))

    def settings(self):
        with self.db() as db:
            return {r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM settings')}

    def save_settings(self, values):
        with self.db() as db:
            for key, value in values.items():
                db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))
