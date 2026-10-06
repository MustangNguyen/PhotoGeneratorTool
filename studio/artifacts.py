import hashlib
import io
import json
import zipfile
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


def game_jpeg(source):
    """Encode an orientation-correct portrait game image, including legacy PNGs."""
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        if image.width < 300 or image.height < 450:
            raise ValueError('Ảnh từ dịch vụ quá nhỏ; cần ít nhất 300×450.')
        if image.mode in ('RGBA', 'LA') or 'transparency' in image.info:
            rgba = image.convert('RGBA')
            background = Image.new('RGBA', rgba.size, 'white')
            image = Image.alpha_composite(background, rgba)
        normalized = ImageOps.fit(image.convert('RGB'), (600, 900), method=Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        normalized.save(buffer, format='JPEG', quality=95, subsampling=0)
        return buffer.getvalue()


def normalize_image(source, target):
    """Preserve the provider original and save a 600×900 JPEG game export."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_bytes(game_jpeg(source))
    temporary.replace(target)
    return str(target)


def fingerprint(path):
    with Image.open(path) as source:
        image = source.convert('RGB').resize((16, 16), Image.Resampling.LANCZOS)
        # Low-resolution RGB difference is only a repeat warning, never a semantic claim.
        return image.tobytes()


def near_image(path, candidates, cache=None):
    cache = cache if cache is not None else {}
    current = fingerprint(path)
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    best = None
    for candidate in candidates:
        other = Path(candidate['image_path'])
        if not other.exists():
            continue
        try:
            stamp = (other.stat().st_mtime_ns, other.stat().st_size)
            cached = cache.get(str(other))
            if cached is None or cached[0] != stamp:
                cached = (stamp, hashlib.sha256(other.read_bytes()).hexdigest(), fingerprint(other))
                cache[str(other)] = cached
            exact = cached[1] == digest
            compare = cached[2]
            distance = sum(abs(a-b) for a, b in zip(current, compare)) / len(current)
        except (OSError, ValueError, UnidentifiedImageError):
            continue
        if exact or distance < 9:
            score = 0 if exact else round(distance, 2)
            if best is None or score < best['distance']:
                best = {'item_id': candidate['id'], 'title': candidate['title'], 'distance': score, 'exact': exact}
    return best


def export_batch(batch, items, approved=False):
    selected = [item for item in items if item['status'] == 'completed' and (not approved or item['review'] == 'approved')]
    if not selected:
        raise ValueError('Chưa có ảnh phù hợp để xuất.')
    buffer = io.BytesIO()
    manifest = {'batch': batch, 'dimensions': [600, 900], 'format': 'JPEG', 'approved_only': approved, 'items': []}
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for item in selected:
            path = Path(item['image_path'])
            if not path.is_file():
                raise ValueError(f"Không tìm thấy file của ảnh {item['title']}.")
            name = f"{item['position']+1:04d}-{item['id'][:8]}.jpg"
            archive.writestr(name, game_jpeg(path))
            metadata = {key: value for key, value in item.items() if key not in {'image_path', 'image_url'}}
            metadata['file'] = name
            manifest['items'].append(metadata)
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    return buffer.getvalue()
