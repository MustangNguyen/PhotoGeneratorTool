"""Planning and heuristic diversity checks for puzzle-image concepts.

This module deliberately has no provider integration.  It prepares a compact prompt,
validates the provider response, and rejects obvious repetition.  The duplicate gate
combines lexical checks with a small, explainable catalogue of visual motifs; it
cannot prove that a large catalogue is semantically unique.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
import json
from pathlib import Path
import re
import threading
import unicodedata
from typing import Any, Iterable

from . import final


# Planner categories and target shares come from the Final sample library (studio/final.py).
CATEGORIES = [category for category, _ in final.THEMES.values()]
CATEGORY_TARGETS = {category: share for category, share in final.THEMES.values()}

FIELDS = (
    "title",
    "category",
    "subject",
    "scene",
    "story",
    "composition",
    "palette",
    "materials",
    "key",
    "prompt",
)

_MAX_LENGTH = {
    "title": 120,
    "category": 80,
    "subject": 320,
    "scene": 420,
    "story": 420,
    "composition": 320,
    "palette": 220,
    "materials": 260,
    "key": 180,
    "prompt": 2400,
}

_IMAGE_RULES = (
    "Portrait 600x900 pixels, 2:3 aspect ratio. Polished, high-detail illustration with photographic "
    "realism, like premium jigsaw-puzzle art: bright warm sunlight or golden-hour glow, vivid harmonious "
    "saturated colors with clean whites and open shadows. A cozy, joyful, carefully arranged scene that "
    "fills the frame with many medium-to-large distinct objects, each with its own color, shape and outline, "
    "so every region of the puzzle has a recognizable landmark. Sharp from front to back; no blur, bokeh or haze. "
    "No people, no human figures, no flat cartoon style, "
    "no text, letters, numbers, logos, watermarks, captions, borders, or collage. No mud, bare soil or murky water. "
    "Avoid carpets of tiny repeated details, large empty areas, and dark, cold, grey or washed-out palettes."
)

ART_DIRECTION_PATH = Path(__file__).resolve().parent.parent / 'art-direction.json'
CONTENT_MOTIFS_PATH = Path(__file__).resolve().parent.parent / 'content-motifs.json'
_DIRECTION_START = '[CURRENT_CONTENT_DIRECTION]'
_DIRECTION_END = '[/CURRENT_CONTENT_DIRECTION]'

_MOTIF_FIELDS = frozenset({"title", "subject", "scene", "composition", "key"})
_MOTIF_CACHE_LOCK = threading.Lock()
_MOTIF_CACHE_KEY: tuple[str, int, int] | None = None
_MOTIF_CACHE: tuple[tuple[str, tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]], ...] = ()


def content_direction(language: str) -> str:
    """Read current editor guidance for every request; invalid guidance must not be ignored."""
    try:
        policy = json.loads(ART_DIRECTION_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError('Không đọc được art-direction.json. Kiểm tra file hướng dẫn nội dung.') from error
    key = 'planning_rules' if language == 'vi' else 'image_rules'
    rules = policy.get(key) if isinstance(policy, dict) else None
    if not isinstance(rules, list) or not all(isinstance(rule, str) and rule.strip() for rule in rules):
        raise ValueError(f'art-direction.json: {key} phải là danh sách các hướng dẫn văn bản.')
    return ' '.join(rule.strip() for rule in rules)


_STOPWORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "into", "of", "on", "the", "to", "with",
    "va", "voi", "cua", "cho", "trong", "tren", "duoi", "ben", "mot", "nhung", "cac", "tai", "tu",
    "scene", "view", "bright", "realistic", "detailed", "image", "photo", "photorealistic",
}

# Map interchangeable surface details to their broader semantic family.  This is
# intentionally small and explainable rather than pretending to be an embedding.
_SYNONYM_GROUPS = {
    "fruit": {
        "apple", "apples", "orange", "oranges", "pear", "pears", "peach", "peaches",
        "apricot", "apricots", "plum", "plums", "mango", "mangos", "mangoes", "lemon",
        "lemons", "lime", "limes", "grape", "grapes", "berry", "berries", "banana", "bananas",
        "tao", "cam", "le", "dao", "mo", "man", "xoai", "chanh", "nho", "chuoi", "qua", "trai",
    },
    "flower": {"flower", "flowers", "rose", "roses", "tulip", "tulips", "orchid", "orchids", "hoa", "hong", "lan"},
    "bird": {"bird", "birds", "sparrow", "sparrows", "parrot", "parrots", "chim", "vet", "se"},
    "workshop": {"workshop", "atelier", "studio", "xuong"},
    "kitchen": {"kitchen", "cookhouse", "bep"},
    "market": {"market", "bazaar", "cho"},
    "garden": {"garden", "orchard", "vuon"},
    "table": {"table", "tabletop", "counter", "ban", "ke"},
    "bowl": {"bowl", "basin", "bat", "to"},
    "window": {"window", "windowsill", "cua", "so"},
}

_SYNONYMS = {word: family for family, words in _SYNONYM_GROUPS.items() for word in words}


def _ascii(value: str) -> str:
    value = value.replace("đ", "d").replace("Đ", "D")
    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)).lower()


def _clean(value: str) -> str:
    value = "".join(ch if ch >= " " or ch in "\n\t" else " " for ch in value)
    return re.sub(r"\s+", " ", value).strip()


def _tokens(value: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", _ascii(value))
    return {_SYNONYMS.get(word, word) for word in words if word not in _STOPWORDS and len(word) > 1}


def _similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _normalized_category(value: str) -> str:
    needle = _ascii(_clean(value))
    for category in CATEGORIES:
        candidate = _ascii(category)
        if needle == candidate:
            return category
    # Do not discard legitimate categories that the initial taxonomy missed.
    # Keeping the label makes later counts and review transparent.
    return _clean(value)


def _fingerprint(concept: dict[str, Any]) -> str:
    title = _clean(str(concept.get("title", "")))[:70]
    key = _clean(str(concept.get("key", "")))[:90]
    category = _normalized_category(str(concept.get("category", "Khác")))[:50]
    subject = _clean(str(concept.get("subject", "")))[:55]
    scene = _clean(str(concept.get("scene", "")))[:55]
    composition = _clean(str(concept.get("composition", "")))[:45]
    semantic = " ".join(sorted(_tokens(" ".join(str(concept.get(name, "")) for name in ("subject", "scene", "story", "composition")))))
    palette = _clean(str(concept.get("palette", "")))[:45]
    materials = _clean(str(concept.get("materials", "")))[:35]
    return f"{category} | {title} | {key or semantic[:90]} | S:{subject} | C:{scene} | B:{composition} | Màu:{palette} | Chất:{materials}"


def _phrase_present(text: str, *phrases: str) -> bool:
    """Match complete normalized phrases instead of loose translated token aliases."""
    normalized = re.sub(r"[^a-z0-9]+", " ", _ascii(_clean(text))).strip()
    padded = f" {normalized} "
    return any(
        f" {re.sub(r'[^a-z0-9]+', ' ', _ascii(phrase)).strip()} " in padded
        for phrase in phrases
    )


def _supplemental_motifs_with_revision() -> tuple[
    tuple[str, int, int],
    tuple[tuple[str, tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]], ...],
]:
    """Load and validate the editable motif catalogue, reusing it until the file changes."""
    global _MOTIF_CACHE_KEY, _MOTIF_CACHE
    try:
        stat = CONTENT_MOTIFS_PATH.stat()
    except OSError as error:
        raise ValueError("Không đọc được content-motifs.json. Kiểm tra catalogue mô-típ.") from error
    cache_key = (str(CONTENT_MOTIFS_PATH.resolve()), stat.st_mtime_ns, stat.st_size)
    with _MOTIF_CACHE_LOCK:
        if cache_key == _MOTIF_CACHE_KEY:
            return cache_key, _MOTIF_CACHE
        try:
            raw = json.loads(CONTENT_MOTIFS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("Không đọc được content-motifs.json. Kiểm tra catalogue mô-típ.") from error
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("content-motifs.json: version phải bằng 1.")
        motifs = raw.get("motifs")
        if not isinstance(motifs, list) or not motifs:
            raise ValueError("content-motifs.json: motifs phải là danh sách không rỗng.")

        ids: set[str] = set()
        labels: set[str] = set()
        parsed: list[tuple[str, tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]]] = []
        for index, motif in enumerate(motifs, start=1):
            if not isinstance(motif, dict):
                raise ValueError(f"content-motifs.json: motif {index} phải là object.")
            motif_id = motif.get("id")
            label = motif.get("label")
            if not isinstance(motif_id, str) or not motif_id.strip():
                raise ValueError(f"content-motifs.json: motif {index} cần id không rỗng.")
            if not isinstance(label, str) or not label.strip():
                raise ValueError(f"content-motifs.json: motif {index} cần label không rỗng.")
            motif_id = motif_id.strip()
            label = label.strip()
            if motif_id in ids:
                raise ValueError(f"content-motifs.json: id trùng “{motif_id}”.")
            if label in labels:
                raise ValueError(f"content-motifs.json: label trùng “{label}”.")
            ids.add(motif_id)
            labels.add(label)

            evidence = motif.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError(f"content-motifs.json: motif {motif_id} cần evidence không rỗng.")
            for path_text in evidence:
                if not isinstance(path_text, str) or not path_text.strip():
                    raise ValueError(f"content-motifs.json: motif {motif_id} có evidence không hợp lệ.")
                evidence_path = Path(path_text)
                if evidence_path.is_absolute() or ".." in evidence_path.parts or not evidence_path.parts or evidence_path.parts[0] != "Final":
                    raise ValueError(
                        f"content-motifs.json: evidence của motif {motif_id} phải là đường dẫn tương đối trong Final/."
                    )

            groups = motif.get("groups")
            if not isinstance(groups, list) or not groups:
                raise ValueError(f"content-motifs.json: motif {motif_id} cần groups không rỗng.")
            parsed_groups: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
            for group_index, group in enumerate(groups, start=1):
                if not isinstance(group, dict):
                    raise ValueError(f"content-motifs.json: group {group_index} của {motif_id} phải là object.")
                fields = group.get("fields")
                phrases = group.get("phrases")
                if not isinstance(fields, list) or not fields or not all(isinstance(field, str) for field in fields):
                    raise ValueError(f"content-motifs.json: fields của {motif_id} phải là danh sách không rỗng.")
                unknown_fields = set(fields) - _MOTIF_FIELDS
                if unknown_fields:
                    raise ValueError(
                        f"content-motifs.json: fields không được phép trong {motif_id}: {', '.join(sorted(unknown_fields))}."
                    )
                if not isinstance(phrases, list) or not phrases or not all(
                    isinstance(phrase, str) and phrase.strip() for phrase in phrases
                ):
                    raise ValueError(f"content-motifs.json: phrases của {motif_id} phải là danh sách chuỗi không rỗng.")
                parsed_groups.append((tuple(fields), tuple(phrase.strip() for phrase in phrases)))
            parsed.append((label, tuple(parsed_groups)))

        _MOTIF_CACHE = tuple(parsed)
        _MOTIF_CACHE_KEY = cache_key
        return cache_key, _MOTIF_CACHE


def _supplemental_motifs() -> tuple[tuple[str, tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]], ...]:
    return _supplemental_motifs_with_revision()[1]


def _catalogue_motifs(
    concept: dict[str, Any],
    catalogue: tuple[tuple[str, tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]], ...],
) -> set[str]:
    result: set[str] = set()
    for label, groups in catalogue:
        if all(
            _phrase_present(" ".join(str(concept.get(field, "")) for field in fields), *phrases)
            for fields, phrases in groups
        ):
            result.add(label)
    return result


@lru_cache(maxsize=8192)
def _motifs_cached(
    title_value: str,
    subject_value: str,
    scene_value: str,
    composition_value: str,
    key_value: str,
    catalogue_revision: tuple[str, int, int],
) -> frozenset[str]:
    """Return strong visual formulas visible in the current catalogue.

    Each formula combines a specific subject family with a setting or composition.
    This deliberately does not classify broad subjects such as every boat or animal.
    """
    del catalogue_revision  # Part of the cache key; caller holds the catalogue lock.
    concept = {
        "title": title_value,
        "subject": subject_value,
        "scene": scene_value,
        "composition": composition_value,
        "key": key_value,
    }
    subject = " ".join((subject_value, key_value, title_value))
    setting = " ".join((scene_value, composition_value, key_value, title_value))
    all_text = f"{subject} {setting}"
    result: set[str] = set()

    telescope = _phrase_present(subject, "kính thiên văn", "telescope")
    observatory = _phrase_present(setting, "đài quan sát", "observatory", "observatory terrace", "observatory balcony")
    if telescope and observatory:
        result.add("kính thiên văn tại đài quan sát")

    boat = _phrase_present(subject, "thuyền chèo", "thuyền đánh cá", "canoe", "rowboat", "fishing boat")
    dock = _phrase_present(setting, "bến gỗ", "cầu gỗ", "cầu tàu", "bến cá", "wooden dock", "wooden pier", "pier shelter")
    if boat and dock:
        result.add("thuyền nhỏ neo cạnh bến gỗ")

    chair = _phrase_present(subject, "ghế bành", "ghế mây", "ghế đọc sách", "reading chair", "armchair", "rattan chair")
    window = _phrase_present(all_text, "bên cửa sổ", "cạnh cửa sổ", "cửa sổ lớn", "cửa sổ rộng", "by the window", "beside a window", "window nook")
    if chair and window:
        result.add("ghế thư giãn cạnh cửa sổ")

    cabin = _phrase_present(subject, "chòi gỗ", "nhà kho nhỏ", "small cabin", "woodland cabin", "small painted barn")
    path = _phrase_present(setting, "lối đi lát", "lối đá dẫn", "stone path", "grassy farm path")
    if cabin and path:
        result.add("công trình nhỏ cuối lối đá xanh")

    aircraft = _phrase_present(subject, "khinh khí cầu", "máy bay lượn", "hot air balloon", "hot-air balloon", "glider aircraft")
    river_valley = (
        _phrase_present(setting, "thung lũng xanh", "green valley")
        and _phrase_present(setting, "sông cong", "sông uốn", "winding river", "river")
    )
    if aircraft and river_valley:
        result.add("vật thể bay trên thung lũng có sông uốn")

    cove = _phrase_present(all_text, "vịnh xanh", "vịnh biển trong", "clear cove", "coastal cove")
    cliffs = _phrase_present(all_text, "vách đá sáng", "vách đá mượt", "smooth cliffs", "cliffs")
    if cove and cliffs:
        result.add("vịnh xanh được ôm bởi vách đá sáng")

    instrument = _phrase_present(subject, "đàn cello", "đàn hạc", "cello", "concert harp")
    chair_near_instrument = _phrase_present(all_text, "cạnh ghế", "tựa bên ghế", "instrument beside chair", "chair")
    tall_window = _phrase_present(setting, "cửa sổ cao", "cửa sổ tạo", "tall window", "window")
    if instrument and chair_near_instrument and tall_window:
        result.add("nhạc cụ cạnh ghế và cửa sổ trong phòng nhạc")

    storefront = _phrase_present(subject, "tiệm bánh", "quầy sách", "bookstore", "bakery")
    awning = _phrase_present(all_text, "mái hiên", "street awning", "striped awning")
    pastel_street = _phrase_present(all_text, "phố pastel", "con ngõ lát đá", "pastel old town", "quiet lane")
    if storefront and awning and pastel_street:
        result.add("cửa tiệm pastel dưới mái hiên ở phố lát đá")

    pond = _phrase_present(all_text, "hồ vườn", "vườn nước", "garden pond", "water garden")
    lily_pads = _phrase_present(all_text, "lá sen lớn", "vài lá lớn", "large lily pads", "large pads")
    if pond and lily_pads:
        result.add("hồ vườn trong với lá sen lớn")

    forest_path = _phrase_present(all_text, "rừng phong", "khu rừng", "woodland", "forest") and _phrase_present(
        all_text, "lối lát đá", "con đường lát đá", "stone path", "stone-path"
    )
    if forest_path:
        result.add("lối đá uốn dưới tán rừng")

    fruit_container = _phrase_present(subject, "bát gốm", "đĩa quả", "đĩa lê", "ceramic bowl", "fruit bowl", "fruit plate")
    fruit_content = _phrase_present(
        subject,
        "trái cây", "quả lê", "lê chín", "quả hồng", "hồng chín",
        "fruit", "ripe pears", "ripe persimmons", "apples", "oranges", "apricots",
    )
    table_window = _phrase_present(all_text, "bàn ăn cạnh cửa sổ", "bên cửa sổ", "window table", "bright window")
    if fruit_container and fruit_content and table_window:
        result.add("trái cây bày trên bàn cạnh cửa sổ")
    result.update(_catalogue_motifs(concept, _MOTIF_CACHE))
    return frozenset(result)


def _motifs(concept: dict[str, Any]) -> set[str]:
    """Return motif labels, memoized by relevant fields and catalogue revision."""
    relevant = tuple(str(concept.get(field, "")) for field in ("title", "subject", "scene", "composition", "key"))
    while True:
        revision, _ = _supplemental_motifs_with_revision()
        with _MOTIF_CACHE_LOCK:
            if revision != _MOTIF_CACHE_KEY:
                continue
            return set(_motifs_cached(*relevant, revision))


def _motif_history_summary(history: list[dict[str, Any]]) -> str:
    """Summarize motifs using the full history, including entries outside prompt sampling."""
    titles: dict[str, list[str]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for item in history:
        if not isinstance(item, dict):
            continue
        item_motifs = _motifs(item)
        counts.update(item_motifs)
        for motif in item_motifs:
            title = _clean(str(item.get("title", ""))) or "không tiêu đề"
            if title not in titles[motif] and len(titles[motif]) < 3:
                titles[motif].append(title)
    if not titles:
        return "- (chưa nhận diện mô-típ mạnh nào)"
    return "\n".join(
        f"- {motif}: đã dùng {counts[motif]} lần; ví dụ {', '.join(examples)}"
        for motif, examples in sorted(titles.items())
    )


def _history_sample(history: list[dict[str, Any]], max_items: int = 72) -> list[dict[str, Any]]:
    """Take recent entries plus category representatives without losing rare groups."""
    if len(history) <= max_items:
        return history
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in history:
        by_category[_normalized_category(str(item.get("category", "Khác")))].append(item)
    sampled: list[dict[str, Any]] = []
    # One representative from every category first, including unforeseen categories.
    for category in sorted(by_category):
        sampled.append(by_category[category][-1])
    seen = {id(item) for item in sampled}
    for item in reversed(history):
        if len(sampled) >= max_items:
            break
        if id(item) not in seen:
            sampled.append(item)
            seen.add(id(item))
    return sampled[:max_items]


def category_plan(count: int, counts: dict[str, int]) -> dict[str, int]:
    """Split one request across categories so the catalogue moves toward Final's theme shares."""
    total = sum(counts.get(category, 0) for category in CATEGORIES) + count
    weight = sum(CATEGORY_TARGETS.values())
    deficit = {
        category: max(0.0, CATEGORY_TARGETS[category] * total / weight - counts.get(category, 0))
        for category in CATEGORIES
    }
    pool = sum(deficit.values()) or 1.0
    raw = {category: count * deficit[category] / pool for category in CATEGORIES}
    plan = {category: int(raw[category]) for category in CATEGORIES}
    leftovers = sorted(CATEGORIES, key=lambda c: (plan[c] - raw[c], -CATEGORY_TARGETS[c]))
    for category in leftovers[: count - sum(plan.values())]:
        plan[category] += 1
    return {category: number for category, number in plan.items() if number}


