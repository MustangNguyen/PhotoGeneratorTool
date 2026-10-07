import http.client
import json
import re
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app import build_server
from studio.artifacts import normalize_image as real_normalize_image
from studio.engine import Engine
from studio.store import Store


def make_concept(number=0):
    return {
        "title": f"Tiến trình {number}",
        "category": "Khoa học",
        "subject": f"Thiết bị alpha{number} và mẫu vật",
        "scene": f"Trạm khảo sát beta{number} trên cao nguyên",
        "story": f"Dụng cụ gamma{number} đang ghi lại địa tầng",
        "composition": f"Tiền cảnh chéo delta{number}, chủ thể giữa và hậu cảnh sâu",
        "palette": f"Lam khoáng và hổ phách epsilon{number}",
        "materials": f"Đá kính đồng zeta{number}",
        "key": f"alpha{number} survey beta{number} geology gamma{number}",
        "prompt": f"Bright realistic alpha{number} survey at beta{number} geology station.",
    }


def assert_iso_utc(testcase, value):
    testcase.assertIsInstance(value, str)
    parsed = datetime.fromisoformat(value)
    testcase.assertIsNotNone(parsed.tzinfo)
    testcase.assertEqual(0, parsed.utcoffset().total_seconds())
    return parsed


class ProgressProvider:
    def __init__(self, control):
        self.control = control

    def status(self):
        self.control.status_entered.set()
        if self.control.block_status:
            if not self.control.release_status.wait(5):
                raise AssertionError("test did not release provider status")
        return {"name": "fake", "ready": True, "text_ready": True, "message": "ready"}

    def plan(self, prompt):
        count = int(re.search(r"chính xác (\d+)", prompt).group(1))
        return {"concepts": [make_concept(number) for number in range(count)]}

    def review(self, prompt):
        payload = json.loads(prompt.split("CONTEXT CẦN DUYỆT\n", 1)[1].split("\n\nChỉ trả JSON", 1)[0])
        return {"reviews": [
            {"index": item["index"], "decision": "keep", "reason": "Cảnh hợp lý."}
            for item in payload
        ]}

    def generate(self, prompt, directory):
        self.control.generate_entered.set()
        if not self.control.release_generate.wait(5):
            raise AssertionError("test did not release provider generation")
        if self.control.fail_next:
            self.control.fail_next = False
            raise RuntimeError("synthetic provider failure")
        target = Path(directory) / "provider-source.png"
        Image.new("RGB", (300, 450), (32, 96, 160)).save(target)
        return target

    def cancel(self):
        self.control.release_status.set()
        self.control.release_generate.set()


class ProgressControl:
    def __init__(self):
        self.factory_entered = threading.Event()
        self.release_factory = threading.Event()
        self.status_entered = threading.Event()
        self.release_status = threading.Event()
        self.generate_entered = threading.Event()
        self.release_generate = threading.Event()
        self.block_worker_factory = False
        self.block_status = False
        self.fail_next = False
        self.factories = 0

    def factory(self):
        self.factories += 1
        # The first provider coordinates the run; later providers own image work.
        if self.factories > 1 and self.block_worker_factory:
            self.factory_entered.set()
            if not self.release_factory.wait(5):
                raise AssertionError("test did not release worker provider factory")
        return ProgressProvider(self)


class ItemProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.store.save_settings({"concurrency": 1})

    def tearDown(self):
        self.temp.cleanup()

    def create_engine(self, control, count=1):
        batch = self.store.create(count)
        self.store.add_concepts(batch["id"], [make_concept(number) for number in range(count)])
        return Engine(self.store, control.factory), batch

    def join(self, engine, timeout=10):
        self.assertIsNotNone(engine.thread)
        engine.thread.join(timeout)
        self.assertFalse(engine.thread.is_alive(), "engine did not finish")

    def test_item_exposes_each_real_generation_stage_and_timestamps(self):
        control = ProgressControl()
        control.block_worker_factory = True
        engine, batch = self.create_engine(control)
        saving_entered = threading.Event()
        release_saving = threading.Event()
        checking_entered = threading.Event()
        release_checking = threading.Event()

        def held_normalize(source, target):
            saving_entered.set()
            self.assertTrue(release_saving.wait(5), "test did not release normalization")
            return real_normalize_image(source, target)

        def held_near_image(path, candidates, cache):
            checking_entered.set()
            self.assertTrue(release_checking.wait(5), "test did not release similarity check")
            return None

        with patch("studio.engine.normalize_image", side_effect=held_normalize), patch(
            "studio.engine.near_image", side_effect=held_near_image
        ):
            engine.start(batch["id"])
            self.assertTrue(control.factory_entered.wait(5), "worker provider was not prepared")
            preparing = self.store.items(batch["id"])[0]
            self.assertEqual("generating", preparing["status"])
            self.assertEqual("preparing", preparing["stage"])
            self.assertTrue(preparing["progress_message"])
            started = assert_iso_utc(self, preparing["started_at"])
            self.assertFalse(preparing["finished_at"])
            assert_iso_utc(self, preparing["stage_changed_at"])

            control.release_factory.set()
            self.assertTrue(control.generate_entered.wait(5), "provider generation did not start")
            generating = self.store.items(batch["id"])[0]
            self.assertEqual("generating", generating["status"])
            self.assertEqual("generating", generating["stage"])
            self.assertEqual(preparing["started_at"], generating["started_at"])
            self.assertTrue(generating["progress_message"])

            control.release_generate.set()
            self.assertTrue(saving_entered.wait(5), "normalization did not start")
            saving = self.store.items(batch["id"])[0]
            self.assertEqual("generating", saving["status"])
            self.assertEqual("saving", saving["stage"])

            release_saving.set()
            self.assertTrue(checking_entered.wait(5), "similarity check did not start")
            checking = self.store.items(batch["id"])[0]
            self.assertEqual("generating", checking["status"])
            self.assertEqual("checking", checking["stage"])
            release_checking.set()
            self.join(engine)

        completed = self.store.items(batch["id"])[0]
        self.assertEqual("completed", completed["status"])
        self.assertEqual("completed", completed["stage"])
        finished = assert_iso_utc(self, completed["finished_at"])
        self.assertGreaterEqual(finished, started)
        self.assertTrue(completed["progress_message"])

    def test_failure_sets_terminal_progress_and_retry_clears_timestamps(self):
        control = ProgressControl()
        control.fail_next = True
        engine, batch = self.create_engine(control)
        engine.start(batch["id"])
        self.assertTrue(control.generate_entered.wait(5), "provider generation did not start")
        control.release_generate.set()
        self.join(engine)

        failed = self.store.items(batch["id"])[0]
        self.assertEqual("failed", failed["status"])
        self.assertEqual("failed", failed["stage"])
        self.assertIn("synthetic provider failure", failed["progress_message"])
        self.assertIn("synthetic provider failure", failed["error"])
        assert_iso_utc(self, failed["started_at"])
        assert_iso_utc(self, failed["finished_at"])

        control.generate_entered.clear()
        control.release_generate.clear()
        control.block_status = True
        control.status_entered.clear()
        control.release_status.clear()
        engine.retry(failed["id"])
        self.assertTrue(control.status_entered.wait(5), "retry did not start")
        reset = self.store.items(batch["id"])[0]
        self.assertEqual("planned", reset["status"])
        self.assertEqual("queued", reset["stage"])
        self.assertFalse(reset["started_at"])
        self.assertFalse(reset["finished_at"])

        control.release_status.set()
        self.assertTrue(control.generate_entered.wait(5), "retried generation did not start")
        control.release_generate.set()
        self.join(engine)
        retried = self.store.items(batch["id"])[0]
        self.assertEqual("completed", retried["stage"])
        self.assertEqual(2, retried["attempts"])

    def test_existing_database_is_migrated_additively_without_losing_items(self):
        old_root = self.root / "old-schema"
        old_root.mkdir()
        database = old_root / "studio.sqlite3"
        concept = make_concept(7)
        with closing(sqlite3.connect(database)) as db:
            db.executescript(
                """
                CREATE TABLE batches (
                    id TEXT PRIMARY KEY, count INTEGER NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, error TEXT DEFAULT ''
                );
                CREATE TABLE items (
                    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
                    position INTEGER NOT NULL, concept TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'planned', image_path TEXT DEFAULT '',
                    error TEXT DEFAULT '', review TEXT DEFAULT 'pending',
                    similarity TEXT DEFAULT '', attempts INTEGER DEFAULT 0
                );
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT,
                    message TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )
            db.execute(
                "INSERT INTO batches(id,count,status,created_at) VALUES(?,?,?,?)",
                ("legacy-batch", 1, "completed", "2026-01-01T00:00:00+00:00"),
            )
            db.execute(
                "INSERT INTO items(id,batch_id,position,concept,status,image_path,attempts) VALUES(?,?,?,?,?,?,?)",
                ("legacy-item", "legacy-batch", 0, json.dumps(concept), "completed", "/kept.png", 1),
            )
            db.commit()

        migrated = Store(old_root)
        item = migrated.item("legacy-item")
        self.assertEqual("completed", item["status"])
        self.assertEqual("/kept.png", item["image_path"])
        self.assertEqual(concept["title"], item["title"])
        for field in ("stage", "progress_message", "started_at", "finished_at", "stage_changed_at"):
            self.assertIn(field, item)
        with closing(sqlite3.connect(database)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(items)")}
        self.assertTrue(
            {"stage", "progress_message", "started_at", "finished_at", "stage_changed_at"} <= columns
        )


class ProgressHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.control = ProgressControl()
        self.server = build_server(self.root, port=0, provider_factory=self.control.factory)
        self.server.store.save_settings({"concurrency": 1})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.control.release_factory.set()
        self.control.release_status.set()
        self.control.release_generate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.temp.cleanup()

    def get_json(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        self.assertEqual(200, response.status, payload)
        return payload

    def test_batch_api_exposes_item_progress_and_live_counts(self):
        batch = self.server.store.create(2)
        self.server.store.add_concepts(batch["id"], [make_concept(0), make_concept(1)])
        self.server.engine.start(batch["id"])
        self.assertTrue(self.control.generate_entered.wait(5), "first image generation did not start")

        detail = self.get_json(f"/api/batches/{batch['id']}")
        self.assertEqual(1, detail["batch"]["generating"])
        self.assertEqual(1, detail["batch"]["waiting"])
        self.assertEqual(["generating", "queued"], [item["stage"] for item in detail["items"]])
        for item in detail["items"]:
            for field in ("stage", "progress_message", "started_at", "finished_at", "stage_changed_at"):
                self.assertIn(field, item)


if __name__ == "__main__":
    unittest.main()
