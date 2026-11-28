from unittest import TestCase

from backend.models import TaskRecord, TaskType
from backend.naming import display_name


class DisplayNameTests(TestCase):
    def test_prefers_model_label(self):
        task = TaskRecord(
            task_type=TaskType.WEB_COMPARE,
            result={"summary": {"label": "两个爬虫练习站点对比"}},
        )
        self.assertEqual(display_name(task), "网页对比 · 两个爬虫练习站点对比")

    def test_ignores_hostname_label_and_uses_source_title(self):
        task = TaskRecord(
            task_type=TaskType.WEB_COMPARE,
            result={
                "summary": {
                    "label": "books.toscrape.com vs quotes.toscrape.com",
                    "sources": [
                        {"title": "All products | Books to Scrape - Sandbox"},
                        {"title": "Quotes to Scrape"},
                    ],
                }
            },
        )
        self.assertEqual(display_name(task), "网页对比 · All products vs Quotes to Scrape")

    def test_filters_missing_pages(self):
        task = TaskRecord(
            task_type=TaskType.WEB_COMPARE,
            result={
                "summary": {
                    "sources": [
                        {"title": "Page Not Found - Flask Documentation"},
                        {"title": "Django 文档 | Django"},
                    ]
                }
            },
        )
        self.assertEqual(display_name(task), "网页对比 · Django 文档")

    def test_compare_without_signal_has_no_url(self):
        task = TaskRecord(
            task_type=TaskType.WEB_COMPARE,
            params={"urls": ["https://books.toscrape.com", "https://quotes.toscrape.com"]},
            result={"summary": {"sources": []}},
        )
        self.assertEqual(display_name(task), "网页对比")

    def test_digest_uses_clean_page_title(self):
        task = TaskRecord(
            task_type=TaskType.WEB_DIGEST,
            result={"summary": {"title": "All products | Books to Scrape - Sandbox"}},
        )
        self.assertEqual(display_name(task), "网页资料整理 · All products")
