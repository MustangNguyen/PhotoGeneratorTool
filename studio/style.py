"""Colour statistics used to compare generated images with the Final sample library."""

import json
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat

ROOT = Path(__file__).resolve().parent.parent
STYLE_PROFILE_PATH = ROOT / 'final-style.json'

# Metric names, Vietnamese labels and the direction words used in warnings.
METRICS = {
    'saturation': ('độ bão hòa màu', 'nhạt màu hơn', 'gắt màu hơn'),
    'brightness': ('độ sáng', 'tối hơn', 'chói hơn'),
    'warmth': ('độ ấm', 'lạnh hơn', 'ám nóng hơn'),
    'colorfulness': ('độ rực màu', 'ít màu hơn', 'rực hơn'),
    'contrast': ('độ tương phản', 'phẳng hơn', 'gắt hơn'),
    'detail': ('mật độ chi tiết', 'trống hơn', 'vụn hơn'),
}


def style_stats(source):
    """Return cheap global colour statistics for one image (path or PIL image)."""
    if isinstance(source, Image.Image):
        image = source
        return _stats(image)
    with Image.open(source) as image:
        return _stats(image)


def _stats(image):
    image = ImageOps.exif_transpose(image)
    if image.mode in ('RGBA', 'LA') or 'transparency' in image.info:
        rgba = image.convert('RGBA')
        background = Image.new('RGBA', rgba.size, 'white')
        image = Image.alpha_composite(background, rgba)
    rgb = ImageOps.fit(image.convert('RGB'), (120, 180), method=Image.Resampling.BILINEAR)
    hsv = rgb.convert('HSV')
    _, saturation, value = (ImageStat.Stat(band).mean[0] for band in hsv.split())
    red, green, blue = (list(band.tobytes()) for band in rgb.split())
    count = len(red)
    rg = [r - g for r, g in zip(red, green)]
    yb = [(r + g) / 2 - b for r, g, b in zip(red, green, blue)]
    colorfulness = (_std(rg) ** 2 + _std(yb) ** 2) ** 0.5 + 0.3 * ((sum(rg) / count) ** 2 + (sum(yb) / count) ** 2) ** 0.5
    luma = rgb.convert('L')
    edges = luma.filter(ImageFilter.FIND_EDGES)
    return {
        'saturation': round(saturation, 1),
        'brightness': round(value, 1),
        'warmth': round(sum(red) / count - sum(blue) / count, 1),
        'colorfulness': round(colorfulness, 1),
        'contrast': round(ImageStat.Stat(luma).stddev[0], 1),
        'detail': round(ImageStat.Stat(edges).mean[0], 1),
    }


def _std(values):
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def load_profile(path=None):
    path = Path(path or STYLE_PROFILE_PATH)
    try:
        profile = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return profile if isinstance(profile.get('metrics'), dict) else None


DEFAULT_WARN_SCORE = 0.5


def style_drift(stats, profile=None):
    """Compare one image's stats with the Final distribution.

    Each metric outside the Final p5–p95 band adds how far it is out, measured
    in Final interquartile ranges. A warning is returned only when the total
    passes the profile's warn_score (0.5 flags about 13% of Final itself and
    most of the older muted output). It only warns; it never blocks or regenerates.
    """
    profile = profile if profile is not None else load_profile()
    if not profile or not stats:
        return None
    issues = []
    score = 0.0
    for key, (label, low_word, high_word) in METRICS.items():
        band = profile['metrics'].get(key)
        value = stats.get(key)
        if not band or value is None:
            continue
        spread = max(1.0, band['p75'] - band['p25'])
        if value < band['p5']:
            score += (band['p5'] - value) / spread
            issues.append({'metric': key, 'value': value, 'p5': band['p5'], 'p95': band['p95'],
                           'text': f"{label} {value:g} thấp hơn ảnh mẫu (p5={band['p5']:g}): {low_word}"})
        elif value > band['p95']:
            score += (value - band['p95']) / spread
            issues.append({'metric': key, 'value': value, 'p5': band['p5'], 'p95': band['p95'],
                           'text': f"{label} {value:g} cao hơn ảnh mẫu (p95={band['p95']:g}): {high_word}"})
    if not issues or score <= profile.get('warn_score', DEFAULT_WARN_SCORE):
        return None
    return {'issues': issues, 'score': round(score, 2),
            'message': 'Lệch phong cách Final: ' + '; '.join(issue['text'] for issue in issues) + '.'}


def summarize(values):
    """Percentile summary used to build final-style.json."""
    ordered = sorted(values)

    def pick(fraction):
        index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
        return round(ordered[index], 1)

    return {'p5': pick(0.05), 'p25': pick(0.25), 'median': pick(0.5), 'p75': pick(0.75), 'p95': pick(0.95)}
