"""Strict batched editorial review for newly drafted puzzle concepts."""

from __future__ import annotations

import json
import re
from typing import Any

from .axes import describe
from .diversity import FIELDS, _IMAGE_RULES, content_direction, validate_concepts


_REVIEW_LENGTHS = {
    "title": 100,
    "category": 70,
    "subject": 200,
    "scene": 200,
    "story": 200,
    "composition": 200,
    "palette": 150,
    "materials": 200,
    "key": 150,
    "prompt": 1800,
}

_CONCEPT_SCHEMA = {
    "type": "object",
    "properties": {
        field: {"type": "string", "minLength": 1, "maxLength": _REVIEW_LENGTHS[field]}
        for field in FIELDS
    },
    "required": list(FIELDS),
    "additionalProperties": False,
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {
                            "index": {"type": "integer", "minimum": 0},
                            "decision": {"type": "string", "enum": ["keep", "reject"]},
                            "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                        },
                        "required": ["index", "decision", "reason"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "index": {"type": "integer", "minimum": 0},
                            "decision": {"type": "string", "enum": ["revise"]},
                            "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                            "revised": _CONCEPT_SCHEMA,
                        },
                        "required": ["index", "decision", "reason", "revised"],
                        "additionalProperties": False,
                    },
                ]
            },
        }
    },
    "required": ["reviews"],
    "additionalProperties": False,
}


def _compact_concept(concept: dict[str, Any]) -> dict[str, str]:
    """Remove repeated image-policy boilerplate from each prompt sent to the reviewer."""
    compact = {field: str(concept.get(field, "")) for field in FIELDS}
    prompt = re.sub(
        r"\[CURRENT_CONTENT_DIRECTION\].*?\[/CURRENT_CONTENT_DIRECTION\]",
        "",
        compact["prompt"],
        flags=re.DOTALL,
    ).strip()
    prompt = prompt.replace(_IMAGE_RULES, "")
    compact["prompt"] = prompt.strip()
    if concept.get("axes"):
        compact["required_axes"] = describe(concept["axes"])
    return compact


def make_review_prompt(concepts: list[dict[str, Any]]) -> str:
    if not concepts or len(concepts) > 20:
        raise ValueError("Mỗi lượt duyệt context phải có từ 1 đến 20 mục.")
    payload = [{"index": index, "concept": _compact_concept(concept)} for index, concept in enumerate(concepts)]
    return f"""Bạn là biên tập viên kiểm định cuối cho context ảnh game ghép hình. Duyệt toàn bộ {len(concepts)} mục trong một lượt, không tạo ảnh, không duyệt web và không gọi công cụ.

Với mỗi index, chọn đúng một quyết định:
- keep: cảnh rõ ràng, tự nhiên và có thể chụp hoặc dựng hiện thực. Giữ nguyên toàn bộ concept.
- revise: ý tưởng dùng được nhưng cần sửa. Trả về revised có ĐỦ chính xác các trường {', '.join(FIELDS)}.
- reject: ý tưởng mơ hồ, phi thực tế, khó hiểu hoặc không thể sửa gọn mà vẫn giữ ý chính.

ĐỊNH DẠNG KHI REVISE
- title/category/subject/scene/story/composition/palette/materials viết tiếng Việt; prompt bắt buộc viết bằng tiếng Anh; key là khóa ngữ nghĩa bằng tiếng Anh.
- title tối đa 100 ký tự; category 70; subject/scene/story/composition/materials mỗi trường 200; palette/key 150; prompt 1800 ký tự.

TIÊU CHÍ
- title, subject, scene, story, composition và prompt phải cùng mô tả một cảnh cụ thể, dễ hiểu ngay.
- Vật thể phải có cấu tạo, tỷ lệ, điểm tựa, cách sử dụng và quan hệ không gian hợp lý ngoài đời.
- Không ép vật thể tạo chữ, biểu tượng hay hình trang trí; không thêm đạo cụ chỉ để kể chuyện nếu không có lý do tự nhiên.
- Không dùng sơ đồ kỹ thuật, mặt cắt, mô hình lai hoặc thiết bị/công trình khó nhận biết. Với đặc trưng địa danh/văn hóa/kỹ thuật, chỉ giữ khi là mẫu quen thuộc có thật; không khẳng định tên riêng chưa được kiểm chứng. Nếu mơ hồ, dùng vật quen thuộc hoặc reject.
- required_axes là các trục đã gán. "Kiểu ảnh" là bắt buộc: kiểu ảnh không có tầm nhìn xa mà concept lại mở cửa sổ/cửa/hiên/ban công ra phong cảnh thì revise bỏ tầm nhìn đó. Các trục khác chỉ là gợi ý: nếu concept gượng ép theo trục (đồ vật đặt sai chỗ, kết hợp không ai làm ngoài đời, vật lạ thêm vào chỉ để thể hiện vùng/dịp/chất liệu) thì revise bỏ trục đó cho tự nhiên, hoặc reject.
- Đánh giá cả nhóm: tránh lặp chủ thể, loại cảnh, bố cục và cách chia mảng màu. Mỗi cảnh vẫn phải bình tĩnh, rõ nét và có các mốc ghép hình tự nhiên.
- reason phải ngắn, cụ thể. Trả đúng mọi index từ 0 đến {len(concepts) - 1}, mỗi index đúng một lần.

HƯỚNG DẪN NỘI DUNG HIỆN TẠI (áp dụng một lần cho cả nhóm)
{content_direction('vi')}

CONTEXT CẦN DUYỆT
{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}

Chỉ trả JSON hợp lệ theo schema, không markdown."""


