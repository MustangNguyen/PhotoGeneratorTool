import json
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

from . import final
from .artifacts import load_fingerprint_cache, near_image, normalize_image, save_fingerprint_cache
from .providers import DEFAULTS
from .store import now
from .diversity import make_planning_prompt, order_concepts, pick_seeds, validate_concepts, image_prompt
from .review import apply_review, make_review_prompt
from .style import style_drift, style_stats


class Engine:
    def __init__(self, store, provider_factory, final_root=None):
        self.store = store
        self.provider_factory = provider_factory
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

    def stopped(self, batch_id):
        if self.pause_requested.is_set():
            self.store.set_batch(batch_id, 'paused')
            self.store.event(batch_id, 'Đã tạm dừng. Kết quả và context được giữ nguyên.')
            return True
        return False

    def run(self, batch_id):
        try:
            provider = self.provider_factory()
            self.provider = provider
            status = provider.status()
            if not status.get('text_ready', status['ready']):
                raise RuntimeError(status['message'])
            stalled = 0
            feedback = []
            while True:
                if self.stopped(batch_id):
                    return
                batch = self.store.batch(batch_id)
                remaining = batch['count'] - batch['planned']
                if remaining <= 0:
                    break
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
                if self.stopped(batch_id):
                    return
                reviewed = []
                review_rejected = []
                if accepted:
                    self.store.event(batch_id, f"Đang duyệt tính tự nhiên và độ rõ ràng của {len(accepted)} context trong một lượt.")
                    review_raw = provider.review(make_review_prompt(accepted))
                    if self.stopped(batch_id):
                        return
                    reviewed, review_rejected = apply_review(review_raw, accepted, history)
                    feedback.extend(review_rejected)
                    kept_count = sum(item.get('context_review', {}).get('decision') == 'keep' for item in reviewed)
                    revised_count = sum(item.get('context_review', {}).get('decision') == 'revise' for item in reviewed)
                    self.store.event(batch_id, f"Kết quả duyệt context: giữ nguyên {kept_count}, sửa {revised_count}, loại {len(review_rejected)}.")
                    reviewed = order_concepts(reviewed, history[-1] if history else None)
                if reviewed:
                    self.store.add_concepts(batch_id, reviewed)
                    stalled = 0
                else:
                    stalled += 1
                self.store.event(batch_id, f"Đã duyệt và giữ {len(reviewed)} context; loại {len(feedback)} đề xuất chưa đạt.")
                if feedback:
                    self.store.event(batch_id, 'Kiểm tra context: ' + '; '.join(feedback[:3])[:700])
                if stalled >= 3:
                    raise RuntimeError('Ba lượt lập context không có đề xuất mới hợp lệ. Batch được giữ lại; đổi model hoặc tiếp tục sau.')
            if self.stopped(batch_id):
                return
            if not status['ready']:
                raise RuntimeError(status['message'])
            self.store.set_batch(batch_id, 'generating')
            self.generate_parallel(batch_id, batch['count'])
            if self.stopped(batch_id):
                return
            batch = self.store.batch(batch_id)
            if batch['failed']:
                self.store.set_batch(batch_id, 'blocked', 'Các ảnh còn lại đã xong. Có ảnh lỗi cần kiểm tra và thử lại riêng.')
            else:
                self.store.set_batch(batch_id, 'completed')
                self.store.event(batch_id, 'Đã tạo đủ ảnh. Hãy duyệt và xuất các ảnh phù hợp.')
        except Exception as error:
            self.store.set_batch(batch_id, 'blocked', str(error)[:1000])
            self.store.event(batch_id, str(error)[:1000])
        finally:
            with self.lock:
                self.active = None
                self.provider = None

    def concurrency(self):
        value = self.store.settings().get('concurrency', DEFAULTS['concurrency'])
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 8:
            raise ValueError('Số ảnh đồng thời phải là số nguyên từ 1 đến 8.')
        return value

    def generate_parallel(self, batch_id, count):
        limit = self.concurrency()
        items = iter(item for item in self.store.items(batch_id) if item['status'] == 'planned')
        pending = set()
        exhausted = False
        first_error = None
        self.store.event(batch_id, f'Tạo tối đa {limit} ảnh đồng thời; không tự thử lại ảnh lỗi.')
        with ThreadPoolExecutor(max_workers=limit, thread_name_prefix='puzzle-image') as pool:
            while True:
                # Never queue the whole batch: only admit enough work to fill free slots.
                with self.lock:
                    while not exhausted and len(pending) < limit and not self.pause_requested.is_set() and not self.generation_failed.is_set():
                        item = next(items, None)
                        if item is None:
                            exhausted = True
                            break
                        pending.add(pool.submit(self.generate_one, batch_id, count, item))
                if not pending:
                    break
                finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in finished:
                    try:
                        future.result()
                    except Exception as error:
                        self.generation_failed.set()
                        if first_error is None:
                            first_error = error
                # On pause/failure, drain existing work and save its results before returning.
        if first_error is not None:
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

    def generate_one(self, batch_id, count, item):
        provider = None
        started = False
        try:
            with self.lock:
                if self.pause_requested.is_set() or self.generation_failed.is_set():
                    return
                self.store.set_item(item['id'], status='generating', attempts=item['attempts'] + 1, error='', stage='preparing', progress_message='Đang chuẩn bị yêu cầu và thư mục ảnh.', started_at=now(), finished_at='', stage_changed_at=now())
                started = True
                self.in_flight += 1
                provider = self.provider_factory()
                self.worker_providers.add(provider)
            self.store.event(batch_id, f"Đang tạo ảnh {item['position']+1}/{count}: {item['title']}.")
            directory = self.store.root / 'images' / item['id']
            directory.mkdir(parents=True, exist_ok=True)
            self.store.set_item(item['id'], stage='generating', progress_message='Đang tạo, chờ dịch vụ trả ảnh. Chưa có phần trăm tiến độ từ dịch vụ.', stage_changed_at=now())
            effective_prompt = image_prompt(item)
            self.store.set_prompt(item['id'], effective_prompt)
            item['prompt'] = effective_prompt
            source = provider.generate(effective_prompt, directory)
            target = directory / '600x900.jpg'
            self.store.set_item(item['id'], stage='saving', progress_message='Đã nhận ảnh; đang lưu bản 600×900.', stage_changed_at=now())
            normalize_image(source, target)
            stats = style_stats(target)
            drift = style_drift(stats)
            style_warning = drift['message'] if drift else ''
            self.store.set_item(item['id'], stage='checking', progress_message='Đang kiểm tra gần trùng và lưu thông tin ảnh.', stage_changed_at=now())
            # Compare and commit atomically so simultaneously finished images also see each other.
            with self.artifact_lock:
                similar = near_image(target, self.store.completed_items() + self.final_reference(), self.image_cache)
                self.save_final_fingerprints()
                warning = f"Ảnh có bố cục/màu gần ảnh «{similar['title']}». Cần người duyệt đối chiếu." if similar else ''
                (directory / 'context.json').write_text(json.dumps({k: item.get(k) for k in ['title','category','subject','scene','story','composition','palette','materials','key','main_subject','final_seed','axes','prompt','context_review'] if k in item} | {'style_stats': stats}, ensure_ascii=False, indent=2), encoding='utf-8')
                self.store.set_item(item['id'], status='completed', image_path=str(target.resolve()), similarity=warning, style_warning=style_warning, stage='completed', progress_message='Đã lưu ảnh 600×900, chờ duyệt.', stage_changed_at=now(), finished_at=now())
            self.store.event(batch_id, f"Đã lưu 600×900: {item['title']}." + (' Có cảnh báo gần trùng.' if warning else '') + (' Màu lệch so với ảnh mẫu Final.' if style_warning else ''))
        except Exception as error:
            self.generation_failed.set()
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
