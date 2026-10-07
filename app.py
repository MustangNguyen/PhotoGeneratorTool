#!/usr/bin/env python3
"""Run with python3 app.py; no network-exposed listener or frontend dependencies."""
import argparse
import fcntl
import signal
import json
import mimetypes
import re
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from studio.artifacts import export_batch, game_jpeg
from studio.engine import Engine
from studio.providers import DEFAULTS, create_provider, read_secret, save_secret
from studio.store import Store

ROOT = Path(__file__).resolve().parent
ID = r'[a-f0-9]{32}'


def build_server(data_root, port=8787, provider_factory=None, final_root=None):
    Path(data_root).mkdir(parents=True, exist_ok=True)
    process_lock = (Path(data_root) / '.server.lock').open('a')
    try:
        fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        process_lock.close()
        raise RuntimeError('Đã có ứng dụng đang dùng thư mục dữ liệu này.')
    store = Store(data_root)
    store.recover()
    factory = provider_factory or (lambda: create_provider(store))
    engine = Engine(store, factory, final_root=final_root)
    status_cache = {'at': 0, 'value': None}
    status_lock = threading.Lock()

    def provider_status():
        with status_lock:
            if time.monotonic() - status_cache['at'] > 30 or status_cache['value'] is None:
                status_cache.update(at=time.monotonic(), value=factory().status())
            return status_cache['value']

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Do not log request bodies, credentials or provider content.
            pass

        def send(self, payload, content_type='application/json; charset=utf-8', status=200, filename=None):
            if isinstance(payload, (dict, list)):
                payload = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            elif isinstance(payload, str):
                payload = payload.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def check_request(self, mutation=False):
            valid_hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            host = self.headers.get('Host', '')
            if host not in valid_hosts:
                raise PermissionError('Host không được phép.')
            if mutation:
                origin = self.headers.get('Origin')
                if origin is not None and origin != f'http://{host}':
                    raise PermissionError('Yêu cầu phải đến từ ứng dụng local.')
                if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                    raise ValueError('Yêu cầu cần Content-Type application/json.')

        def body(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                raise ValueError('Content-Length không hợp lệ.')
            if not 0 <= length <= 16384:
                raise ValueError('Yêu cầu quá lớn.')
            try:
                value = json.loads(self.rfile.read(length) or b'{}')
            except (ValueError, UnicodeError):
                raise ValueError('JSON không hợp lệ.')
            if not isinstance(value, dict):
                raise ValueError('JSON phải là một object.')
            return value

        def do_GET(self):
            self.dispatch(False)

        def do_POST(self):
            self.dispatch(True)

        def do_PUT(self):
            self.dispatch(True)

        def dispatch(self, mutation):
            try:
                self.check_request(mutation)
                parsed = urlsplit(self.path)
                path = parsed.path
                if not mutation:
                    if path == '/api/status':
                        batches = store.batches()
                        return self.send({'provider': provider_status(), 'counts': {'images': sum(b['completed'] for b in batches), 'concepts': sum(b['planned'] for b in batches), 'batches': len(batches)}, 'activeBatchId': engine.active, 'concurrency': engine.concurrency(), 'inFlight': engine.in_flight})
                    if path == '/api/settings':
                        settings = {**DEFAULTS, **store.settings()}
                        return self.send({
                            **settings,
                            'has_api_key': bool(read_secret(store.root)),
                            'codex_available': bool(shutil.which('codex')),
                            'antigravity_available': bool(shutil.which('agy')),
                        })
                    if path == '/api/batches':
                        return self.send(store.batches())
                    match = re.fullmatch(f'/api/batches/({ID})', path)
                    if match:
                        batch_id = match[1]
                        return self.send({'batch': store.batch(batch_id), 'items': store.items(batch_id), 'events': store.events(batch_id)})
                    match = re.fullmatch(f'/api/batches/({ID})/export', path)
                    if match:
                        batch = store.batch(match[1])
                        archive = export_batch(batch, store.items(match[1]), parse_qs(parsed.query).get('approved') == ['1'])
                        return self.send(archive, 'application/zip', filename=f'puzzle-{match[1][:8]}.zip')
                    match = re.fullmatch(f'/images/({ID})[.](?:png|jpg)', path)
                    if match:
                        item = store.item(match[1])
                        if not item['image_path']:
                            raise KeyError('Ảnh chưa được tạo.')
                        image = Path(item['image_path']).resolve()
                        if not image.is_relative_to((store.root / 'images').resolve()) or not image.is_file():
                            raise KeyError('Không tìm thấy file ảnh.')
                        return self.send(game_jpeg(image), 'image/jpeg')
                    assets = {'/': ROOT / 'web/index.html', '/app.js': ROOT / 'web/app.js', '/styles.css': ROOT / 'web/styles.css', '/sample.png': ROOT / 'output/apricot-kitchen-source.png'}
                    if path in assets and assets[path].is_file():
                        return self.send(assets[path].read_bytes(), mimetypes.guess_type(str(assets[path]))[0] or 'application/octet-stream')
                    raise KeyError('Không tìm thấy trang.')
                payload = self.body()
                if self.command == 'PUT' and path == '/api/settings':
                    with engine.lock:
                        if engine.active:
                            raise ValueError('Tạm dừng batch trước khi đổi kết nối.')
                        allowed = {'provider', 'text_model', 'antigravity_text_model', 'image_model', 'api_key', 'concurrency'}
                        if not payload.keys() <= allowed:
                            raise ValueError('Cài đặt không hợp lệ.')
                        settings = {**DEFAULTS, **store.settings(), **{k: v for k, v in payload.items() if k != 'api_key'}}
                        value = settings['concurrency']
                        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 8:
                            raise ValueError('Số ảnh đồng thời phải là số nguyên từ 1 đến 8.')
                        if settings['provider'] not in {'codex', 'antigravity', 'openai'}:
                            raise ValueError('Provider không hợp lệ.')
                        for key in ('text_model', 'antigravity_text_model', 'image_model'):
                            settings.setdefault(key, '')
                            if not isinstance(settings[key], str) or not re.fullmatch(r'[a-zA-Z0-9._:/-]{0,100}', settings[key]):
                                raise ValueError('Tên model không hợp lệ.')
                        if settings['provider'] == 'openai' and not (settings['text_model'] and settings['image_model']):
                            raise ValueError('OpenAI API cần model context và model ảnh.')
                        if 'api_key' in payload and payload['api_key']:
                            save_secret(store.root, payload['api_key'])
                        store.save_settings(settings)
                        status_cache['at'] = 0
                        return self.send({'saved': True})
                if self.command != 'POST':
                    raise KeyError('Không tìm thấy thao tác.')
                if path == '/api/batches':
                    with engine.lock:
                        if engine.active:
                            raise ValueError('Đang có batch chạy. Tạm dừng hoặc đợi batch đó xong trước.')
                        batch = store.create(payload.get('count'))
                        return self.send(engine.start(batch['id']), status=201)
                match = re.fullmatch(f'/api/batches/({ID})/(start|pause)', path)
                if match:
                    return self.send(engine.start(match[1]) if match[2] == 'start' else engine.pause(match[1]))
                match = re.fullmatch(f'/api/items/({ID})/(review|retry)', path)
                if match:
                    item = store.item(match[1])
                    if match[2] == 'retry':
                        return self.send(engine.retry(item['id']))
                    review = payload.get('review')
                    if review not in {'approved', 'rejected', 'pending'} or item['status'] != 'completed':
                        raise ValueError('Chỉ duyệt ảnh đã tạo xong với trạng thái hợp lệ.')
                    store.set_item(item['id'], review=review)
                    return self.send(store.item(item['id']))
                raise KeyError('Không tìm thấy thao tác.')
            except PermissionError as error:
                self.send({'error': str(error)}, status=403)
            except KeyError as error:
                self.send({'error': str(error).strip("'")}, status=404)
            except (ValueError, TypeError) as error:
                self.send({'error': str(error)}, status=400)
            except Exception:
                self.send({'error': 'Ứng dụng gặp lỗi nội bộ. Kết quả đã lưu được giữ nguyên.'}, status=500)

    class LocalServer(ThreadingHTTPServer):
        def server_close(self):
            engine.shutdown()
            super().server_close()
            process_lock.close()

    try:
        server = LocalServer(('127.0.0.1', port), Handler)
    except BaseException:
        process_lock.close()
        raise
    server.store = store
    server.engine = engine
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Puzzle Atelier — local content studio')
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    args = parser.parse_args()
    server = build_server(args.data_dir.resolve(), args.port, final_root=ROOT / 'Final')
    print(f'Puzzle Atelier: http://127.0.0.1:{server.server_port}', flush=True)
    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
