"""文件职责：cron 解析与下次触发时间计算。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.cron import CronError, next_after, parse_cron


def _dt(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class CronTests(unittest.TestCase):
    def test_every_fifteen_minutes(self) -> None:
        spec = parse_cron("*/15 * * * *")
        self.assertEqual(next_after(spec, _dt("2026-11-27T10:07:00")), _dt("2026-11-27T10:15:00"))

    def test_weekday_morning(self) -> None:
        # 2026-11-27 是周五，下一次工作日 09:30 应落在周一。
        spec = parse_cron("30 9 * * 1-5")
        self.assertEqual(next_after(spec, _dt("2026-11-27T10:00:00")), _dt("2026-11-30T09:30:00"))

    def test_day_or_weekday(self) -> None:
        # 日与周都受限时按 cron 语义取“或”：每月 1 号或每周一。
        spec = parse_cron("0 0 1 * 1")
        self.assertEqual(next_after(spec, _dt("2026-11-02T00:00:00")), _dt("2026-11-09T00:00:00"))

    def test_invalid_expression(self) -> None:
        for expression in ("", "* * *", "61 * * * *", "*/0 * * * *", "5-1 * * * *"):
            with self.subTest(expression=expression):
                with self.assertRaises(CronError):
                    parse_cron(expression)


if __name__ == "__main__":
    unittest.main()
