"""Standalone CSV profiler used inside the sandbox.

The module only depends on the Python standard library so it can be uploaded
next to a CSV file and executed with a plain interpreter. It produces a
profile, view recommendations and lightweight chart data (no image binaries).
"""

from __future__ import annotations

import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
MAX_CATEGORIES = 30
MAX_SERIES = 4
MAX_POINTS = 80
MAX_HEATMAP_COLS = 8
MAX_HEATMAP_ROWS = 12
MAX_SAMPLE_ROWS = 12


TIME_HEADER = re.compile(
    r"(日期|时间|年份|年度|月份|季度|版本|迭代|date|time|year|month|quarter|version)",
    re.IGNORECASE,
)
VERSION_VALUE = re.compile(r"^v?\d+(?:\.\d+){1,3}$", re.IGNORECASE)
DATE_VALUE = re.compile(r"^\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?$")
HIERARCHY_HEADER = re.compile(r"(父级|父项|子项|层级|分类|目录|模块|path|parent|child)", re.IGNORECASE)


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _number(value: str) -> float | None:
    text = _clean(value)
    if not text:
        return None
    text = text.replace(",", "").replace(" ", "")
    text = text.rstrip("%")
    text = text.lstrip("¥$€£")
    text = text.rstrip("元万亿次秒毫秒ms")
    try:
        number = float(text)
    except ValueError:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _time_key(value: str) -> Any:
    text = _clean(value)
    if not text:
        return None
    match = VERSION_VALUE.fullmatch(text)
    if match:
        return tuple(int(part) for part in re.findall(r"\d+", text))
    if DATE_VALUE.match(text):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m", "%Y/%m", "%Y"):
            try:
                return datetime.strptime(text.replace("/", "-").replace(".", "-"), fmt)
            except ValueError:
                continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _trim(value: str, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", _clean(value))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def analyze_csv(input_path: Path) -> dict[str, Any]:
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError("CSV 文件为空") from exc
        if not header or any(not _clean(name) for name in header):
            raise ValueError("CSV 表头不能为空")
        if len({name.strip() for name in header}) != len(header):
            raise ValueError("CSV 表头存在重复列名")

        rows: list[list[str]] = []
        for index, row in enumerate(reader, start=2):
            if not any(_clean(cell) for cell in row):
                continue
            if len(row) != len(header):
                raise ValueError(f"第 {index} 行有 {len(row)} 列，表头有 {len(header)} 列")
            rows.append([_clean(cell) for cell in row])

    if not rows:
        raise ValueError("CSV 没有数据行")

    names = [name.strip() for name in header]
    columns: list[list[str]] = [[row[i] for row in rows] for i in range(len(names))]

    duplicate_rows = len(rows) - len({tuple(row) for row in rows})
    total_cells = len(rows) * len(names)
    missing_cells = sum(1 for row in rows for cell in row if not cell)
    completeness = round((total_cells - missing_cells) / total_cells, 4) if total_cells else 0.0

    stats: list[dict[str, Any]] = []
    roles: list[dict[str, Any]] = []
    for name, values in zip(names, columns):
        column_stats = _column_stats(name, values)
        role = _detect_role(name, values, column_stats)
        role["name"] = name
        column_stats["role"] = role["role"]
        column_stats["role_reason"] = role["reason"]
        stats.append(column_stats)
        roles.append(role)

    table_kind = _detect_table_kind(stats, roles, rows)
    profile = {
        "signals": {
            "time_columns": [item["name"] for item in roles if item["role"] == "time"],
            "measure_columns": [item["name"] for item in roles if item["role"] == "measure"],
            "dimension_columns": [
                item["name"] for item in roles if item["role"] in {"category", "enum", "hierarchy"}
            ],
            "long_text_columns": [item["name"] for item in roles if item["role"] == "long_text"],
            "has_time": any(item["role"] == "time" for item in roles),
            "measure_count": sum(1 for item in roles if item["role"] == "measure"),
        },
        "columns": roles,
    }

    charts = _build_charts(names, columns, rows, roles)
    view_options = _view_options(table_kind, charts, roles, len(rows))
    recommended = _recommended_view(table_kind, view_options, charts)
    sample = [dict(zip(names, row)) for row in rows[:MAX_SAMPLE_ROWS]]

    return {
        "version": SCHEMA_VERSION,
        "rows": len(rows),
        "columns": len(names),
        "column_names": names,
        "duplicate_rows": duplicate_rows,
        "missing_cells": missing_cells,
        "completeness": completeness,
        "column_summary": stats,
        "profile": profile,
        "table_kind": table_kind,
        "recommended_view": recommended,
        "view_options": view_options,
        "charts": charts,
        "sample": sample,
    }


def _column_stats(name: str, values: list[str]) -> dict[str, Any]:
    nonempty = [value for value in values if value]
    numbers = [number for number in (_number(value) for value in nonempty) if number is not None]
    numeric_ratio = len(numbers) / len(nonempty) if nonempty else 0.0
    unique = len(set(nonempty))
    item: dict[str, Any] = {
        "name": name,
        "count": len(nonempty),
        "missing": len(values) - len(nonempty),
        "unique": unique,
        "numeric_ratio": round(numeric_ratio, 4),
        "avg_length": round(sum(len(value) for value in nonempty) / len(nonempty), 2) if nonempty else 0,
        "max_length": max((len(value) for value in nonempty), default=0),
        "examples": [_trim(value, 40) for value in nonempty[:3]],
    }
    if nonempty and numeric_ratio >= 0.98:
        numbers.sort()
        is_integer = all(float(number).is_integer() for number in numbers)
        item.update(
            {
                "type": "integer" if is_integer else "number",
                "min": _round(min(numbers)),
                "max": _round(max(numbers)),
                "mean": _round(sum(numbers) / len(numbers)),
                "median": _round(numbers[len(numbers) // 2]),
                "stdev": _round(_stdev(numbers)),
            }
        )
    else:
        counts: Counter[str] = Counter(nonempty)
        item["type"] = "string"
        item["top_values"] = [
            {"value": _trim(value, 40), "count": count} for value, count in counts.most_common(5)
        ]
    return item


def _stdev(numbers: list[float]) -> float:
    if len(numbers) < 2:
        return 0.0
    mean = sum(numbers) / len(numbers)
    return math.sqrt(sum((number - mean) ** 2 for number in numbers) / len(numbers))


def _detect_role(name: str, values: list[str], stats: dict[str, Any]) -> dict[str, str]:
    nonempty = [value for value in values if value]
    if not nonempty:
        return {"role": "long_text", "reason": "整列为空"}
    unique = stats["unique"]
    time_hits = sum(1 for value in nonempty if _time_key(value) is not None)
    header_time = bool(TIME_HEADER.search(name))
    if unique >= 2 and time_hits / len(nonempty) >= 0.8 and (header_time or len(nonempty) <= 60):
        if any(DATE_VALUE.match(value) or VERSION_VALUE.fullmatch(value) for value in nonempty) or header_time:
            return {"role": "time", "reason": f"{time_hits}/{len(nonempty)} 个值可解析为日期或版本"}
    if stats.get("type") in {"integer", "number"}:
        return {"role": "measure", "reason": f"{stats['count']} 个数值，可用于计算"}
    if HIERARCHY_HEADER.search(name):
        return {"role": "hierarchy", "reason": "列名包含层级/分类语义"}
    if any(value.startswith((" ", "\t")) or " > " in value or " / " in value for value in nonempty):
        return {"role": "hierarchy", "reason": "存在缩进或路径分隔的值"}
    avg_length = stats["avg_length"]
    max_length = stats["max_length"]
    if 2 <= unique <= 8 and avg_length <= 10 and max_length <= 16:
        return {"role": "enum", "reason": f"仅 {unique} 个短标签，适合作分类轴"}
    if avg_length >= 30 or max_length >= 100:
        return {"role": "long_text", "reason": "平均文本较长，适合检索/摘要而不是坐标轴"}
    if unique <= min(MAX_CATEGORIES, max(2, len(nonempty) * 0.8)):
        return {"role": "category", "reason": f"{unique} 个不同取值，可作维度"}
    return {"role": "long_text", "reason": "取值分散且偏长，按文本处理"}


def _detect_table_kind(
    stats: list[dict[str, Any]], roles: list[dict[str, Any]], rows: list[list[str]]
) -> str:
    measures = [item for item in roles if item["role"] == "measure"]
    times = [item for item in roles if item["role"] == "time"]
    dims = [item for item in roles if item["role"] in {"category", "enum", "hierarchy"}]
    labels = [item for item in roles if item["role"] in {"category", "enum"}]
    long_texts = [item for item in roles if item["role"] == "long_text"]
    text_like = [item for item in roles if item["role"] in {"long_text", "category", "enum"}]

    if times and measures:
        return "numeric_timeseries"
    if measures and (len(dims) >= 2 or (len(labels) >= 1 and len(measures) >= 2 and len(rows) >= 3)):
        return "cross_matrix"
    if measures and labels:
        return "numeric_snapshot"
    if not measures and len(long_texts) >= 2 and len(text_like) >= 2:
        return "compare_checklist"
    if not measures and len(long_texts) == len(roles) >= 1:
        return "text_archive"
    if not measures and text_like:
        return "compare_checklist" if len(text_like) >= 2 else "text_archive"
    return "mixed"


def _aggregate(keys: list[str], values: list[float]) -> float | None:
    numbers = [value for value in values if value is not None]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _dedupe(values: list[str], limit: int) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = _trim(value, 32)
        if text and text not in seen:
            seen.append(text)
        if len(seen) >= limit:
            break
    return seen


def _build_charts(
    names: list[str], columns: list[list[str]], rows: list[list[str]], roles: list[dict[str, Any]]
) -> dict[str, Any]:
    charts: dict[str, Any] = {}
    role_map = {item["name"]: item["role"] for item in roles}
    measures = [item["name"] for item in roles if item["role"] == "measure"]
    times = [item["name"] for item in roles if item["role"] == "time"]
    dims = [item["name"] for item in roles if item["role"] in {"category", "enum", "hierarchy"}]
    labels = [item["name"] for item in roles if item["role"] in {"category", "enum"}]

    profile_items = []
    for name, values in zip(names, columns):
        filled = sum(1 for value in values if value)
        profile_items.append({"label": _trim(name, 16), "value": filled})
    charts["profile_bar"] = {
        "type": "bar",
        "title": "各列有效值数量",
        "label": "列",
        "value_label": "非空单元格",
        "items": profile_items[:20],
    }

    line = _build_line(names, columns, rows, times, measures)
    if line:
        charts["line"] = line
    bar = _build_bar(names, columns, rows, dims or labels, measures)
    if bar:
        charts["bar"] = bar
    pie = _build_pie(names, columns, rows, labels, measures)
    if pie:
        charts["pie"] = pie
    heatmap = _build_heatmap(names, columns, rows, dims, labels, measures)
    if heatmap:
        charts["heatmap"] = heatmap
    scatter = _build_scatter(names, columns, measures)
    if scatter:
        charts["scatter"] = scatter
    mindmap = _build_mindmap(names, columns, rows, roles)
    if mindmap:
        charts["mindmap"] = mindmap
    return charts


def _build_line(
    names: list[str],
    columns: list[list[str]],
    rows: list[list[str]],
    times: list[str],
    measures: list[str],
) -> dict[str, Any] | None:
    if not times or not measures or len(rows) < 2:
        return None
    time_name = times[0]
    time_index = names.index(time_name)
    measure_indexes = [names.index(name) for name in measures[:MAX_SERIES]]
    records: list[tuple[Any, str, list[float | None]]] = []
    for row in rows:
        key = _time_key(row[time_index])
        if key is None:
            continue
        values = [_number(row[index]) for index in measure_indexes]
        records.append((key, row[time_index], values))
    if len(records) < 2 or len({record[1] for record in records}) < 2:
        return None
    if len({type(record[0]) for record in records}) == 1:
        try:
            records.sort(key=lambda item: item[0])
        except TypeError:
            pass
    if len(records) > MAX_POINTS:
        step = len(records) / MAX_POINTS
        records = [records[int(index * step)] for index in range(MAX_POINTS)]
    series = []
    for offset, name in enumerate(measures[:MAX_SERIES]):
        points = [
            [record[1], _round(record[2][offset])]
            for record in records
            if record[2][offset] is not None
        ]
        if len(points) >= 2:
            series.append({"name": name, "points": points})
    if not series:
        return None
    return {
        "type": "line",
        "title": f"{time_name} 趋势",
        "x_label": time_name,
        "y_label": "值",
        "series": series,
    }


def _build_bar(
    names: list[str],
    columns: list[list[str]],
    rows: list[list[str]],
    dims: list[str],
    measures: list[str],
) -> dict[str, Any] | None:
    if not dims or not measures:
        return None
    candidates = []
    for dim in dims:
        unique = len(set(value for value in columns[names.index(dim)] if value))
        if 2 <= unique <= MAX_CATEGORIES:
            candidates.append((unique, dim))
    if not candidates:
        return None
    candidates.sort()
    dim = candidates[0][1]
    measure = measures[0]
    dim_index, measure_index = names.index(dim), names.index(measure)
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if not row[dim_index]:
            continue
        value = _number(row[measure_index])
        if value is not None:
            buckets[_trim(row[dim_index], 24)].append(value)
    items = []
    for label, values in buckets.items():
        mean = _aggregate([label], values)
        if mean is not None:
            items.append({"label": label, "value": _round(mean)})
    items.sort(key=lambda item: item["value"], reverse=True)
    if len(items) < 2:
        return None
    return {
        "type": "bar",
        "title": f"{dim} 与 {measure}",
        "label": dim,
        "value_label": f"{measure}（均值）",
        "items": items[:15],
    }


def _build_pie(
    names: list[str],
    columns: list[list[str]],
    rows: list[list[str]],
    labels: list[str],
    measures: list[str],
) -> dict[str, Any] | None:
    if not labels or not measures:
        return None
    dim = labels[0]
    unique = len(set(value for value in columns[names.index(dim)] if value))
    if not 2 <= unique <= 8:
        return None
    measure = measures[0]
    dim_index, measure_index = names.index(dim), names.index(measure)
    buckets: dict[str, float] = defaultdict(float)
    for row in rows:
        if not row[dim_index]:
            continue
        value = _number(row[measure_index])
        if value is not None:
            buckets[_trim(row[dim_index], 24)] += value
    items = [
        {"label": label, "value": _round(value)}
        for label, value in buckets.items()
        if value is not None and value >= 0
    ]
    items.sort(key=lambda item: item["value"], reverse=True)
    total = sum(item["value"] for item in items)
    if len(items) < 2 or total <= 0:
        return None
    return {
        "type": "pie",
        "title": f"{dim} 占比",
        "label": dim,
        "value_label": measure,
        "items": items[:8],
    }


def _build_heatmap(
    names: list[str],
    columns: list[list[str]],
    rows: list[list[str]],
    dims: list[str],
    labels: list[str],
    measures: list[str],
) -> dict[str, Any] | None:
    if not measures:
        return None
    if len(dims) >= 2:
        x_dim, y_dim = dims[0], dims[1]
        measure = measures[0]
        x_vals = _dedupe(columns[names.index(x_dim)], MAX_HEATMAP_COLS)
        y_vals = _dedupe(columns[names.index(y_dim)], MAX_HEATMAP_ROWS)
        x_index, y_index, value_index = names.index(x_dim), names.index(y_dim), names.index(measure)
        buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
        for row in rows:
            value = _number(row[value_index])
            if value is None:
                continue
            x, y = _trim(row[x_index], 32), _trim(row[y_index], 32)
            if x and y:
                buckets[(x, y)].append(value)
        cells = []
        for y in y_vals:
            for x in x_vals:
                values = buckets.get((x, y))
                if values:
                    cells.append({"x": x, "y": y, "value": _round(_aggregate([], values))})
        if len(cells) < 2:
            return None
        return {
            "type": "heatmap",
            "title": f"{y_dim} × {x_dim}",
            "x_label": x_dim,
            "y_label": y_dim,
            "value_label": measure,
            "x_labels": x_vals,
            "y_labels": y_vals,
            "cells": cells,
        }

    if labels and len(measures) >= 2:
        row_dim = labels[0]
        row_vals = _dedupe(columns[names.index(row_dim)], MAX_HEATMAP_ROWS)
        row_index = names.index(row_dim)
        measure_indexes = [names.index(name) for name in measures[:MAX_HEATMAP_COLS]]
        x_labels = measures[:MAX_HEATMAP_COLS]
        cells = []
        for row in rows:
            row_label = _trim(row[row_index], 32)
            if row_label not in row_vals:
                continue
            for x_label, index in zip(x_labels, measure_indexes):
                value = _number(row[index])
                if value is not None:
                    cells.append({"x": x_label, "y": row_label, "value": _round(value)})
        if len(cells) < 2:
            return None
        return {
            "type": "heatmap",
            "title": f"{row_dim} × 指标",
            "x_label": "指标",
            "y_label": row_dim,
            "value_label": "值",
            "x_labels": x_labels,
            "y_labels": row_vals,
            "cells": cells,
        }
    return None


def _build_scatter(
    names: list[str], columns: list[list[str]], measures: list[str]
) -> dict[str, Any] | None:
    if len(measures) < 2:
        return None
    x_name, y_name = measures[0], measures[1]
    x_index, y_index = names.index(x_name), names.index(y_name)
    points = []
    for x_value, y_value in zip(columns[x_index], columns[y_index]):
        x, y = _number(x_value), _number(y_value)
        if x is not None and y is not None:
            points.append([_round(x), _round(y)])
        if len(points) >= 200:
            break
    if len(points) < 3:
        return None
    return {
        "type": "scatter",
        "title": f"{x_name} 与 {y_name}",
        "x_label": x_name,
        "y_label": y_name,
        "points": points,
    }


def _build_mindmap(
    names: list[str],
    columns: list[list[str]],
    rows: list[list[str]],
    roles: list[dict[str, Any]],
) -> dict[str, Any] | None:
    hierarchy = [item["name"] for item in roles if item["role"] == "hierarchy"]
    labels = [item["name"] for item in roles if item["role"] in {"category", "enum"}]
    texts = [item["name"] for item in roles if item["role"] in {"long_text", "category", "enum"}]
    if hierarchy:
        parent = hierarchy[0]
        child_candidates = [
            item["name"] for item in roles
            if item["name"] != parent and item["role"] in {"category", "enum", "long_text"}
        ]
        if child_candidates:
            child = child_candidates[0]
            p_index, c_index = names.index(parent), names.index(child)
            groups: dict[str, list[str]] = defaultdict(list)
            for row in rows:
                key, value = _trim(row[p_index], 28), _trim(row[c_index], 40)
                if key and value and value not in groups[key]:
                    groups[key].append(value)
            children = []
            for key, values in list(groups.items())[:10]:
                children.append({"name": key, "children": [{"name": value} for value in values[:6]]})
            if children:
                return {"type": "mindmap", "title": f"{parent} 层级", "root": {"name": parent, "children": children}}

    if labels and len(texts) >= 2:
        label = labels[0] if labels[0] in texts else texts[0]
        source_columns = [name for name in texts if name != label][:4]
        label_index = names.index(label)
        groups: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            key = _trim(row[label_index], 28)
            if not key or key in seen:
                continue
            seen.add(key)
            children = []
            for source in source_columns:
                value = _trim(row[names.index(source)], 60)
                if value:
                    children.append({"name": source, "value": value})
            groups.append({"name": key, "children": children})
            if len(groups) >= 10:
                break
        if groups:
            return {
                "type": "mindmap",
                "title": f"{label} 对比结构",
                "root": {"name": label, "children": groups},
            }
    return None


def _view_options(
    table_kind: str,
    charts: dict[str, Any],
    roles: list[dict[str, Any]],
    row_count: int,
) -> list[dict[str, Any]]:
    labels = {
        "line": "折线图",
        "bar": "柱状图",
        "pie": "饼图",
        "heatmap": "热力图",
        "scatter": "散点图",
        "mindmap": "思维导图",
        "profile_bar": "列总览",
    }
    reasons = {
        "line": "需要时间列和可计算数值，且至少 2 个数据点" if row_count < 2 else "需要时间列与数值列",
        "bar": "需要一个分类维度与一个数值列",
        "pie": "需要 2–8 个类别且有对应数值",
        "heatmap": "需要两个维度（或一个维度 × 多个指标）和数值",
        "scatter": "需要两个数值列与至少 3 行数据",
        "mindmap": "需要层级列，或多来源对比清单",
        "profile_bar": "始终可用",
    }
    preferred = {
        "numeric_timeseries": "line",
        "cross_matrix": "heatmap",
        "compare_checklist": "mindmap",
        "text_archive": "profile_bar",
        "numeric_snapshot": "bar",
        "mixed": "bar",
    }.get(table_kind)
    options = []
    for view in ("line", "bar", "pie", "heatmap", "scatter", "mindmap", "profile_bar"):
        enabled = view == "profile_bar" or view in charts
        score = 10
        if view == preferred and enabled:
            score = 95
        elif enabled:
            score = {"bar": 80, "line": 75, "heatmap": 70, "mindmap": 65, "pie": 60, "scatter": 50}.get(view, 20)
        if not enabled and row_count < 2 and view in {"line", "scatter"}:
            score = 0
        options.append(
            {
                "id": view,
                "label": labels[view],
                "enabled": enabled,
                "score": score,
                "reason": (
                    reasons[view]
                    if not enabled
                    else (
                        "检测到多来源文本对比，适合层级梳理"
                        if view == "mindmap" and table_kind == "compare_checklist"
                        else "与当前表格结构匹配"
                    )
                ),
            }
        )
    options.sort(key=lambda item: (not item["enabled"], -item["score"]))
    return options


def _recommended_view(
    table_kind: str, options: list[dict[str, Any]], charts: dict[str, Any]
) -> str:
    mapping = {
        "numeric_timeseries": "line",
        "cross_matrix": "heatmap",
        "compare_checklist": "mindmap",
        "text_archive": "profile_bar",
        "numeric_snapshot": "bar",
        "mixed": "bar",
    }
    preferred = mapping.get(table_kind)
    enabled = {item["id"] for item in options if item["enabled"]}
    if preferred in enabled:
        return preferred
    for item in options:
        if item["id"] != "profile_bar" and item["id"] in enabled:
            return item["id"]
    return "profile_bar"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python analyze_csv.py input.csv summary.json", file=sys.stderr)
        return 2
    source = Path(argv[1])
    target = Path(argv[2])
    try:
        summary = analyze_csv(source)
    except Exception as exc:  # pragma: no cover - CLI boundary
        target.write_text(
            json.dumps({"error": "CSV_ANALYSIS_FAILED", "message": str(exc)}, ensure_ascii=False),
            encoding="utf-8",
        )
        return 2
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
