"""文件职责：在沙箱内用标准库分析 CSV，并输出可校验的 JSON 摘要。"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def analyze_csv(input_path: Path) -> dict:
    """计算行列、缺失值、重复行和数值列统计。"""
    if not input_path.is_file():
        raise ValueError(f"input file not found: {input_path}")
    if input_path.stat().st_size == 0:
        raise ValueError("CSV file is empty")

    with input_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    if not rows:
        raise ValueError("CSV file contains no rows")

    header = [cell.strip() for cell in rows[0]]
    if not header or any(not name for name in header):
        raise ValueError("CSV header contains an empty column name")
    if len(set(header)) != len(header):
        raise ValueError("CSV header contains duplicate column names")

    data_rows = rows[1:]
    for index, row in enumerate(data_rows, start=2):
        if len(row) != len(header):
            raise ValueError(f"row {index} has {len(row)} columns; expected {len(header)}")

    columns: dict[str, dict] = {}
    for column_index, name in enumerate(header):
        raw_values = [row[column_index].strip() for row in data_rows]
        non_empty = [value for value in raw_values if value]
        missing = len(raw_values) - len(non_empty)
        numbers: list[float] = []
        for value in non_empty:
            try:
                number = float(value)
            except ValueError:
                numbers = []
                break
            if not math.isfinite(number):
                numbers = []
                break
            numbers.append(number)

        if numbers and len(numbers) == len(non_empty):
            integer_values = all(value.lstrip("-").isdigit() for value in non_empty)
            columns[name] = {
                "type": "integer" if integer_values else "number",
                "count": len(non_empty),
                "missing": missing,
                "min": min(numbers),
                "max": max(numbers),
                "mean": statistics.fmean(numbers),
                "median": statistics.median(numbers),
                "stdev": statistics.stdev(numbers) if len(numbers) >= 2 else 0.0,
            }
        else:
            columns[name] = {
                "type": "string",
                "count": len(non_empty),
                "missing": missing,
                "unique": len(set(non_empty)),
            }

    normalized_rows = [tuple(cell.strip() for cell in row) for row in data_rows]
    return {
        "rows": len(data_rows),
        "columns": len(header),
        "column_names": header,
        "duplicate_rows": len(normalized_rows) - len(set(normalized_rows)),
        "column_summary": columns,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_path")
    parser.add_argument("output_path")
    args = parser.parse_args()
    try:
        summary = analyze_csv(Path(args.input_path))
        output = Path(args.output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"success": True, "output": str(output)}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": "CSV_ANALYSIS_FAILED", "message": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
