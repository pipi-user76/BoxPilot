"""文件职责：解析标准五段 cron 表达式并计算下一次触发时间。

不引入额外依赖：支持 `*`、`*/n`、`a,b,c`、`a-b` 与它们的组合，足够覆盖
“每天几点”“每 N 分钟”“工作日”等常见定时任务。时间统一按 UTC 计算。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_FIELD_NAMES = ("分钟", "小时", "日期", "月份", "星期")
_ALIASES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}
_MAX_LOOKAHEAD_MINUTES = 366 * 24 * 60


class CronError(ValueError):
    """cron 表达式非法；message 直接返回给调用方。"""


@dataclass(frozen=True)
class CronSpec:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    day_restricted: bool
    weekday_restricted: bool
    expression: str


def _parse_number(token: str, low: int, high: int, label: str) -> int:
    key = token.strip().lower()
    if key in _ALIASES:
        value = _ALIASES[key]
    else:
        try:
            value = int(key)
        except ValueError as exc:
            raise CronError(f"无法识别的{label}：{token}") from exc
    if not low <= value <= high:
        raise CronError(f"{label}超出范围：{token}")
    return value


def _expand_field(field: str, index: int) -> tuple[frozenset[int], bool]:
    low, high = _FIELD_RANGES[index]
    label = _FIELD_NAMES[index]
    values: set[int] = set()
    restricted = False
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"{label}存在空分段")
        step = 1
        if "/" in part:
            base, _, raw_step = part.partition("/")
            try:
                step = int(raw_step)
            except ValueError as exc:
                raise CronError(f"{label}的步长不是整数：{part}") from exc
            if step <= 0:
                raise CronError(f"{label}的步长必须大于 0")
            part = base
        if part == "*":
            start, end = low, high
        elif "-" in part:
            raw_start, _, raw_end = part.partition("-")
            start = _parse_number(raw_start, low, high, label)
            end = _parse_number(raw_end, low, high, label)
            if start > end:
                raise CronError(f"{label}的区间起点大于终点：{part}")
        else:
            start = _parse_number(part, low, high, label)
            end = start
        if part != "*" or step != 1:
            restricted = True
        values.update(range(start, end + 1, step))
    if index == 4 and high in values:
        values.discard(7)
        values.add(0)
    return frozenset(values), restricted


def parse_cron(expression: str) -> CronSpec:
    """解析五段 cron；字段顺序为分、时、日、月、周。"""
    if not expression or not expression.strip():
        raise CronError("cron 表达式不能为空")
    parts = expression.split()
    if len(parts) != 5:
        raise CronError("cron 表达式需要 5 段：分 时 日 月 周")
    minutes, _ = _expand_field(parts[0], 0)
    hours, _ = _expand_field(parts[1], 1)
    days, day_restricted = _expand_field(parts[2], 2)
    months, _ = _expand_field(parts[3], 3)
    weekdays, weekday_restricted = _expand_field(parts[4], 4)
    return CronSpec(
        minutes=minutes,
        hours=hours,
        days=days,
        months=months,
        weekdays=weekdays,
        day_restricted=day_restricted,
        weekday_restricted=weekday_restricted,
        expression=expression.strip(),
    )


def _matches_day(spec: CronSpec, moment: datetime) -> bool:
    day_ok = moment.day in spec.days
    # Python: 周一=0；cron: 周日=0。
    weekday_ok = ((moment.weekday() + 1) % 7) in spec.weekdays
    if spec.day_restricted and spec.weekday_restricted:
        return day_ok or weekday_ok
    if spec.day_restricted:
        return day_ok
    if spec.weekday_restricted:
        return weekday_ok
    return True


def next_after(spec: CronSpec, moment: datetime, tz=None) -> datetime:
    """返回严格晚于 moment 的下一次触发时间，按 tz（默认 UTC）匹配字段。"""
    zone = tz or timezone.utc
    current = moment.astimezone(zone).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(_MAX_LOOKAHEAD_MINUTES):
        if (
            current.month in spec.months
            and current.hour in spec.hours
            and current.minute in spec.minutes
            and _matches_day(spec, current)
        ):
            return current
        current += timedelta(minutes=1)
    raise CronError("cron 表达式在一年内没有可执行时间")
