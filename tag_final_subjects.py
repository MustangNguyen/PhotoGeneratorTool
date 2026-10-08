#!/usr/bin/env python3
"""Tag each Final sample with an English main_subject so seeds can skip full subject families.

Runs once (and again after Final grows); already tagged items are skipped. Uses the Codex
CLI login like the app, a few captions per call, several calls side by side.
"""
import argparse
import json
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from studio import final
from studio.providers import CodexProvider
from studio.store import Store

ROOT = Path(__file__).resolve().parent
SCHEMA = {
    'type': 'object', 'properties': {'subjects': {'type': 'array', 'items': {
        'type': 'object', 'properties': {'index': {'type': 'integer'}, 'main_subject': {'type': 'string', 'minLength': 1, 'maxLength': 40}},
        'required': ['index', 'main_subject'], 'additionalProperties': False,
    }}}, 'required': ['subjects'], 'additionalProperties': False,
}
PROMPT = '''Return only the requested JSON. Text-only task; do not browse, run commands or modify files.

Each line is a Vietnamese caption of a jigsaw puzzle picture. For every line give main_subject:
the single most prominent thing in the picture as a generic English singular noun, 1-3 words,
lowercase, no adjectives, colours or setting (e.g. "rabbit", "bicycle", "railway station",
"bread", "teapot", "cottage"). Answer every index exactly once.

{lines}

JSON shape: {{"subjects":[{{"index":0,"main_subject":"rabbit"}}, ...]}}'''


def dump(raw):
    """Same layout as the committed index: header keys, then one item per line."""
    head = ',\n'.join(f'{json.dumps(key)}:{json.dumps(value, ensure_ascii=False)}' for key, value in raw.items() if key != 'items')
    body = ',\n'.join(json.dumps(item, ensure_ascii=False, separators=(',', ':')) for item in raw['items'])
    return '{' + head + ',\n"items":[\n' + body + '\n]}\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chunk', type=int, default=120)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    data = ROOT / 'data'
    settings = Store(data).settings()
    raw = json.loads(final.FINAL_INDEX_PATH.read_text(encoding='utf-8'))
    items = raw['items']
    todo = [i for i, item in enumerate(items) if item.get('caption') and not item.get('main_subject')]
    chunks = [todo[n:n + args.chunk] for n in range(0, len(todo), args.chunk)]
    print(f'{len(todo)} captions to tag in {len(chunks)} calls', flush=True)
    lock = threading.Lock()
    work = data / 'final-tagging'

    def tag(chunk):
        provider = CodexProvider(settings, work)
        lines = '\n'.join(f'{n}: {items[i]["caption"]}' for n, i in enumerate(chunk))
        directory = Path(tempfile.mkdtemp(prefix='tag-', dir=provider.work_root))
        reply = json.loads(provider._exec(PROMPT.format(lines=lines), directory, structured=True, timeout=600, output_schema=SCHEMA))
        tagged = 0
        with lock:
            for entry in reply['subjects']:
                n = entry.get('index')
                subject = ' '.join(str(entry.get('main_subject', '')).lower().split())
                if isinstance(n, int) and 0 <= n < len(chunk) and subject:
                    items[chunk[n]]['main_subject'] = subject
                    tagged += 1
            # Save after every call so an interrupted run keeps its progress.
            temporary = final.FINAL_INDEX_PATH.with_suffix('.json.tmp')
            temporary.write_text(dump(raw), encoding='utf-8')
            temporary.replace(final.FINAL_INDEX_PATH)
        print(f'tagged {tagged}/{len(chunk)}', flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for future in [pool.submit(tag, chunk) for chunk in chunks]:
            try:
                future.result()
            except Exception as error:
                print(f'chunk failed: {error}', flush=True)
    print('missing:', sum(1 for item in items if item.get('caption') and not item.get('main_subject')))


if __name__ == '__main__':
    main()
