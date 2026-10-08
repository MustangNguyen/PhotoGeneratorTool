import http.client
import io
import json
import re
import tempfile
import threading
import unittest
import zipfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app import build_server
from studio.artifacts import export_batch
from studio.engine import Engine
from studio.store import Store


def make_concept(number):
    return {
        "title": f"Cảnh{number}",
        "category": "Khám phá",
        "subject": f"Thiếtbị{number} mẫuvật{number} côngcụ{number}",
        "scene": f"Địađiểm{number} khônggian{number} địahình{number}",
        "story": f"Câuchuyện{number} nhiệmvụ{number} khámphá{number}",
        "composition": f"Đường chéo tiền cảnh delta{number}, chủ thể giữa và hậu cảnh",
        "palette": f"Lam khoáng và hổ phách epsilon{number}",
        "materials": f"Đá, kính, đồng và mẫu vật zeta{number}",
        "key": f"alpha{number} survey device beta{number} geology station gamma{number}",
        "prompt": f"A bright realistic alpha{number} survey device in beta{number} geology station.",
    }


class GenerationControl:
    """Event-driven fake backend; tests never depend on timing sleeps."""

    def __init__(self, blocked=True):
        self.condition = threading.Condition()
        self.blocked = blocked
        self.started = []
        self.finished = []
        self.failed = []
        self.active = 0
        self.max_active = 0
        self.providers = []
        self.release_events = {}
        self.fail_numbers = set()

    def factory(self):
        provider = ControlledProvider(self, len(self.providers))
        with self.condition:
            self.providers.append(provider)
            self.condition.notify_all()
        return provider

    def wait_for(self, predicate, message, timeout=5):
        with self.condition:
            if not self.condition.wait_for(predicate, timeout):
                raise AssertionError(message)

    def wait_started(self, count):
        self.wait_for(lambda: len(self.started) >= count, f"only {len(self.started)} of {count} calls started")

    def wait_finished(self, count):
        self.wait_for(lambda: len(self.finished) + len(self.failed) >= count, f"only {len(self.finished) + len(self.failed)} of {count} calls finished")

    def release(self, *numbers):
        with self.condition:
            for number in numbers:
                self.release_events.setdefault(number, threading.Event()).set()
            self.condition.notify_all()

    def release_all(self):
        with self.condition:
            self.blocked = False
            for event in self.release_events.values():
                event.set()
            self.condition.notify_all()

    def generate(self, provider, prompt, directory):
        match = re.search(r"alpha(\d+)", prompt)
        if not match:
            raise AssertionError(f"could not identify concept in prompt: {prompt}")
        number = int(match.group(1))
        with self.condition:
            self.started.append(number)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            release = self.release_events.setdefault(number, threading.Event())
            self.condition.notify_all()
        try:
            if self.blocked:
                while not release.wait(5):
                    raise AssertionError(f"test did not release image {number}")
            if provider.cancelled.is_set():
                raise RuntimeError("synthetic cancellation")
            if number in self.fail_numbers:
                with self.condition:
                    self.failed.append(number)
                    self.condition.notify_all()
                raise RuntimeError(f"synthetic failure for image {number}")
            target = Path(directory) / "provider-source.png"
            Image.new("RGB", (300, 450), (20 + number * 17, 80, 150)).save(target)
            with self.condition:
                self.finished.append(number)
                self.condition.notify_all()
            return target
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()


class ControlledProvider:
    def __init__(self, control, serial):
        self.control = control
        self.serial = serial
        self.cancelled = threading.Event()
        self.cancel_calls = 0

    def status(self):
        return {"name": "fake", "ready": True, "text_ready": True, "message": "ready"}

    def plan(self, prompt):
        match = re.search(r"chính xác (\d+)", prompt)
        count = int(match.group(1))
        return {"concepts": [make_concept(number) for number in range(count)]}

    def review(self, prompt):
        payload = json.loads(prompt.split("CONTEXT CẦN DUYỆT\n", 1)[1].split("\n\nChỉ trả JSON", 1)[0])
        return {"reviews": [
            {"index": item["index"], "decision": "keep", "reason": "Cảnh hợp lý."}
            for item in payload
        ]}

    def generate(self, prompt, directory):
        return self.control.generate(self, prompt, directory)

    def cancel(self):
        self.cancel_calls += 1
        self.cancelled.set()
        self.control.release_all()


class ParallelEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.background = []

    def tearDown(self):
        for engine, control in self.background:
            control.release_all()
            engine.shutdown()
        self.temp.cleanup()

    def make_engine(self, count, concurrency):
        self.store.save_settings({"concurrency": concurrency})
        control = GenerationControl()
        engine = Engine(self.store, control.factory)
        self.background.append((engine, control))
        batch = self.store.create(count)
        self.store.add_concepts(batch["id"], [make_concept(number) for number in range(count)])
        return engine, control, batch

    def join(self, engine, timeout=10):
        thread = engine.thread
        self.assertIsNotNone(thread)
        thread.join(timeout)
        self.assertFalse(thread.is_alive(), "engine worker did not finish")

    def test_parallel_generation_never_starts_or_queues_beyond_limit(self):
        engine, control, batch = self.make_engine(7, 3)
        engine.start(batch["id"])
        control.wait_started(3)

        self.assertCountEqual([0, 1, 2], control.started)
        self.assertEqual(3, control.active)
        self.assertEqual(3, engine.in_flight)
        self.assertEqual(4, len(control.providers), "one coordinator plus one provider per admitted image")
        self.assertEqual(3, len({provider.serial for provider in control.providers[1:]}))

        control.release_all()
        self.join(engine)
        self.assertEqual(3, control.max_active)
        self.assertEqual(7, len(control.started))
        self.assertEqual("completed", self.store.batch(batch["id"])["status"])

    def test_pause_drains_admitted_images_and_resume_does_not_duplicate_them(self):
        engine, control, batch = self.make_engine(5, 3)
        engine.start(batch["id"])
        control.wait_started(3)
        engine.pause(batch["id"])
        control.release_all()
        self.join(engine)

        paused = self.store.items(batch["id"])
        self.assertEqual("paused", self.store.batch(batch["id"])["status"])
        self.assertEqual(3, sum(item["status"] == "completed" for item in paused))
        self.assertEqual(2, sum(item["status"] == "planned" for item in paused))

        engine.start(batch["id"])
        self.join(engine)
        self.assertEqual("completed", self.store.batch(batch["id"])["status"])
        self.assertEqual(Counter(range(5)), Counter(control.started))
        self.assertTrue(all(item["attempts"] == 1 for item in self.store.items(batch["id"])))

    def test_failure_stops_admission_but_keeps_successful_in_flight_results(self):
        engine, control, batch = self.make_engine(5, 3)
        control.fail_numbers.add(0)
        engine.start(batch["id"])
        control.wait_started(3)

        control.release(0)
        control.wait_finished(1)
        self.assertTrue(engine.generation_failed.is_set())
        control.release(1, 2)
        self.join(engine)

        items = self.store.items(batch["id"])
        self.assertEqual([0, 1, 2], sorted(control.started))
        self.assertEqual("failed", items[0]["status"])
        self.assertEqual(["completed", "completed"], [item["status"] for item in items[1:3]])
        self.assertEqual(["planned", "planned"], [item["status"] for item in items[3:]])
        self.assertEqual("blocked", self.store.batch(batch["id"])["status"])

    def test_shutdown_cancels_every_active_worker_provider(self):
        engine, control, batch = self.make_engine(3, 3)
        engine.start(batch["id"])
        control.wait_started(3)
        workers = list(control.providers[1:])

        engine.shutdown()

        self.assertEqual(3, len(workers))
        self.assertTrue(all(provider.cancel_calls == 1 for provider in workers))
        self.assertTrue(all(provider.cancelled.is_set() for provider in workers))
        self.assertEqual(0, engine.in_flight)
        self.assertFalse(engine.thread.is_alive())

    def test_artifact_check_and_commit_are_serialized(self):
        engine, control, batch = self.make_engine(2, 2)
        entered = threading.Event()
        release_check = threading.Event()
        check_lock = threading.Lock()
        calls = []
        active_checks = 0
        max_checks = 0

        def controlled_near_image(path, candidates, cache):
            nonlocal active_checks, max_checks
            with check_lock:
                active_checks += 1
                max_checks = max(max_checks, active_checks)
                calls.append(len(candidates))
                first = len(calls) == 1
            if first:
                entered.set()
                self.assertTrue(release_check.wait(5), "test did not release artifact check")
            with check_lock:
                active_checks -= 1
            return None

        with patch("studio.engine.near_image", side_effect=controlled_near_image):
            engine.start(batch["id"])
            control.wait_started(2)
            control.release_all()
            self.assertTrue(entered.wait(5), "first artifact check never started")
            self.assertEqual([0], calls, "second image entered near-image check before the lock was released")
            release_check.set()
            self.join(engine)

        self.assertEqual(1, max_checks)
        self.assertEqual([0, 1], calls, "second comparison must see the first committed image")

    def test_images_start_while_next_context_round_is_still_planning(self):
        self.store.save_settings({"concurrency": 3})
        control = GenerationControl()
        second_round_started = threading.Event()
        release_second_round = threading.Event()
        rounds = []

        def plan(prompt):
            count = int(re.search(r"chính xác (\d+)", prompt).group(1))
            offset = sum(rounds)
            rounds.append(count)
            if len(rounds) == 2:
                second_round_started.set()
                self.assertTrue(release_second_round.wait(5), "test did not release the second planning round")
            return {"concepts": [make_concept(offset + number) for number in range(count)]}

        def factory():
            provider = control.factory()
            provider.plan = plan
            return provider

        engine = Engine(self.store, factory)
        self.background.append((engine, control))
        batch = self.store.create(23)
        engine.start(batch["id"])

        self.assertTrue(second_round_started.wait(5), "planner never began the second round")
        control.wait_started(3)
        self.assertEqual("generating", self.store.batch(batch["id"])["status"])
        self.assertEqual(20, self.store.batch(batch["id"])["planned"], "second round is still being planned")

        release_second_round.set()
        control.release_all()
        self.join(engine)
        self.assertEqual([20, 3], rounds)
        self.assertEqual(23, len(control.started))
        self.assertEqual("completed", self.store.batch(batch["id"])["status"])

    def test_planning_failure_keeps_generating_planned_images_then_blocks(self):
        self.store.save_settings({"concurrency": 2})
        control = GenerationControl(blocked=False)
        rounds = []

        def plan(prompt):
            rounds.append(1)
            if len(rounds) == 1:
                return {"concepts": [make_concept(number) for number in range(20)]}
            raise RuntimeError("planner offline")

        def factory():
            provider = control.factory()
            provider.plan = plan
            return provider

        engine = Engine(self.store, factory)
        self.background.append((engine, control))
        batch = self.store.create(25)
        engine.start(batch["id"])
        self.join(engine)

        result = self.store.batch(batch["id"])
        self.assertEqual(20, result["completed"])
        self.assertEqual("blocked", result["status"])
        self.assertIn("planner offline", result["error"])

    def make_multi_engine(self, count, sources, unready=()):
        self.store.save_settings({"provider": "codex", "image_sources": sources})
        control = GenerationControl()
        built = []

        def source_factory(name):
            provider = control.factory()
            provider.source = name
            if name in unready:
                provider.status = lambda: {"name": name, "ready": False, "text_ready": False, "message": f"{name} offline"}
            built.append(name)
            return provider

        engine = Engine(self.store, control.factory, source_factory=source_factory)
        self.background.append((engine, control))
        batch = self.store.create(count)
        self.store.add_concepts(batch["id"], [make_concept(number) for number in range(count)])
        return engine, control, batch, built

    def test_each_source_fills_its_own_parallel_slots(self):
        engine, control, batch, _ = self.make_multi_engine(7, {"codex": 2, "digen": 1})
        engine.start(batch["id"])
        control.wait_started(3)

        running = Counter(item["source"] for item in self.store.items(batch["id"]) if item["status"] == "generating")
        self.assertEqual({"codex": 2, "digen": 1}, dict(running))
        self.assertEqual(3, engine.concurrency())

        control.release_all()
        self.join(engine)
        self.assertEqual(3, control.max_active)
        items = self.store.items(batch["id"])
        self.assertEqual(["completed"] * 7, [item["status"] for item in items])
        self.assertTrue({item["source"] for item in items} <= {"codex", "digen"})
        self.assertEqual("completed", self.store.batch(batch["id"])["status"])

    def test_failed_source_stops_while_other_sources_finish_the_batch(self):
        engine, control, batch, _ = self.make_multi_engine(5, {"codex": 1, "digen": 1})
        control.fail_numbers.add(1)
        engine.start(batch["id"])
        control.wait_started(2)
        failing = next(item["source"] for item in self.store.items(batch["id"]) if item["position"] == 1)
        control.release_all()
        self.join(engine)

        items = self.store.items(batch["id"])
        self.assertEqual("failed", items[1]["status"])
        self.assertEqual(["completed"] * 4, [item["status"] for i, item in enumerate(items) if i != 1])
        self.assertEqual([1], [item["position"] for item in items if item["source"] == failing], "failed source takes no new images")
        self.assertFalse(engine.generation_failed.is_set())
        self.assertEqual("blocked", self.store.batch(batch["id"])["status"])

    def test_unready_source_is_skipped(self):
        engine, control, batch, built = self.make_multi_engine(3, {"codex": 1, "antigravity": 2}, unready={"antigravity"})
        control.release_all()
        engine.start(batch["id"])
        self.join(engine)

        self.assertEqual({"codex"}, {item["source"] for item in self.store.items(batch["id"])})
        self.assertEqual("completed", self.store.batch(batch["id"])["status"])
        self.assertTrue(any("antigravity offline" in event["message"] for event in self.store.events(batch["id"])))

    def test_completion_order_does_not_change_item_or_export_order(self):
        engine, control, batch = self.make_engine(3, 3)
        engine.start(batch["id"])
        control.wait_started(3)
        for number in (2, 1, 0):
            control.release(number)
            control.wait_finished(3 - number)
        self.join(engine)

        items = self.store.items(batch["id"])
        self.assertEqual([2, 1, 0], control.finished)
        self.assertEqual([0, 1, 2], [item["position"] for item in items])
        payload = export_batch(self.store.batch(batch["id"]), items)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            image_names = [name for name in archive.namelist() if name.endswith(".jpg")]
        self.assertEqual([0, 1, 2], [item["position"] for item in manifest["items"]])
        self.assertEqual([f"{position:04d}" for position in (1, 2, 3)], [name.split("-")[0] for name in image_names])


class ParallelSettingsHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.control = GenerationControl(blocked=False)
        self.server = build_server(self.root, port=0, provider_factory=self.control.factory)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.temp.cleanup()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        encoded = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def test_settings_reject_invalid_concurrency_without_overwriting_value(self):
        for invalid in (True, 0, 1.5, 9):
            with self.subTest(invalid=invalid):
                status, payload = self.request("PUT", "/api/settings", {"concurrency": invalid})
                self.assertEqual(400, status, payload)
                self.assertIn("1 đến 8", payload["error"])
        status, settings = self.request("GET", "/api/settings")
        self.assertEqual(200, status)
        self.assertEqual(4, settings["concurrency"])

    def test_settings_persist_valid_parallel_limits(self):
        for value in (4, 8):
            status, payload = self.request("PUT", "/api/settings", {"concurrency": value})
            self.assertEqual(200, status, payload)
            status, settings = self.request("GET", "/api/settings")
            self.assertEqual(200, status)
            self.assertEqual(value, settings["concurrency"])
            self.assertEqual(value, self.server.engine.concurrency())

    def test_settings_validate_and_persist_image_sources(self):
        for invalid in ({"codex": 9}, {"codex": True}, {"midjourney": 1}, {"codex": 0, "digen": 0}, [1]):
            with self.subTest(invalid=invalid):
                status, payload = self.request("PUT", "/api/settings", {"image_sources": invalid})
                self.assertEqual(400, status, payload)
        status, payload = self.request("PUT", "/api/settings", {"image_sources": {"codex": 3, "digen": 2, "antigravity": 0}})
        self.assertEqual(200, status, payload)
        status, settings = self.request("GET", "/api/settings")
        self.assertEqual({"codex": 3, "digen": 2, "antigravity": 0}, settings["image_sources"])
        self.assertEqual(5, self.server.engine.concurrency())


if __name__ == "__main__":
    unittest.main()
