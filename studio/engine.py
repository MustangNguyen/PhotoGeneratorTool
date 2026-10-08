import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

from . import final
from .artifacts import load_fingerprint_cache, near_image, normalize_image, save_fingerprint_cache
from .providers import DEFAULTS, PROVIDER_NAMES, DigenProvider, ItemError, image_sources
from .store import now
from .diversity import learn_from, make_planning_prompt, order_concepts, pick_seeds, validate_concepts, image_prompt
from .review import apply_review, make_review_prompt
from .style import style_drift, style_stats

ITEM_FAILURES_BEFORE_STOP = 3  # consecutive single-image errors that mean the source itself is failing


class Engine:
    def __init__(self, store, provider_factory, final_root=None, source_factory=None):
        self.store = store
        self.provider_factory = provider_factory
        # source_factory(name) builds one image source; without it every slot uses provider_factory.
        self.source_factory = source_factory
        self.final_root = final_root
        self.final_candidates = None
        self.final_cached = 0
        self.lock = threading.RLock()
        self.active = None
        self.thread = None
        self.provider = None
        self.image_cache = {}
        self.artifact_lock = threading.Lock()
        self.worker_providers = set()
        self.in_flight = 0
        self.generation_failed = threading.Event()
        self.pause_requested = threading.Event()
        self.sources = {}
        self.failed_sources = set()
        self.item_failures = Counter()  # consecutive single-image errors per source
        # Recovery instead of giving up: a failed image is generated once more, and a failing
        # source rests for a while and then takes images again, up to a few rests per batch.
        self.item_retries = 1
        self.source_cooldown_seconds = 180
        self.max_cooldowns = 3
        self.cooldown_until = {}
        self.cooldowns = Counter()
        # Planner thread state; planning overlaps image generation.
        self.planning_done = threading.Event()
        self.planning_done.set()
        self.planning_stop = threading.Event()
        self.work_ready = threading.Event()
        self.planning_error = None

    def start(self, batch_id):
        with self.lock:
            batch = self.store.batch(batch_id)
            if self.active:
                if self.active == batch_id:
                    return batch
                raise ValueError('Một batch đang chạy. Tạm dừng batch đó trước khi chạy batch khác.')
            if batch['status'] == 'completed':
                return batch
            self.active = batch_id
            self.pause_requested.clear()
            self.generation_failed.clear()
            self.store.set_batch(batch_id, 'planning' if batch['planned'] < batch['count'] else 'generating')
            self.thread = threading.Thread(target=self.run, args=(batch_id,), daemon=True)
            self.thread.start()
            return self.store.batch(batch_id)

    def pause(self, batch_id):
        with self.lock:
            batch = self.store.batch(batch_id)
            if batch['status'] == 'completed':
                return batch
            if self.active == batch_id:
                self.pause_requested.set()
                self.store.set_batch(batch_id, 'pausing')
                self.store.event(batch_id, 'Ngừng nhận ảnh mới; sẽ tạm dừng khi các ảnh đang tạo hoàn tất.')
            else:
                self.store.set_batch(batch_id, 'paused')
            return self.store.batch(batch_id)

    def retry(self, item_id):
        with self.lock:
            if self.active:
                raise ValueError('Tạm dừng batch đang chạy trước khi thử lại ảnh lỗi.')
            item = self.store.item(item_id)
            if item['status'] != 'failed':
                raise ValueError('Chỉ thử lại ảnh đang ở trạng thái lỗi.')
            self.store.set_item(item_id, status='planned', error='', stage='queued', progress_message='Đang chờ thử lại.', started_at='', finished_at='', stage_changed_at=now())
            self.store.set_batch(item['batch_id'], 'paused')
            self.store.event(item['batch_id'], f"Đã xếp lại ảnh lỗi: {item['title']}.")
            return self.start(item['batch_id'])

    def retry_failed(self, batch_id):
        with self.lock:
            if self.active:
                raise ValueError('Tạm dừng batch đang chạy trước khi thử lại ảnh lỗi.')
            failed = [item for item in self.store.items(batch_id) if item['status'] == 'failed']
            if not failed:
                raise ValueError('Lô này không có ảnh lỗi để thử lại.')
            for item in failed:
                self.store.set_item(item['id'], status='planned', error='', stage='queued', progress_message='Đang chờ thử lại.', started_at='', finished_at='', stage_changed_at=now())
            self.store.set_batch(batch_id, 'paused')
            self.store.event(batch_id, f'Đã xếp lại {len(failed)} ảnh lỗi.')
            return self.start(batch_id)

    def stopped(self, batch_id):
        if self.pause_requested.is_set():
            self.store.set_batch(batch_id, 'paused')
            self.store.event(batch_id, 'Đã tạm dừng. Kết quả và context được giữ nguyên.')
            return True
        return False

    def run(self, batch_id):
        planner = None
        try:
            provider = self.provider_factory()
            self.provider = provider
            status = provider.status()
            if not status.get('text_ready', status['ready']):
                raise RuntimeError(status['message'])
            batch = self.store.batch(batch_id)
            sources = self.ready_sources(batch_id, provider, status)
            # Planning and image generation overlap: each reviewed round of contexts is queued for
            # image generation immediately while the planner thread drafts the next round.
            self.planning_error = None
            self.planning_stop.clear()
            self.planning_done.clear()
            if batch['planned'] < batch['count']:
                planner = threading.Thread(target=self.plan_all, args=(batch_id, provider), daemon=True, name='puzzle-planner')
                planner.start()
            else:
                self.planning_done.set()
            self.generate_parallel(batch_id, batch['count'], sources)
            planner = self.finish_planner(planner, provider, cancel=False)
            if self.stopped(batch_id):
                return
            if self.planning_error is not None:
                raise self.planning_error
            batch = self.store.batch(batch_id)
            if batch['failed']:
                self.store.set_batch(batch_id, 'blocked', 'Các ảnh còn lại đã xong. Có ảnh lỗi cần kiểm tra và thử lại riêng.')
            else:
                self.store.set_batch(batch_id, 'completed')
                self.store.event(batch_id, 'Đã tạo đủ ảnh. Hãy duyệt và xuất các ảnh phù hợp.')
        except Exception as error:
            self.finish_planner(planner, self.provider, cancel=True)
            self.store.set_batch(batch_id, 'blocked', str(error)[:1000])
            self.store.event(batch_id, str(error)[:1000])
        finally:
            with self.lock:
                self.active = None
                self.provider = None

    def finish_planner(self, planner, provider, cancel):
        """Wait for the planner thread; on failure cancel its plan call instead of waiting for it."""
        if planner is None or not planner.is_alive():
            return None
        self.planning_stop.set()
        if cancel and provider is not None and hasattr(provider, 'cancel'):
            try:
                provider.cancel()
            except Exception:
                pass
        planner.join()
        return None

    def planning_halted(self):
        return self.pause_requested.is_set() or self.generation_failed.is_set() or self.planning_stop.is_set()

    def plan_all(self, batch_id, provider):
        try:
            self.plan_rounds(batch_id, provider)
        except Exception as error:
            # A cancelled plan call after a stop is expected; only a real planning failure blocks the batch.
            if not self.planning_halted():
                self.planning_error = error
                self.store.event(batch_id, ('Lập context dừng: ' + str(error))[:900] + ' Các ảnh đã có context vẫn tiếp tục tạo.')
        finally:
            self.planning_done.set()
            self.work_ready.set()

    def plan_rounds(self, batch_id, provider):
        stalled = 0
        feedback = []
        while True:
            if self.planning_halted():
                return
            batch = self.store.batch(batch_id)
            remaining = batch['count'] - batch['planned']
            if remaining <= 0:
                return
            history = self.store.history()
            requested = min(20, remaining)
            category_counts = dict(Counter(c.get('category', '') for c in history))
            seeds = pick_seeds(requested, history, category_counts)
            prompt = make_planning_prompt(requested, history, category_counts, seeds)
            if feedback:
                prompt += '\nLượt trước đã bị loại vì: ' + '; '.join(feedback[:5]) + '. Hãy sửa các vấn đề này.'
            self.store.event(batch_id, f"Đang lập {requested} context, đối chiếu {len(history)} context đã lưu.")
            raw = provider.plan(prompt)
            accepted, rejected = validate_concepts(raw, history, requested, seeds)
            feedback = list(rejected)
            if self.planning_halted():
                return
            reviewed = []
            review_rejected = []
            if accepted:
                self.store.event(batch_id, f"Đang duyệt tính tự nhiên và độ rõ ràng của {len(accepted)} context trong một lượt.")
                review_raw = provider.review(make_review_prompt(accepted))
                if self.planning_halted():
                    return
                reviewed, review_rejected = apply_review(review_raw, accepted, history)
                feedback.extend(review_rejected)
                kept_count = sum(item.get('context_review', {}).get('decision') == 'keep' for item in reviewed)
                revised_count = sum(item.get('context_review', {}).get('decision') == 'revise' for item in reviewed)
                self.store.event(batch_id, f"Kết quả duyệt context: giữ nguyên {kept_count}, sửa {revised_count}, loại {len(review_rejected)}.")
                reviewed = order_concepts(reviewed, history[-1] if history else None)
            if reviewed:
                self.store.add_concepts(batch_id, reviewed)
                self.work_ready.set()
                stalled = 0
                try:
                    learned = learn_from(reviewed)
                except (OSError, ValueError) as error:
                    self.store.event(batch_id, f'Không ghi được họ chủ thể/vật phụ mới: {error}'[:500])
                else:
                    if any(learned.values()):
                        self.store.event(batch_id, f"Đã học thêm {learned['members']} chủ thể vào họ ({learned['families']} họ mới) và {learned['props']} vật phụ vào subject-families.json.")
            else:
                stalled += 1
            self.store.event(batch_id, f"Đã duyệt và giữ {len(reviewed)} context; loại {len(feedback)} đề xuất chưa đạt.")
            if feedback:
                self.store.event(batch_id, 'Kiểm tra context: ' + '; '.join(feedback[:3])[:700])
            if stalled >= 3:
                raise RuntimeError('Ba lượt lập context không có đề xuất mới hợp lệ. Batch được giữ lại; đổi model hoặc tiếp tục sau.')

    def image_sources(self):
        return image_sources({**DEFAULTS, **self.store.settings()})

    def concurrency(self):
        return sum(self.image_sources().values())

    def source_provider(self, name):
        return self.source_factory(name) if self.source_factory else self.provider_factory()

    def ready_sources(self, batch_id, provider, status):
        """Configured image sources whose service is ready; unready ones are skipped, not fatal."""
        sources = self.image_sources()
        main = {**DEFAULTS, **self.store.settings()}['provider']
        ready, skipped = {}, []
        for name, slots in sources.items():
            source_status = status if name == main or not self.source_factory else self.source_provider(name).status()
            if source_status.get('image_ready', source_status['ready']):
                ready[name] = slots
            else:
                skipped.append(f"{PROVIDER_NAMES.get(name, name)}: {source_status['message']}")
        if skipped:
            self.store.event(batch_id, ('Bỏ qua nguồn chưa sẵn sàng — ' + ' | '.join(skipped))[:1000])
        if not ready:
            raise RuntimeError(skipped[0] if len(skipped) == 1 else 'Không nguồn tạo ảnh nào sẵn sàng. ' + ' | '.join(skipped)[:900])
        return ready

    def generate_parallel(self, batch_id, count, sources=None):
        sources = sources or self.image_sources()
        admitted = set()
        reserved = {}
        running = dict.fromkeys(sources, 0)
        pending = {}
        first_error = None
        with self.lock:
            self.sources = dict(sources)
            self.failed_sources = set()
            self.item_failures = Counter()
            self.cooldown_until = {}
            self.cooldowns = Counter()
        retried = Counter()
        items_of = {}
        retry_note = f'ảnh lỗi tự thử lại {self.item_retries} lần' if self.item_retries else 'không tự thử lại ảnh lỗi'
        plan = ', '.join(f'{PROVIDER_NAMES.get(name, name)} {slots}' for name, slots in sources.items())
        self.store.event(batch_id, f'Tạo tối đa {sum(sources.values())} ảnh đồng thời ({plan}); {retry_note}.' if len(sources) > 1 else f'Tạo tối đa {sum(sources.values())} ảnh đồng thời; {retry_note}.')
        with ThreadPoolExecutor(max_workers=sum(sources.values()), thread_name_prefix='puzzle-image') as pool:
            while True:
                # Read the done flag before the store so contexts saved just before it are never missed.
                self.work_ready.clear()
                planning_done = self.planning_done.is_set()
                queue = [item for item in self.store.items(batch_id) if item['status'] == 'planned' and item['id'] not in admitted]
                for item in queue:
                    if item['id'] not in reserved:
                        # An image Digen already finished is fetched again for free only through Digen.
                        reserved[item['id']] = (item.get('source') == 'digen' and 'digen' in sources
                                                and bool(DigenProvider.finished_task(self.store.root / 'images' / item['id'])))
                # Never queue the whole batch: only admit enough work to fill each source's free slots.
                with self.lock:
                    clock = time.monotonic()
                    resting = [until for name, until in self.cooldown_until.items() if until > clock and name not in self.failed_sources]
                    for name, slots in sources.items():
                        while (running[name] < slots and name not in self.failed_sources and self.cooldown_until.get(name, 0) <= clock
                               and not self.pause_requested.is_set() and not self.generation_failed.is_set()):
                            item = next((item for item in queue if not reserved[item['id']] or name == 'digen'), None)
                            if item is None:
                                break
                            if not admitted and self.store.batch(batch_id)['status'] == 'planning':
                                self.store.set_batch(batch_id, 'generating')
                            queue.remove(item)
                            admitted.add(item['id'])
                            running[name] += 1
                            future = pool.submit(self.generate_one, batch_id, count, item, name)
                            pending[future] = name
                            items_of[future] = item
                    stopping = self.pause_requested.is_set() or self.generation_failed.is_set()
                # A resting source wakes the loop when its rest ends, so waiting images are not dropped.
                rest = max(0.1, min(resting) - clock) if resting and (queue or not planning_done) and not stopping else None
                if not pending:
                    if (planning_done or stopping) and rest is None:
                        break
                    self.work_ready.wait(rest)
                    continue
                # While contexts are still being planned, wake periodically to admit newly saved ones.
                timeout = None if planning_done else 0.5
                if rest is not None:
                    timeout = rest if timeout is None else min(timeout, rest)
                finished, _ = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
                for future in finished:
                    name = pending.pop(future)
                    item = items_of.pop(future)
                    running[name] -= 1
                    try:
                        future.result()
                    except Exception as error:
                        if first_error is None:
                            first_error = error
                        # Put the image back in the queue for one more try; any source may take it.
                        if retried[item['id']] < self.item_retries and not self.pause_requested.is_set() and not self.generation_failed.is_set():
                            retried[item['id']] += 1
                            self.store.set_item(item['id'], status='planned', stage='queued', progress_message='Lần trước lỗi; đang chờ tự thử lại.', stage_changed_at=now())
                            self.store.event(batch_id, f"Tự thử lại ảnh {item['position'] + 1}/{count}: {item['title']}.")
                            admitted.discard(item['id'])
                            reserved.pop(item['id'], None)
                # On pause/failure, drain existing work and save its results before returning.
        # A failed source stops taking images while others keep going; only stop the batch when all failed.
        if first_error is not None and self.generation_failed.is_set():
            raise first_error

    def final_reference(self):
        """Final sample images on this machine; fingerprints are cached in the data folder."""
        if self.final_candidates is None:
            self.final_candidates = final.image_candidates(self.final_root) if self.final_root else []
            if self.final_candidates:
                self.image_cache.update(load_fingerprint_cache(self.store.root / 'final-fingerprints.json'))
                self.final_cached = sum(c['image_path'] in self.image_cache for c in self.final_candidates)
        return self.final_candidates

    def save_final_fingerprints(self):
        keys = [c['image_path'] for c in self.final_candidates or ()]
        cached = sum(key in self.image_cache for key in keys)
        if cached > self.final_cached:
            save_fingerprint_cache(self.store.root / 'final-fingerprints.json', self.image_cache, keys)
            self.final_cached = cached

    def generate_one(self, batch_id, count, item, source=None):
        provider = None
        started = False
        label = PROVIDER_NAMES.get(source, source) if source and len(self.sources) > 1 else ''
        try:
            with self.lock:
                if self.pause_requested.is_set() or self.generation_failed.is_set() or source in self.failed_sources:
                    return
                self.store.set_item(item['id'], status='generating', attempts=item['attempts'] + 1, error='', stage='preparing', progress_message='Đang chuẩn bị yêu cầu và thư mục ảnh.', started_at=now(), finished_at='', stage_changed_at=now(), source=source or '')
                started = True
                self.in_flight += 1
                provider = self.source_provider(source) if source else self.provider_factory()
                self.worker_providers.add(provider)
            self.store.event(batch_id, f"Đang tạo ảnh {item['position']+1}/{count}" + (f' qua {label}' if label else '') + f": {item['title']}.")
            directory = self.store.root / 'images' / item['id']
            directory.mkdir(parents=True, exist_ok=True)
            self.store.set_item(item['id'], stage='generating', progress_message=(f'Đang tạo qua {label}' if label else 'Đang tạo') + ', chờ dịch vụ trả ảnh. Chưa có phần trăm tiến độ từ dịch vụ.', stage_changed_at=now())
            effective_prompt = image_prompt(item)
            self.store.set_prompt(item['id'], effective_prompt)
            item['prompt'] = effective_prompt
            raw_image = provider.generate(effective_prompt, directory)
            target = directory / '600x900.jpg'
            self.store.set_item(item['id'], stage='saving', progress_message='Đã nhận ảnh; đang lưu bản 600×900.', stage_changed_at=now())
            normalize_image(raw_image, target)
            stats = style_stats(target)
            drift = style_drift(stats)
            style_warning = drift['message'] if drift else ''
            self.store.set_item(item['id'], stage='checking', progress_message='Đang kiểm tra gần trùng và lưu thông tin ảnh.', stage_changed_at=now())
            # Compare and commit atomically so simultaneously finished images also see each other.
            with self.artifact_lock:
                similar = near_image(target, self.store.completed_items() + self.final_reference(), self.image_cache)
                self.save_final_fingerprints()
                warning = f"Ảnh có bố cục/màu gần ảnh «{similar['title']}». Cần người duyệt đối chiếu." if similar else ''
                (directory / 'context.json').write_text(json.dumps({k: item.get(k) for k in ['title','category','subject','scene','story','composition','palette','materials','key','main_subject','subject_family','props','final_seed','axes','prompt','context_review'] if k in item} | {'source': source or '', 'style_stats': stats}, ensure_ascii=False, indent=2), encoding='utf-8')
                self.store.set_item(item['id'], status='completed', image_path=str(target.resolve()), similarity=warning, style_warning=style_warning, stage='completed', progress_message='Đã lưu ảnh 600×900, chờ duyệt.', stage_changed_at=now(), finished_at=now())
            with self.lock:
                self.item_failures[source] = 0
            self.store.event(batch_id, f"Đã lưu 600×900: {item['title']}" + (f' (từ {label})' if label else '') + "." + (' Có cảnh báo gần trùng.' if warning else '') + (' Màu lệch so với ảnh mẫu Final.' if style_warning else ''))
        except Exception as error:
            # A bad result for one image (ItemError) does not take its source out of the batch.
            item_only = isinstance(error, ItemError)
            repeated = False
            if item_only and started:
                with self.lock:
                    self.item_failures[source] += 1
                    # Several images failing in a row means the source itself is broken (e.g. out of credit).
                    repeated = self.item_failures[source] >= ITEM_FAILURES_BEFORE_STOP
                item_only = not repeated
            notice = ''
            if not item_only:
                with self.lock:
                    clock = time.monotonic()
                    # Images already running on a source fail together; that is one rest, not several.
                    if source not in self.failed_sources and self.cooldown_until.get(source, 0) <= clock:
                        self.cooldowns[source] += 1
                        self.item_failures[source] = 0
                        if self.cooldowns[source] > self.max_cooldowns:
                            self.failed_sources.add(source)
                            notice = 'ngừng nhận ảnh mới đến hết lượt chạy này'
                        else:
                            self.cooldown_until[source] = clock + self.source_cooldown_seconds
                            notice = f'nghỉ {max(1, round(self.source_cooldown_seconds / 60))} phút rồi tự nhận ảnh tiếp (lần nghỉ {self.cooldowns[source]}/{self.max_cooldowns})'
                    if not self.sources or self.failed_sources >= set(self.sources):
                        self.generation_failed.set()
            if started and notice:
                reason = f'lỗi {ITEM_FAILURES_BEFORE_STOP} ảnh liên tiếp' if repeated else 'lỗi'
                others = ', các nguồn khác vẫn chạy' if len(self.sources) > 1 else ''
                self.store.event(batch_id, f'{label or PROVIDER_NAMES.get(source, source) or "Nguồn tạo ảnh"} {reason}; {notice}{others}.')
            if started:
                self.store.set_item(item['id'], status='failed', error=str(error)[:1000], stage='failed', progress_message='Lỗi: ' + str(error)[:300], stage_changed_at=now(), finished_at=now())
            raise
        finally:
            if started:
                with self.lock:
                    if provider is not None:
                        self.worker_providers.discard(provider)
                    self.in_flight -= 1

    def shutdown(self):
        with self.lock:
            self.pause_requested.set()
            providers = set(self.worker_providers)
            if self.provider is not None:
                providers.add(self.provider)
        # Cancel independently: a slow process must not delay cancellation of other slots.
        if providers:
            with ThreadPoolExecutor(max_workers=len(providers)) as pool:
                futures = [pool.submit(provider.cancel) for provider in providers if hasattr(provider, 'cancel')]
                for future in futures:
                    try:
                        future.result()
                    except Exception:
                        # Still drain the engine before releasing the database/process lock.
                        pass
        if self.thread and self.thread.is_alive():
            self.thread.join()
