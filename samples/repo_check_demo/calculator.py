"""离线演示仓库的计算模块，仅依赖 Python 标准库。"""


def add(left: int, right: int) -> int:
    return left + right


def divide(left: float, right: float) -> float:
    if right == 0:
        raise ValueError("right operand must not be zero")
    return left / right
