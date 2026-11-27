# BoxPilot 服务器部署

本文面向 Ubuntu 22.04/24.04。最低配置建议 2 核 4G、60 GB 磁盘；2 核 2G 的机器需要先加 4 GB swap，否则沙箱里的 Chromium 很容易把内存吃满。

第一次部署、不想看长文档的话，按 [deploy_quickstart.md](deploy_quickstart.md) 一步步粘贴即可。

部署形态是三个进程：

- AIO Sandbox 容器，只监听 `127.0.0.1:8080`。
- BoxPilot 后端，单进程单 Worker，监听 `127.0.0.1:8000`。
- Caddy 反向代理，对外提供 80/443 和 HTTPS。

## 一、一键初始化

```bash
sudo apt-get update && sudo apt-get install -y git
sudo REPO_URL=git@github.com:you/BoxPilot.git bash deploy/bootstrap.sh
```

脚本会创建 `boxpilot` 系统账号、把代码放到 `/opt/boxpilot`、安装 Docker 与 uv、拉取沙箱镜像、生成 `/etc/boxpilot.env`、注册并启动 `boxpilot.service`。可以重复执行，已有配置不会被覆盖。

首次拉取沙箱镜像约 13 GB，网络慢时要等一会儿。

## 二、手动步骤

如果不想用脚本，按顺序执行：

1. 准备目录与账号：

```bash
sudo useradd --system --create-home --shell /bin/bash boxpilot
sudo mkdir -p /opt/boxpilot /var/lib/boxpilot/{data,artifacts,logs}
sudo chown -R boxpilot:boxpilot /var/lib/boxpilot
```

2. 放代码并安装依赖：

```bash
sudo -u boxpilot git clone git@github.com:you/BoxPilot.git /opt/boxpilot
sudo -u boxpilot bash -lc 'cd /opt/boxpilot && uv python install 3.13 && uv sync --locked'
```

如果 GitHub 下载解释器超时，先设置：

```bash
export UV_PYTHON_INSTALL_MIRROR=https://registry.npmmirror.com/-/binary/python-build-standalone
export UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
```

3. 启动沙箱：

```bash
sudo docker run -d --name boxpilot-sandbox --restart unless-stopped \
  --shm-size=1g -p 127.0.0.1:8080:8080 \
  ghcr.io/agent-infra/sandbox:1.11.0
```

4. 写 `/etc/boxpilot.env`（权限 `640 root:boxpilot`）：

```text
DATABASE_PATH=/var/lib/boxpilot/data/boxpilot.db
ARTIFACT_ROOT=/var/lib/boxpilot/artifacts
LOG_FILE=/var/lib/boxpilot/logs/boxpilot.jsonl
SANDBOX_URL=http://127.0.0.1:8080
SESSION_SECRET=<openssl rand -hex 32 的输出>
SESSION_COOKIE_SECURE=true
AUTH_REQUIRED=true
UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
```

`DATABASE_PATH` 和 `ARTIFACT_ROOT` 必须写绝对路径。相对路径会跟着工作目录走，换个目录启动就会变成一套空库。

5. 安装服务：

```bash
sudo install -m 0644 deploy/boxpilot.service /etc/systemd/system/boxpilot.service
sudo systemctl daemon-reload
sudo systemctl enable --now boxpilot
```

6. 配反向代理：把 `deploy/Caddyfile` 复制到 `/etc/caddy/Caddyfile`，替换域名与 Basic Auth 哈希，然后 `sudo systemctl reload caddy`。Caddy 2.8 以下把 `basic_auth` 改写成 `basicauth`。

## 沙箱扩展依赖（可选）

基础四类任务不需要额外依赖。需要做 CSV 可视化、统计建模时，在服务器上执行：

```bash
cd /opt/boxpilot
docker cp scripts/sandbox_extras.sh boxpilot-sandbox:/tmp/sandbox_extras.sh
docker exec boxpilot-sandbox bash /tmp/sandbox_extras.sh
docker exec boxpilot-sandbox /home/gem/extras-venv/bin/python -c "import pandas, sklearn; print(pandas.__version__)"
```

脚本会创建 `/home/gem/extras-venv`，安装 `pandas`、`matplotlib`、`scikit-learn`，不影响镜像自带 Python。2 核 4G 的机器安装时内存会短时升高，建议在低峰执行。

## 三、验证清单

```bash
curl -s http://127.0.0.1:8000/health
```

预期 `status` 为 `ok`，`store` 为 `sqlite`，`sandbox.success` 为 `true`。

- 打开 `https://你的域名/ui/login.html`，首次进入应出现“创建管理员”。
- 登录后创建一个 `WEB_DIGEST` 任务，确认能出 `article.md`、`items.csv` 和截图。
- 退出登录，直接访问 `/tasks` 应返回 401。
- 创建第二个账号，确认它看不到第一个账号的任务。

## 四、备份与升级

数据只有两处，必须一起备份：

```bash
sudo sqlite3 /var/lib/boxpilot/data/boxpilot.db ".backup '/backup/boxpilot-$(date +%F).db'"
sudo rsync -a /var/lib/boxpilot/artifacts/ /backup/artifacts-$(date +%F)/
```

升级：

```bash
sudo systemctl stop boxpilot
sudo -u boxpilot git -C /opt/boxpilot pull
sudo -u boxpilot bash -lc 'cd /opt/boxpilot && uv sync --locked'
sudo systemctl start boxpilot
```

数据库迁移由启动时的 `init_db()` 按 `user_version` 自动执行；升级前先做上面的备份。

## 五、常见问题

- `/health` 里沙箱报 `SANDBOX_UNAVAILABLE`：`sudo docker ps` 看容器是否在运行，`sudo docker logs boxpilot-sandbox` 看 8080 是否被占用。
- 启动报 `unable to open database file`：`/var/lib/boxpilot/data` 不存在或不属于 `boxpilot`，补目录和权限即可。
- 页面还是旧版：后端对 `/ui/` 发了 `Cache-Control: no-store`，仍看到旧页面时先强制刷新；确认访问的是同一个端口。
- 仓库体检装不上依赖：检查服务器到 `mirrors.aliyun.com` 与 `registry.npmmirror.com` 的连通性，或把 `UV_INDEX_URL` 换成可达的镜像。
- 内存吃紧：给机器加 swap，或把沙箱的 `--shm-size` 降到 512m；不要同时跑多个浏览器任务。
