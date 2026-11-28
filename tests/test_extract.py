"""正文提取链的单元测试：trafilatura 优先、Readability 与文本降级。"""

from __future__ import annotations

import unittest

from backend.extract import extract_article

_HTML = """
<html><body>
<nav><a href="/a">首页</a><a href="/b">课程</a><a href="/c">论坛</a></nav>
<main><article>
<h1>HTML 标题教程</h1>
<p>HTML 标题由 h1 到 h6 六个层级组成，h1 表示页面主标题，h2 到 h6 表示逐级子标题。</p>
<p>标题应当按内容结构使用，不要为了字号跳过层级；屏幕阅读器也依赖标题层级来导航。</p>
<p>一个页面通常只保留一个 h1，其余小节使用 h2；需要强调样式时用 CSS，而不是随意换标签。</p>
<p>本节还介绍标题的默认样式、外边距以及如何用开发者工具检查标题层级是否正确。</p>
<p>搜索爬虫抓取时也常依赖标题判断页面主题，因此标题层级混乱会直接影响摘要质量与结构化抽取结果。</p>
<p>如果要验证标题层级，可以在浏览器控制台运行查询脚本，统计每个标题的文本、层级与在文档中的顺序。</p>
<p>教程示例覆盖六个层级的写法、常见错误、可访问性注意事项，以及如何用 CSS 调整字号而保持语义不变。</p>
</article></main>
<footer>版权所有 示例站点</footer>
</body></html>
"""


class ExtractArticleTests(unittest.TestCase):
    def test_trafilatura_removes_navigation(self) -> None:
        result = extract_article(_HTML, "https://example.com/headings")
        self.assertEqual(result["mode"], "trafilatura")
        self.assertIn("HTML 标题教程", result["markdown"])
        self.assertIn("h1 表示页面主标题", result["markdown"])
        self.assertNotIn("首页", result["markdown"])
        self.assertNotIn("版权所有", result["markdown"])

    def test_empty_html_falls_back_to_readability(self) -> None:
        result = extract_article("", "https://example.com", fallback_markdown="# 已提取正文")
        self.assertEqual(result["mode"], "readability")
        self.assertEqual(result["markdown"], "# 已提取正文")

    def test_empty_html_falls_back_to_text(self) -> None:
        result = extract_article("", "https://example.com", fallback_text="纯文本正文")
        self.assertEqual(result["mode"], "text")
        self.assertEqual(result["markdown"], "纯文本正文")

    def test_no_content_returns_none(self) -> None:
        result = extract_article("", "https://example.com")
        self.assertEqual(result["mode"], "none")
        self.assertEqual(result["markdown"], "")


if __name__ == "__main__":
    unittest.main()
