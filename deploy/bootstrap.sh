#!/usr/bin/env bash
# BoxPilot 服务器初始化脚本（Ubuntu 22.04/24.04）。
# 用法：sudo REPO_URL=git@github.com:you/BoxPilot.git bash deploy/bootstrap.sh
# 重复执行是安全的：已存在的目录、用户、容器和配置不会被覆盖。
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/boxpilot}"
DATA_DIR="${DATA_DIR:-/var/lib/boxpilot}"
SERVICE_USER="${SERVICE_USER:-boxpilot}"
SANDBOX_IMAGE="${SANDBOX_IMAGE:-ghcr.io/agent-infra/sandbox:1.11.0}"
SANDBOX_NAME="${SANDBOX_NAME:-boxpilot-sandbox}"
REPO_URL="${REPO_URL:-}"
UV_INDEX_URL="${UV_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
UV_PYTHON_INSTALL_MIRROR="${UV_PYTHON_INSTALL_MIRROR:-https://registry.npmmirror.com/-/binary/python-build-standalone}"

log() { printf '\n[boxpilot] %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 或 sudo 运行：sudo REPO_URL=... bash deploy/bootstrap.sh" >&2
  exit 1
fi

log "安装基础依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends ca-certificates curl git rsync openssl

log "创建服务账号与目录"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /bin/bash "$SERVICE_USER"
fi
mkdir -p "$APP_DIR" "$DATA_DIR/data" "$DATA_DIR/artifacts" "$DATA_DIR/logs"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$DATA_DIR"

if [ -n "$REPO_URL" ] && [ ! -d "$APP_DIR/.git" ]; then
  log "拉取代码到 $APP_DIR"
  rmdir "$APP_DIR" 2>/dev/null || true
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi
if [ ! -f "$APP_DIR/pyproject.toml" ]; then
  echo "未找到 $APP_DIR/pyproject.toml。请先把代码放到该目录，或用 REPO_URL 指定仓库。" >&2
  exit 1
fi
chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"

log "安装 Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

log "启动 AIO Sandbox 容器（镜像约 13 GB，首次需要几分钟）"
if ! docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1; then
  docker pull "$SANDBOX_IMAGE"
fi
if docker container inspect "$SANDBOX_NAME" >/dev/null 2>&1; then
  docker start "$SANDBOX_NAME" >/dev/null
else
  docker run -d \
    --name "$SANDBOX_NAME" \
    --restart unless-stopped \
    --shm-size=1g \
    -p 127.0.0.1:8080:8080 \
    "$SANDBOX_IMAGE"
fi

log "安装 uv"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  install -m 0755 /root/.local/bin/uv /usr/local/bin/uv
  install -m 0755 /root/.local/bin/uvx /usr/local/bin/uvx 2>/dev/null || true
fi

log "安装 Python 3.13 与项目依赖"
sudo -u "$SERVICE_USER" env \
  UV_INDEX_URL="$UV_INDEX_URL" \
  UV_PYTHON_INSTALL_MIRROR="$UV_PYTHON_INSTALL_MIRROR" \
  bash -lc "cd '$APP_DIR' && uv python install 3.13 && uv sync --locked"

log "写入 /etc/boxpilot.env"
if [ ! -f /etc/boxpilot.env ]; then
  SESSION_SECRET="$(openssl rand -hex 32)"
  cat >/etc/boxpilot.env <<EOF
DATABASE_PATH=$DATA_DIR/data/boxpilot.db
ARTIFACT_ROOT=$DATA_DIR/artifacts
LOG_FILE=$DATA_DIR/logs/boxpilot.jsonl
LOG_LEVEL=INFO
SANDBOX_URL=http://127.0.0.1:8080
SESSION_SECRET=$SESSION_SECRET
SESSION_COOKIE_SECURE=false
AUTH_REQUIRED=true
UV_INDEX_URL=$UV_INDEX_URL
UV_PYTHON_INSTALL_MIRROR=$UV_PYTHON_INSTALL_MIRROR
EOF
  chown root:"$SERVICE_USER" /etc/boxpilot.env
  chmod 640 /etc/boxpilot.env
else
  log "/etc/boxpilot.env 已存在，保留原配置"
fi

log "安装 systemd 服务"
install -m 0644 "$APP_DIR/deploy/boxpilot.service" /etc/systemd/system/boxpilot.service
systemctl daemon-reload
systemctl enable --now boxpilot

log "等待服务启动"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -sS "http://127.0.0.1:8000/health" || true

log "完成。下一步："
cat <<EOF
1. 打开 http://<服务器IP>:8000/ui/login.html 之前，先配置反向代理和 HTTPS。
2. Caddy 模板在 deploy/Caddyfile，把域名和 Basic Auth 哈希替换后放到 /etc/caddy/Caddyfile。
3. 首次访问页面会要求创建管理员账号。
4. 日志：journalctl -u boxpilot -f；应用日志：$DATA_DIR/logs/boxpilot.jsonl
EOF
