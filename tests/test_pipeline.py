import io
import http.client
import json
import re
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from PIL import Image

from studio.artifacts import export_batch, game_jpeg
from studio.engine import Engine
from studio.store import Store
from app import build_server


def make_concept(number):
    return {
        "title": f"Cảnh{number}",
        "category": "Khoa học và khám phá" if number % 2 else "Xưởng thủ công",
        "subject": f"Thiếtbị{number} mẫuvật{number} côngcụ{number}",
        "scene": f"Địađiểm{number} khônggian{number} địahình{number}",
        "story": f"Câuchuyện{number} nhiệmvụ{number} khámphá{number}",
        "composition": f"Đường chéo tiền cảnh delta{number}, chủ thể giữa và nhiều lớp hậu cảnh",
        "palette": f"Lam khoáng và hổ phách sắc độ epsilon{number}",
        "materials": f"Đá, kính, đồng và mẫu vật zeta{number}",
        "key": f"alpha{number} survey device beta{number} geology station gamma{number}",
        "prompt": f"A bright realistic alpha{number} survey device in beta{number} geology station, detailed tools across the frame.",
    }


class FakeProvider:
    def __init__(self, fail_calls=None, block_first=False):
        self.plan_calls = []
        self.generate_calls = []
        self.next_number = 1
        self.fail_calls = set(fail_calls or ())
        self.block_first = block_first
        self.generate_started = threading.Event()
        self.release_generate = threading.Event()

    def status(self):
        return {"name": "fake", "ready": True, "text_ready": True, "message": "ready"}

    def plan(self, prompt):
        self.plan_calls.append(prompt)
        match = re.search(r"chính xác (\d+)", prompt)
        if not match:
            raise AssertionError("planning prompt did not contain the requested count")
        count = int(match.group(1))
        concepts = [make_concept(number) for number in range(self.next_number, self.next_number + count)]
        self.next_number += count
        return {"concepts": concepts}

    def review(self, prompt):
        payload = json.loads(prompt.split("CONTEXT CẦN DUYỆT\n", 1)[1].split("\n\nChỉ trả JSON", 1)[0])
        return {"reviews": [
            {"index": item["index"], "decision": "keep", "reason": "Cảnh hợp lý."}
            for item in payload
        ]}

    def generate(self, prompt, directory):
        call = len(self.generate_calls) + 1
        self.generate_calls.append(prompt)
        self.generate_started.set()
        if self.block_first and call == 1:
            if not self.release_generate.wait(5):
                raise RuntimeError("test timed out waiting to release fake provider")
        if call in self.fail_calls:
            self.fail_calls.remove(call)
            raise RuntimeError("synthetic image failure; do not retry automatically")
        target = Path(directory) / "provider-source.png"
        Image.new("RGB", (360, 540), (20 + call, 90, 160)).save(target)
        return target


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.store.save_settings({"concurrency": 1})

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def wait(engine, timeout=10):
        thread = engine.thread
        if thread:
            thread.join(timeout)
            if thread.is_alive():
                raise AssertionError("engine worker did not finish")

    def test_automatic_planning_and_normalized_images(self):
        provider = FakeProvider()
        engine = Engine(self.store, lambda: provider)
        batch = self.store.create(3)
        engine.start(batch["id"])
        self.wait(engine)

        finished = self.store.batch(batch["id"])
        items = self.store.items(batch["id"])
        self.assertEqual("completed", finished["status"])
        self.assertEqual(3, finished["planned"])
        self.assertEqual(3, len(provider.generate_calls))
        self.assertEqual(1, len(provider.plan_calls))
        self.assertTrue(any("giữ nguyên 3, sửa 0, loại 0" in event["message"] for event in self.store.events(batch["id"])))
        for item in items:
            self.assertEqual("completed", item["status"])
            with Image.open(item["image_path"]) as image:
                self.assertEqual((600, 900), image.size)
            self.assertTrue((Path(item["image_path"]).parent / "context.json").is_file())

    def test_recover_marks_only_interrupted_work(self):
        batch = self.store.create(2)
        self.store.add_concepts(batch["id"], [make_concept(1), make_concept(2)])
        first, second = self.store.items(batch["id"])
        self.store.set_item(first["id"], status="completed", image_path="kept.png")
        self.store.set_item(second["id"], status="generating", attempts=1)
        self.store.set_batch(batch["id"], "generating")

        reopened = Store(self.root)
        reopened.recover()
        recovered = reopened.items(batch["id"])
        self.assertEqual("completed", recovered[0]["status"])
        self.assertEqual("kept.png", recovered[0]["image_path"])
        self.assertEqual("failed", recovered[1]["status"])
        self.assertIn("bị ngắt", recovered[1]["error"])
        self.assertEqual("paused", reopened.batch(batch["id"])["status"])

    def test_recover_changes_queued_batch_to_paused(self):
        batch = self.store.create(1)
        self.assertEqual("queued", self.store.batch(batch["id"])["status"])
        self.store.recover()
        recovered = self.store.batch(batch["id"])
        self.assertEqual("paused", recovered["status"])
        self.assertIn("khởi động lại", recovered["error"])

    def test_pause_completed_batch_preserves_completed_status(self):
        provider = FakeProvider()
        engine = Engine(self.store, lambda: provider)
        batch = self.store.create(1)
        self.store.set_batch(batch["id"], "completed")
        result = engine.pause(batch["id"])
        self.assertEqual("completed", result["status"])
        self.assertEqual("completed", self.store.batch(batch["id"])["status"])

    def test_pause_then_resume_keeps_completed_without_duplicate_generation(self):
        provider = FakeProvider(block_first=True)
        engine = Engine(self.store, lambda: provider)
        batch = self.store.create(3)
        engine.start(batch["id"])
        self.assertTrue(provider.generate_started.wait(5), "fake generation never started")
        engine.pause(batch["id"])
        provider.release_generate.set()
        self.wait(engine)

        paused_items = self.store.items(batch["id"])
        self.assertEqual("paused", self.store.batch(batch["id"])["status"])
        self.assertEqual(1, sum(item["status"] == "completed" for item in paused_items))
        completed_id = next(item["id"] for item in paused_items if item["status"] == "completed")

        engine.start(batch["id"])
        self.wait(engine)
        final_items = self.store.items(batch["id"])
        self.assertEqual("completed", self.store.batch(batch["id"])["status"])
        self.assertEqual(3, len(provider.generate_calls))
        self.assertEqual(1, next(item for item in final_items if item["id"] == completed_id)["attempts"])

    def test_failure_blocks_without_retry_and_explicit_retry_resumes(self):
        provider = FakeProvider(fail_calls={1})
        engine = Engine(self.store, lambda: provider)
        batch = self.store.create(3)
        engine.start(batch["id"])
        self.wait(engine)

        blocked_items = self.store.items(batch["id"])
        self.assertEqual("blocked", self.store.batch(batch["id"])["status"])
        self.assertEqual(1, len(provider.generate_calls), "failed image must not be retried automatically")
        failed = next(item for item in blocked_items if item["status"] == "failed")
        self.assertEqual(2, sum(item["status"] == "planned" for item in blocked_items))

        engine.retry(failed["id"])
        self.wait(engine)
        final_items = self.store.items(batch["id"])
        self.assertEqual("completed", self.store.batch(batch["id"])["status"])
        self.assertEqual(4, len(provider.generate_calls))
        self.assertEqual(2, next(item for item in final_items if item["id"] == failed["id"])["attempts"])

    def test_batch_count_bounds(self):
        for invalid in (True, 0, -1, 1001, 1.5, "2"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.create(invalid)
        self.assertEqual(1, self.store.create(1)["count"])
        self.assertEqual(1000, self.store.create(1000)["count"])

    def test_game_jpeg_converts_landscape_and_transparency(self):
        source = io.BytesIO()
        Image.new('RGBA', (1200, 600), (255, 0, 0, 0)).save(source, format='PNG')
        source.seek(0)
        with Image.open(io.BytesIO(game_jpeg(source))) as result:
            self.assertEqual('JPEG', result.format)
            self.assertEqual((600, 900), result.size)
            self.assertEqual('RGB', result.mode)
            self.assertEqual((255, 255, 255), result.getpixel((300, 450)))

    def test_export_approved_only_with_manifest(self):
        batch = self.store.create(2)
        self.store.add_concepts(batch["id"], [make_concept(1), make_concept(2)])
        items = self.store.items(batch["id"])
        for index, item in enumerate(items):
            path = self.root / f"image-{index}.png"
            Image.new("RGB", (600, 900), (index * 50, 80, 120)).save(path)
            self.store.set_item(
                item["id"], status="completed", image_path=str(path),
                review="approved" if index == 0 else "rejected",
            )
        items = self.store.items(batch["id"])
        payload = export_batch(self.store.batch(batch["id"]), items, approved=True)

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            manifest = json.loads(archive.read("manifest.json"))
            with Image.open(io.BytesIO(archive.read(manifest["items"][0]["file"]))) as exported:
                self.assertEqual("JPEG", exported.format)
                self.assertEqual((600, 900), exported.size)
        self.assertEqual(2, len(names), names)
        self.assertEqual(1, len([name for name in names if name.endswith(".jpg")]))
        self.assertTrue(manifest["approved_only"])
        self.assertEqual(1, len(manifest["items"]))
        self.assertEqual("approved", manifest["items"][0]["review"])
        self.assertNotIn("image_path", manifest["items"][0])


class HTTPPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.provider = FakeProvider()
        self.server = build_server(self.root, port=0, provider_factory=lambda: self.provider)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        supplied = dict(headers or {})
        if body is not None and not isinstance(body, (bytes, str)):
            body = json.dumps(body).encode()
        if body is not None and "Content-Type" not in supplied:
            supplied["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=supplied)
        response = connection.getresponse()
        payload = response.read()
        result = (response.status, dict(response.getheaders()), payload)
        connection.close()
        return result

    def decoded(self, method, path, body=None, headers=None):
        status, response_headers, payload = self.request(method, path, body, headers)
        return status, response_headers, json.loads(payload)

    def test_http_create_status_and_review_flow(self):
        status, _, created = self.decoded("POST", "/api/batches", {"count": 1})
        self.assertEqual(201, status, created)
        batch_id = created["id"]
        worker = self.server.engine.thread
        self.assertIsNotNone(worker)
        worker.join(10)
        self.assertFalse(worker.is_alive())

        status, _, overview = self.decoded("GET", "/api/status")
        self.assertEqual(200, status)
        self.assertEqual(1, overview["counts"]["images"])
        self.assertNotIn("api_key", overview)

        status, _, detail = self.decoded("GET", f"/api/batches/{batch_id}")
        self.assertEqual(200, status)
        self.assertEqual("completed", detail["batch"]["status"])
        item_id = detail["items"][0]["id"]
        status, _, reviewed = self.decoded(
            "POST", f"/api/items/{item_id}/review", {"review": "approved"}
        )
        self.assertEqual(200, status, reviewed)
        self.assertEqual("approved", reviewed["review"])

    def test_http_rejects_bad_host_origin_and_json(self):
        status, _, payload = self.decoded("GET", "/api/status", headers={"Host": "evil.example"})
        self.assertEqual(403, status, payload)

        good_host = f"127.0.0.1:{self.port}"
        status, _, payload = self.decoded(
            "POST", "/api/batches", {"count": 1},
            headers={"Host": good_host, "Origin": "http://evil.example"},
        )
        self.assertEqual(403, status, payload)

        status, _, payload = self.decoded(
            "POST", "/api/batches", b"not-json",
            headers={"Host": good_host, "Content-Type": "application/json"},
        )
        self.assertEqual(400, status, payload)
        status, _, payload = self.decoded(
            "POST", "/api/batches", [1, 2], headers={"Host": good_host}
        )
        self.assertEqual(400, status, payload)
        status, _, payload = self.decoded(
            "POST", "/api/batches", {"count": 1},
            headers={"Host": good_host, "Content-Type": "text/plain"},
        )
        self.assertEqual(400, status, payload)

    def test_http_blocks_path_traversal_and_outside_image(self):
        status, _, _ = self.decoded("GET", "/../app.py")
        self.assertEqual(404, status)

        batch = self.server.store.create(1)
        self.server.store.add_concepts(batch["id"], [make_concept(99)])
        item = self.server.store.items(batch["id"])[0]
        outside = self.root.parent / f"outside-{item['id']}.png"
        Image.new("RGB", (600, 900), (1, 2, 3)).save(outside)
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        self.server.store.set_item(item["id"], status="completed", image_path=str(outside))
        status, _, payload = self.decoded("GET", f"/images/{item['id']}.png")
        self.assertEqual(404, status, payload)

    def test_http_never_returns_saved_secret(self):
        secret = "sk-test-super-secret-value"
        settings = {
            "provider": "openai",
            "text_model": "gpt-test",
            "image_model": "gpt-image-test",
            "api_key": secret,
        }
        status, _, payload = self.request("PUT", "/api/settings", settings)
        self.assertEqual(200, status, payload)
        self.assertNotIn(secret.encode(), payload)
        status, _, payload = self.request("GET", "/api/settings")
        self.assertEqual(200, status)
        self.assertNotIn(secret.encode(), payload)
        decoded = json.loads(payload)
        self.assertTrue(decoded["has_api_key"])
        self.assertNotIn("api_key", decoded)

    def test_http_rejects_openai_settings_without_image_model(self):
        status, _, payload = self.decoded(
            "PUT", "/api/settings",
            {"provider": "openai", "text_model": "gpt-test", "image_model": ""},
        )
        self.assertEqual(400, status, payload)
        self.assertIn("model ảnh", payload["error"])

    def test_http_saves_antigravity_settings_separately(self):
        status, _, payload = self.decoded(
            "PUT", "/api/settings",
            {
                "provider": "antigravity",
                "text_model": "gpt-codex",
                "antigravity_text_model": "gemini-antigravity",
                "image_model": "",
            },
        )
        self.assertEqual(200, status, payload)

        with mock.patch("app.shutil.which", side_effect=lambda name: "/usr/bin/agy" if name == "agy" else None):
            status, _, settings = self.decoded("GET", "/api/settings")
        self.assertEqual(200, status, settings)
        self.assertEqual("antigravity", settings["provider"])
        self.assertEqual("gpt-codex", settings["text_model"])
        self.assertEqual("gemini-antigravity", settings["antigravity_text_model"])
        self.assertTrue(settings["antigravity_available"])
        self.assertFalse(settings["codex_available"])

    def test_data_directory_allows_only_one_server(self):
        with self.assertRaisesRegex(RuntimeError, "đang dùng thư mục dữ liệu"):
            build_server(self.root, port=0, provider_factory=lambda: self.provider)


if __name__ == "__main__":
    unittest.main()