def _final_examples(offset: int) -> str:
    lines = []
    for category in CATEGORIES:
        captions = final.examples(category, 3, offset)
        if captions:
            lines.append(f"- {category}: " + "; ".join(captions))
    return "\n".join(lines) or "- (chưa có final-index.json)"


def make_planning_prompt(
    count: int,
    history: list[dict[str, Any]],
    category_counts: dict[str, int] | None = None,
) -> str:
    """Build a compact catalogue-planning prompt for a text model."""
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("count must be a positive integer")
    history = history if isinstance(history, list) else []
    counts = Counter(category_counts or {})
    if not category_counts:
        counts.update(_normalized_category(str(item.get("category", "Khác"))) for item in history if isinstance(item, dict))
    count_text = ", ".join(f"{name}: {counts.get(name, 0)} (mục tiêu {CATEGORY_TARGETS[name]}%)" for name in CATEGORIES)
    plan_text = ", ".join(f"{name}: {number}" for name, number in category_plan(count, counts).items())
    # Bound text sent to the model; duplicate checks still use the entire history.
    history_lines = []
    history_chars = 0
    for item in _history_sample([x for x in history if isinstance(x, dict)]):
        line = f"- {_fingerprint(item)}"
        if history_chars + len(line) + 1 > 10000:
            break
        history_lines.append(line)
        history_chars += len(line) + 1
    fingerprints = "\n".join(history_lines)
    if not fingerprints:
        fingerprints = "- (chưa có concept trước đó)"
    return f"""Bạn là biên tập viên concept cho game ghép hình. Hãy tạo chính xác {count} concept mới, sâu sắc và khác nhau về ngữ cảnh.

CẢNH TỰ NHIÊN TRƯỚC, BỐ CỤC SAU
- Chọn tình huống đời thực đơn giản: vật gì, ở đâu, đặt/tựa/treo thế nào, vì sao các vật cùng xuất hiện.
- title gọi đúng vật/cảnh có trong ảnh; story không bắt buộc có đạo cụ hay dấu vết. Không có người thì không đặt tiêu đề như đang có vũ công.
- subject/scene xác định cấu tạo, tỷ lệ và điểm tựa cần thiết; composition chọn góc máy/cắt khung, không ép vật tạo chữ X hoặc hình trang trí.
- Viết prompt ngắn, cụ thể, theo thứ tự cảnh → vị trí/cấu tạo → máy ảnh → ánh sáng/mảng màu. Không nối lại toàn bộ quy tắc chung; ứng dụng sẽ thêm một lần.

MỤC TIÊU ĐA DẠNG
- Mỗi concept phải khác về chủ thể chính, môi trường, câu chuyện qua đồ vật và cấu trúc bố cục; tạo một cảnh mạch lạc, không ghép ngẫu nhiên nhiều thứ.
- Đổi loại trái cây, màu sắc, giống hoa hoặc vật trang trí trong cùng kiểu cảnh KHÔNG tạo thành concept mới. Cùng họ chủ thể chính + cùng môi trường là gần trùng và phải tránh.
- Chia concept theo GỢI Ý PHÂN BỔ bên dưới để kho ảnh tiến dần về tỷ lệ chủ đề của Final, trong mỗi nhóm vẫn đa dạng nội dung, bố cục, chất liệu và mảng màu. Không bù nhóm thiếu bằng các cảnh gần giống. Nếu có nhóm thực sự mới, đặt tên rõ ràng.
- Hãy tự nghĩ thêm phương án dự phòng khi suy luận để thay thế concept trùng, nhưng chỉ xuất đúng {count} concept tốt nhất.

NHÓM GỢI Ý
{'; '.join(CATEGORIES)}

SỐ LƯỢNG HIỆN CÓ THEO NHÓM VÀ TỶ LỆ MỤC TIÊU THEO FINAL
{count_text}

GỢI Ý PHÂN BỔ {count} CONCEPT LẦN NÀY
{plan_text}

ẢNH MẪU FINAL CÙNG NHÓM (học độ phong phú, màu và không khí; không dựng lại đúng cảnh, concept trùng ảnh mẫu sẽ bị loại)
{_final_examples(len(history))}

DẤU VÂN TAY CONCEPT ĐÃ DÙNG (được rút gọn từ {len(history)} mục; phải tránh lặp ý, không sao chép):
{fingerprints}

MÔ-TÍP HÌNH ẢNH ĐÃ DÙNG TRONG TOÀN BỘ {len(history)} MỤC (không đổi tên/chủ thể phụ rồi dựng lại cùng công thức hình):
{_motif_history_summary(history)}

ĐẦU RA
Chỉ trả về JSON hợp lệ, không markdown, theo dạng {{"concepts":[...]}}. Mỗi phần tử có đủ chuỗi:
title, category, subject, scene, story, composition, palette, materials, key, prompt.
- title/category/subject/scene/story/composition/palette/materials viết tiếng Việt, thật ngắn gọn.
- title tối đa 100 ký tự; category 70; subject/scene/story/composition/materials mỗi trường tối đa 200 ký tự; palette 150; key 150; prompt 1800 ký tự.
- key là khóa ngữ nghĩa chuẩn bằng tiếng Anh, mô tả họ chủ thể + môi trường + câu chuyện; không dùng số thứ tự.
- palette mô tả màu chủ đạo, màu phụ, điểm nhấn và vùng tương ứng; composition nêu cách phân bố các mảng lớn và mốc nối. Không chỉ liệt kê tên màu.
- prompt là prompt ảnh hoàn chỉnh bằng tiếng Anh, thể hiện đúng bố cục, chất liệu và palette đã chọn.

HƯỚNG DẪN NỘI DUNG HIỆN TẠI — ÁP DỤNG NGAY KHI CHỌN CONCEPT
{content_direction("vi")}

QUY CÁCH ẢNH BẮT BUỘC
Ảnh dọc 600x900, tỷ lệ 2:3; minh họa trau chuốt gần như ảnh thật giống kho Final: nắng ấm hoặc vàng chiều, màu tươi bão hòa hài hòa, cảnh ấm cúng vui vẻ được bày biện chăm chút. Chủ thể chính rõ, xung quanh nhiều vật cỡ vừa-lớn khác màu khác hình phủ khung để mảnh nào cũng có mốc; nét từ trước ra sau, không blur. Màu nhạt, xỉn hoặc ánh sáng phẳng kiểu ảnh tư liệu là lỗi. Không chữ/logo/watermark; không người, không hoạt hình phẳng, không bùn đất; tránh thảm chi tiết li ti lặp lại và vùng trống lớn.
"""


