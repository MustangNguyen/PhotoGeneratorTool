"""The Final sample library: themes, captions and image paths used as a style reference.

final-index.json is committed (captions, grid, colour stats per image); the images
themselves stay on the owner's machine under Final/ and are only read when present.
"""

import json
import threading
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FINAL_INDEX_PATH = ROOT / 'final-index.json'
FINAL_ROOT = ROOT / 'Final'

# Index theme -> planner category, with the share each theme has in Final (2,204 images).
THEMES = {
    'food': ('Ẩm thực và bàn ăn', 22),
    'interior': ('Nội thất ấm cúng', 14),
    'garden': ('Hoa, vườn và thiên nhiên tươi', 13),
    'objects': ('Đồ vật hoài niệm và sưu tầm', 11),
    'facade': ('Phố, mặt tiền và công trình', 10),
    'animal': ('Động vật dễ thương', 9),
    'vehicle': ('Phương tiện cổ và du lịch', 8),
    'shop': ('Cửa tiệm và quầy hàng', 5),
    'coast': ('Biển và nghỉ dưỡng ven biển', 5),
    'drink': ('Đồ uống và tiệc trà', 3),
}
CATEGORY_TO_THEME = {category: theme for theme, (category, _) in THEMES.items()}

_LOCK = threading.Lock()
_CACHE_KEY = None
_CACHE = ()


def load_index(path=None):
    """Return Final items, reloading only when the index file changes. Missing file -> ()."""
    global _CACHE_KEY, _CACHE
    path = Path(path or FINAL_INDEX_PATH)
    try:
        stat = path.stat()
    except OSError:
        return ()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _LOCK:
        if key != _CACHE_KEY:
            try:
                raw = json.loads(path.read_text(encoding='utf-8'))
                items = raw.get('items') if isinstance(raw, dict) else None
            except (OSError, ValueError):
                items = None
            _CACHE = tuple(item for item in items or () if isinstance(item, dict) and item.get('caption') and item.get('path'))
            _CACHE_KEY = key
        return _CACHE


def examples(category, count=4, offset=0, items=None):
    """A rotating, deterministic sample of Final captions for one planner category."""
    theme = CATEGORY_TO_THEME.get(category)
    items = load_index() if items is None else items
    pool = [item['caption'] for item in items if item.get('theme') == theme and not item.get('people')]
    if not pool:
        return []
    step = max(1, len(pool) // count)
    return [pool[(offset + index * step) % len(pool)] for index in range(min(count, len(pool)))]


def image_candidates(root=None, items=None):
    """Final images that exist on this machine, shaped like completed items for near_image."""
    root = Path(root or FINAL_ROOT)
    if not root.is_dir():
        return []
    items = load_index() if items is None else items
    result = []
    for item in items:
        path = root / item['path']
        if path.is_file():
            result.append({'id': 'final:' + item['path'], 'title': f"Final/{item['path']} ({item['caption']})", 'image_path': str(path)})
    return result


def theme_counts(items=None):
    counts = defaultdict(int)
    for item in load_index() if items is None else items:
        counts[item.get('theme')] += 1
    return dict(counts)
