"""Diversity axes assigned to each planned concept.

diversity-axes.json lists axes (viewpoint, season, region…) and their values.  Each
concept gets a random subset of axes, and for every chosen axis the value used least
often in the history.  This spreads the catalogue across many directions instead of
only rejecting repeats after the fact.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import random
import threading
from typing import Any

from . import final

AXES_PATH = Path(__file__).resolve().parent.parent / 'diversity-axes.json'
_THEMES = frozenset(final.THEMES)

_LOCK = threading.Lock()
_CACHE_KEY = None
_CACHE: dict[str, Any] | None = None


def _themes(axis_id: str, value: Any, field: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f'diversity-axes.json: {field} của trục {axis_id} phải là danh sách mã nhóm.')
    unknown = set(value) - _THEMES
    if unknown:
        raise ValueError(f"diversity-axes.json: trục {axis_id} có mã nhóm không hợp lệ: {', '.join(sorted(unknown))}.")
    return frozenset(value)


def load_axes(path: Path | None = None) -> dict[str, Any]:
    """Return the validated axis catalogue, reloading only when the file changes."""
    global _CACHE_KEY, _CACHE
    path = Path(path or AXES_PATH)
    try:
        stat = path.stat()
    except OSError as error:
        raise ValueError('Không đọc được diversity-axes.json.') from error
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    with _LOCK:
        if key == _CACHE_KEY and _CACHE is not None:
            return _CACHE
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise ValueError('Không đọc được diversity-axes.json.') from error
        if not isinstance(raw, dict) or raw.get('version') != 1:
            raise ValueError('diversity-axes.json: version phải bằng 1.')
        per = raw.get('per_concept')
        if (
            not isinstance(per, dict)
            or not all(isinstance(per.get(name), int) and not isinstance(per.get(name), bool) for name in ('min', 'max'))
            or not 0 <= per['min'] <= per['max']
        ):
            raise ValueError('diversity-axes.json: per_concept cần min/max là số nguyên, 0 ≤ min ≤ max.')
        axes = raw.get('axes')
        if not isinstance(axes, list) or not axes:
            raise ValueError('diversity-axes.json: axes phải là danh sách không rỗng.')
        parsed = []
        ids = set()
        for index, axis in enumerate(axes, start=1):
            if not isinstance(axis, dict):
                raise ValueError(f'diversity-axes.json: trục {index} phải là object.')
            axis_id = axis.get('id')
            label = axis.get('label')
            if not isinstance(axis_id, str) or not axis_id.strip() or axis_id in ids:
                raise ValueError(f'diversity-axes.json: trục {index} cần id không rỗng, không trùng.')
            if not isinstance(label, str) or not label.strip():
                raise ValueError(f'diversity-axes.json: trục {axis_id} cần label.')
            weight = axis.get('weight', 1)
            if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
                raise ValueError(f'diversity-axes.json: weight của trục {axis_id} phải là số dương.')
            values = axis.get('values')
            if not isinstance(values, list) or not values or not all(isinstance(v, str) and v.strip() for v in values):
                raise ValueError(f'diversity-axes.json: values của trục {axis_id} phải là danh sách chuỗi không rỗng.')
            if len({v.strip() for v in values}) != len(values):
                raise ValueError(f'diversity-axes.json: trục {axis_id} có giá trị trùng.')
            ids.add(axis_id)
            parsed.append({
                'id': axis_id,
                'label': label.strip(),
                'weight': float(weight),
                'values': [v.strip() for v in values],
                'categories': _themes(axis_id, axis.get('categories'), 'categories'),
                'exclude': _themes(axis_id, axis.get('exclude_categories'), 'exclude_categories'),
            })
        rules = _parse_rules(raw.get('rules', []), {axis['id']: axis for axis in parsed})
        _CACHE = {'min': per['min'], 'max': per['max'], 'axes': parsed, 'rules': rules}
        _CACHE_KEY = key
        return _CACHE


def _parse_selector(raw: Any, axes_by_id: dict[str, dict[str, Any]], where: str) -> tuple[str, str, frozenset[str] | None]:
    """('axis', id, values|None) or ('category', '', themes)."""
    if not isinstance(raw, dict):
        raise ValueError(f'diversity-axes.json: {where} phải là object.')
    if set(raw) == {'category'}:
        return ('category', '', _themes(where, raw['category'], 'category'))
    if 'axis' not in raw or not set(raw) <= {'axis', 'values'}:
        raise ValueError(f'diversity-axes.json: {where} cần "axis" (kèm "values" tùy chọn) hoặc chỉ "category".')
    axis = axes_by_id.get(raw['axis'])
    if axis is None:
        raise ValueError(f"diversity-axes.json: {where} dùng trục không tồn tại “{raw['axis']}”.")
    if 'values' not in raw:
        return ('axis', axis['id'], None)
    values = raw['values']
    if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
        raise ValueError(f'diversity-axes.json: values trong {where} phải là danh sách chuỗi.')
    unknown = [v for v in values if v not in axis['values']]
    if unknown:
        raise ValueError(f"diversity-axes.json: {where} có giá trị không thuộc trục {axis['id']}: {unknown[0]}.")
    return ('axis', axis['id'], frozenset(values))


def _parse_rules(raw: Any, axes_by_id: dict[str, dict[str, Any]]) -> list[tuple[str, tuple, tuple]]:
    if not isinstance(raw, list):
        raise ValueError('diversity-axes.json: rules phải là danh sách.')
    rules = []
    for index, rule in enumerate(raw, start=1):
        where = f'rule {index}'
        if not isinstance(rule, dict) or rule.get('type') not in {'exclude', 'require'} or not set(rule) <= {'type', 'if', 'then', 'note'}:
            raise ValueError(f'diversity-axes.json: {where} cần type exclude/require, if và then.')
        rules.append((
            rule['type'],
            _parse_selector(rule.get('if'), axes_by_id, where + '.if'),
            _parse_selector(rule.get('then'), axes_by_id, where + '.then'),
        ))
    return rules


def _matches(selector: tuple, theme: str | None, assigned: dict[str, str]) -> bool | None:
    """True/False when the selector can be judged now; None when its axis is not assigned."""
    kind, axis_id, values = selector
    if kind == 'category':
        return theme in values
    if axis_id not in assigned:
        return None
    return values is None or assigned[axis_id] in values


def compatible(theme: str | None, assigned: dict[str, str], rules: list[tuple[str, tuple, tuple]]) -> bool:
    """exclude: if and then must not both hold. require: when if holds, then must hold (once judgeable)."""
    for kind, left, right in rules:
        if not _matches(left, theme, assigned):
            continue
        result = _matches(right, theme, assigned)
        if kind == 'exclude' and result:
            return False
        if kind == 'require' and result is False:
            return False
    return True


def _applies(axis: dict[str, Any], category: str) -> bool:
    theme = final.CATEGORY_TO_THEME.get(category)
    if axis['categories'] and theme not in axis['categories']:
        return False
    return theme not in axis['exclude']


def value_counts(history: list[dict[str, Any]]) -> dict[str, Counter[str]]:
    counts: dict[str, Counter[str]] = {}
    for item in history:
        assigned = item.get('axes') if isinstance(item, dict) else None
        if isinstance(assigned, dict):
            for axis_id, value in assigned.items():
                counts.setdefault(axis_id, Counter())[str(value)] += 1
    return counts


def _weighted_sample(axes: list[dict[str, Any]], count: int, rng: random.Random) -> list[dict[str, Any]]:
    pool = list(axes)
    chosen = []
    while pool and len(chosen) < count:
        pick = rng.choices(pool, weights=[axis['weight'] for axis in pool])[0]
        chosen.append(pick)
        pool.remove(pick)
    return chosen


def assign_axes(
    categories: list[str],
    history: list[dict[str, Any]],
    rng: random.Random | None = None,
) -> list[dict[str, str]]:
    """One {axis_id: value} per category slot; values least used so far win, ties random."""
    rng = rng or random.Random()
    catalogue = load_axes()
    counts = value_counts(history)
    result = []
    for category in categories:
        theme = final.CATEGORY_TO_THEME.get(category)
        eligible = [axis for axis in catalogue['axes'] if _applies(axis, category)]
        number = rng.randint(catalogue['min'], catalogue['max'])
        assigned: dict[str, str] = {}
        # Visit every eligible axis in weighted order and stop at the target count, so an
        # axis left without a compatible value is replaced by the next one.
        for axis in _weighted_sample(eligible, len(eligible), rng):
            if len(assigned) >= number:
                break
            candidates = [v for v in axis['values'] if compatible(theme, {**assigned, axis['id']: v}, catalogue['rules'])]
            if not candidates:
                continue
            used = counts.setdefault(axis['id'], Counter())
            lowest = min(used[value] for value in candidates)
            value = rng.choice([value for value in candidates if used[value] == lowest])
            used[value] += 1  # Later slots in this batch see this choice.
            assigned[axis['id']] = value
        result.append(assigned)
    return result


def describe(assigned: dict[str, str]) -> str:
    """'Góc nhìn: …; Mùa: …' using labels from the catalogue."""
    labels = {axis['id']: axis['label'] for axis in load_axes()['axes']}
    return '; '.join(f"{labels.get(axis_id, axis_id)}: {value}" for axis_id, value in assigned.items())
