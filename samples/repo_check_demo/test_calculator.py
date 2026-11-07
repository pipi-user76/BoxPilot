"""离线演示仓库的标准库 unittest 测试。"""

import unittest

from calculator import add, divide


class CalculatorTests(unittest.TestCase):
    def test_add(self) -> None:
        self.assertEqual(add(2, 3), 5)

    def test_divide(self) -> None:
        self.assertEqual(divide(8, 2), 4)

    def test_divide_by_zero(self) -> None:
        with self.assertRaises(ValueError):
            divide(1, 0)


if __name__ == "__main__":
    unittest.main()
