#!/usr/bin/env bash
# 在沙箱容器内准备数据分析与可视化依赖。
#
# 独立虚拟环境，避免污染镜像自带 Python；任务模板需要时可显式使用
# /home/gem/extras-venv/bin/python。默认不随部署自动执行，见 INSTALL_SANDBOX_EXTRAS。

set -euo pipefail

VENV_DIR="${VENV_DIR:-/home/gem/extras-venv}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
INDEX_URL="${UV_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"

echo "[extras] 创建虚拟环境：${VENV_DIR}（Python ${PYTHON_VERSION}）"
uv venv --python "${PYTHON_VERSION}" "${VENV_DIR}"

echo "[extras] 安装 pandas / matplotlib / scikit-learn"
uv pip install --python "${VENV_DIR}/bin/python" \
  --index-url "${INDEX_URL}" \
  pandas matplotlib scikit-learn

echo "[extras] 完成：${VENV_DIR}/bin/python"
"${VENV_DIR}/bin/python" -c "import pandas, matplotlib, sklearn; print('pandas', pandas.__version__, 'sklearn', sklearn.__version__)"