def image_prompt(concept: dict[str, Any]) -> str:
    """Return the provider prompt with non-negotiable art constraints appended."""
    base = _clean(str(concept.get("prompt", "")))
    # Replace the prior version when retrying or using a previously planned prompt.
    base = re.sub(re.escape(_DIRECTION_START) + r'.*?' + re.escape(_DIRECTION_END), '', base, flags=re.DOTALL).strip()
    if _IMAGE_RULES.lower() not in base.lower():
        base = f"{base.rstrip(' .')}. {_IMAGE_RULES}" if base else _IMAGE_RULES
    guidance = content_direction('en')
    if guidance:
        base += f" {_DIRECTION_START} Current art direction takes precedence over conflicting visual details in the earlier brief. {guidance} {_DIRECTION_END}"
    return _clean(base)


def _duplicate_reason(
    candidate: dict[str, str],
    other: dict[str, Any],
    candidate_motifs: set[str] | None = None,
    other_motifs: set[str] | None = None,
) -> str | None:
    candidate_key = _tokens(candidate["key"])
    other_key = _tokens(str(other.get("key", "")))
    combined_fields = ("title", "subject", "scene", "story")
    candidate_all = _tokens(" ".join(candidate[name] for name in combined_fields)) | candidate_key
    other_all = _tokens(" ".join(str(other.get(name, "")) for name in combined_fields)) | other_key
    subject_score = _similarity(_tokens(candidate["subject"]), _tokens(str(other.get("subject", ""))))
    scene_score = _similarity(_tokens(candidate["scene"]), _tokens(str(other.get("scene", ""))))
    overall = _similarity(candidate_all, other_all)
    key_score = _similarity(candidate_key, other_key)
    same_family = bool(_tokens(candidate["subject"]) & _tokens(str(other.get("subject", ""))) & set(_SYNONYM_GROUPS))
    repeated_motifs = (candidate_motifs if candidate_motifs is not None else _motifs(candidate)) & (
        other_motifs if other_motifs is not None else _motifs(other)
    )

    if repeated_motifs:
        return f"lặp mô-típ hình ảnh “{sorted(repeated_motifs)[0]}” dù có thể đã đổi tiêu đề hoặc câu chuyện"
    if candidate_key and candidate_key == other_key:
        return "trùng khóa ngữ nghĩa canonical"
    if overall >= 0.72 or key_score >= 0.78:
        return f"dấu vân tay từ vựng quá giống (score {max(overall, key_score):.2f})"
    if same_family and scene_score >= 0.46 and (subject_score >= 0.25 or overall >= 0.42):
        return "cùng họ chủ thể chính và môi trường; có thể chỉ là đổi loại/màu"
    return None