def apply_review(
    raw: Any,
    concepts: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply a complete review response; malformed responses fail closed."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("Kết quả duyệt context không phải JSON hợp lệ.") from error
    if not isinstance(raw, dict) or set(raw) != {"reviews"} or not isinstance(raw["reviews"], list):
        raise ValueError("Kết quả duyệt context phải là object chỉ chứa mảng reviews.")
    reviews = raw["reviews"]
    if len(reviews) != len(concepts):
        raise ValueError("Kết quả duyệt context phải trả đúng một quyết định cho mỗi mục.")

    by_index: dict[int, dict[str, Any]] = {}
    for entry in reviews:
        if not isinstance(entry, dict):
            raise ValueError("Mỗi quyết định duyệt context phải là object.")
        decision = entry.get("decision")
        expected = {"index", "decision", "reason", "revised"} if decision == "revise" else {"index", "decision", "reason"}
        if set(entry) != expected or decision not in {"keep", "revise", "reject"}:
            raise ValueError("Quyết định duyệt context có trường hoặc decision không hợp lệ.")
        index = entry.get("index")
        reason = entry.get("reason")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(concepts):
            raise ValueError("Kết quả duyệt context có index ngoài phạm vi.")
        if index in by_index:
            raise ValueError("Kết quả duyệt context lặp index.")
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
            raise ValueError("Mỗi quyết định duyệt context cần reason hợp lệ.")
        if decision == "revise":
            revised = entry.get("revised")
            if not isinstance(revised, dict) or set(revised) != set(FIELDS):
                raise ValueError("Context sửa phải có đầy đủ và chỉ gồm các trường concept bắt buộc.")
            for field in FIELDS:
                value = revised[field]
                if not isinstance(value, str) or not value.strip() or len(value.strip()) > _REVIEW_LENGTHS[field]:
                    raise ValueError(f"Context sửa có trường {field} không hợp lệ.")
        by_index[index] = entry
    if set(by_index) != set(range(len(concepts))):
        raise ValueError("Kết quả duyệt context thiếu index.")

    keep_peers = [
        concepts[index]
        for index in range(len(concepts))
        if by_index[index]["decision"] == "keep"
    ]
    accepted_by_index: dict[int, dict[str, Any]] = {}
    rejected: list[str] = []
    for index, original in enumerate(concepts):
        entry = by_index[index]
        reason = entry["reason"].strip()
        decision = entry["decision"]
        if decision == "reject":
            rejected.append(f"review loại concept {index + 1} ({original['title']}): {reason}")
            continue
        if decision == "keep":
            concept = dict(original)
        else:
            revised = dict(entry["revised"])
            # The review only polishes wording, so the planner's subject, family and props
            # stay with the concept; guessing them from the revised key let rewrites such as
            # "souvenir coins kept in..." slip past the subject and family caps.
            if original.get("main_subject"):
                revised["main_subject"] = original["main_subject"]
            if original.get("subject_family"):
                claimed = original["subject_family"]
                if original.get("family_label"):
                    claimed += f": {original['family_label']}"
                revised["subject_family"] = claimed
            if original.get("props"):
                revised["props"] = original["props"]
            peers = list(history) + keep_peers + list(accepted_by_index.values())
            valid, errors = validate_concepts({"concepts": [revised]}, peers, 1)
            if not valid:
                detail = "; ".join(errors) or "context sửa không hợp lệ"
                rejected.append(f"review sửa concept {index + 1} ({original['title']}) nhưng không qua kiểm tra: {detail}")
                continue
            concept = valid[0]
            for field in ("final_seed", "axes"):
                if original.get(field):
                    concept[field] = original[field]
        audit = {"decision": decision, "reason": reason}
        if decision == "revise":
            audit["original"] = {field: original[field] for field in FIELDS}
        concept["context_review"] = audit
        accepted_by_index[index] = concept
    return [accepted_by_index[index] for index in sorted(accepted_by_index)], rejected