_FINAL_TOKENS: tuple[Any, list[tuple[dict[str, Any], set[str]]]] = ((), [])


def _final_duplicate(candidate: dict[str, str]) -> dict[str, Any] | None:
    """Return the Final sample whose caption the candidate title repeats, if any.

    Only the short title is compared: long subject/scene text would contain most
    four-word captions by chance.
    """
    global _FINAL_TOKENS
    items = final.load_index()
    if _FINAL_TOKENS[0] is not items:
        _FINAL_TOKENS = (items, [(item, _tokens(item["caption"])) for item in items])
    title = _tokens(candidate["title"])
    for item, caption in _FINAL_TOKENS[1]:
        if (len(caption) >= 4 and caption <= title) or (len(caption) >= 3 and _similarity(title, caption) >= 0.75):
            return item
    return None


def validate_concepts(raw: Any, history: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, str]], list[str]]:
    """Normalize valid concepts and reject malformed or obvious near-duplicates.

    Reasons are intended for retry prompts and human review.  Passing this heuristic
    is evidence against obvious lexical repetition, not proof of semantic uniqueness.
    """
    errors: list[str] = []
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        return [], ["limit phải là số nguyên không âm"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return [], ["phản hồi không phải JSON hợp lệ"]
    if not isinstance(raw, dict) or not isinstance(raw.get("concepts"), list):
        return [], ["phản hồi phải có dạng object JSON với mảng concepts"]

    source = raw["concepts"]
    if len(source) > limit:
        errors.append(f"phản hồi có {len(source)} concept; chỉ nhận tối đa {limit}")
    accepted: list[dict[str, str]] = []
    comparison_pool: list[dict[str, Any]] = [x for x in history if isinstance(x, dict)]
    comparison_motifs = [_motifs(item) for item in comparison_pool]

    for index, item in enumerate(source[:limit], start=1):
        if not isinstance(item, dict):
            errors.append(f"concept {index}: phải là object")
            continue
        normalized: dict[str, str] = {}
        malformed = False
        for field in FIELDS:
            value = item.get(field)
            if not isinstance(value, str):
                errors.append(f"concept {index}: trường {field} phải là chuỗi")
                malformed = True
                continue
            value = _clean(value)
            if not value:
                errors.append(f"concept {index}: thiếu trường {field}")
                malformed = True
            elif len(value) > _MAX_LENGTH[field]:
                errors.append(f"concept {index}: trường {field} dài quá {_MAX_LENGTH[field]} ký tự")
                malformed = True
            normalized[field] = value
        if malformed:
            continue
        normalized["category"] = _normalized_category(normalized["category"])
        sample = _final_duplicate(normalized)
        if sample:
            errors.append(
                f"concept {index} ({normalized['title']}): gần trùng ảnh mẫu Final «{sample['caption']}» ({sample['path']}); "
                "hãy giữ phong cách nhưng đổi cảnh"
            )
            continue
        candidate_motifs = _motifs(normalized)
        reason = next(
            (
                reason
                for other, other_motifs in zip(comparison_pool, comparison_motifs)
                if (reason := _duplicate_reason(normalized, other, candidate_motifs, other_motifs))
            ),
            None,
        )
        if reason:
            errors.append(f"concept {index} ({normalized['title']}): gần trùng — {reason}; heuristic cần người duyệt nếu nghi ngờ")
            continue
        normalized["prompt"] = image_prompt(normalized)
        accepted.append(normalized)
        comparison_pool.append(normalized)
        comparison_motifs.append(candidate_motifs)
    return accepted, errors


def order_concepts(concepts: list[dict[str, Any]], previous: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Greedily separate neighboring category, palette, and composition patterns."""
    remaining = list(concepts)
    ordered: list[dict[str, Any]] = []
    prior = previous

    def adjacency_penalty(item: dict[str, Any], last: dict[str, Any] | None) -> tuple[float, str]:
        if not last:
            return (0.0, _clean(str(item.get("title", ""))))
        penalty = 0.0
        if _normalized_category(str(item.get("category", ""))) == _normalized_category(str(last.get("category", ""))):
            penalty += 4.0
        penalty += 2.0 * _similarity(_tokens(str(item.get("palette", ""))), _tokens(str(last.get("palette", ""))))
        penalty += 2.0 * _similarity(_tokens(str(item.get("composition", ""))), _tokens(str(last.get("composition", ""))))
        penalty += _similarity(_tokens(str(item.get("subject", ""))), _tokens(str(last.get("subject", ""))))
        return (penalty, _clean(str(item.get("title", ""))))

    while remaining:
        chosen = min(remaining, key=lambda item: adjacency_penalty(item, prior))
        ordered.append(chosen)
        remaining.remove(chosen)
        prior = chosen
    return ordered
