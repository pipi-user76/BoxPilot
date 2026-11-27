# 从零部署到阿里云（新手版）

这份文档按“点哪里、输什么”写，假设你刚买好一台 Ubuntu 22.04 的轻量应用服务器，手上只有浏览器和 Windows 电脑。

## 零、购买页怎么选

- 实例：CPU 优化型，2 vCPU / 4 GiB / 60 GiB。
- 镜像：系统镜像 → Ubuntu → 22.04。
- 地域：离你近的即可，北京、杭州都行。
- 时长：先买 1 个月，自动续费可以不勾。
- 计费提醒：页面若写“无固定流量、峰值带宽 200 Mbps”，是按流量计费，演示够用；长期跑大量下载要留意账单。

买完后进入“轻量应用服务器 → 实例列表”，点进实例继续下面的步骤。

## 一、在阿里云控制台做三件事

1. 找到公网 IP
   控制台 → 轻量应用服务器 → 实例列表 → 点进实例，复制“公网 IP”。后面用 `<IP>` 表示这串数字。

2. 设置 root 密码
   实例详情 → 远程连接 / 重置密码 → 设置一个新密码，记下来。

3. 放行 80 端口
   实例详情 → 防火墙 → 添加规则：
   - 应用类型：HTTP
   - 端口：80
   - 授权对象：0.0.0.0/0

   22 端口默认已放行，不要放行 8000 和 8080。

## 二、从 Windows 连上服务器

打开“开始菜单 → Windows PowerShell”，输入（把 `<IP>` 换成你的公网 IP）：

```powershell
ssh root@<IP>
```

第一次会问 `Are you sure you want to continue connecting?`，输入 `yes` 回车；然后输入刚才设置的 root 密码（输入时屏幕不显示字符，是正常的）。

看到 `root@...:~#` 就说明已经登录到服务器。

## 三、把项目装到服务器

先看内存：

```bash
free -h
```

如果“Mem”那一行总量只有 2G 左右，先加 4G 交换空间（一次粘贴整段）：

```bash
fallocate -l 4G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
free -h
```

然后整段粘贴下面这段。它会下载代码、安装依赖、拉取约 13 GB 的沙箱镜像、注册开机自启服务：

```bash
apt-get update
apt-get install -y git
rm -rf /root/boxpilot-src
git clone https://github.com/pipi-user76/BoxPilot.git /root/boxpilot-src
cd /root/boxpilot-src
REPO_URL=https://github.com/pipi-user76/BoxPilot.git bash deploy/bootstrap.sh
```

最后一行会打印一段 `[boxpilot]` 日志，中途拉镜像可能要等 10–20 分钟。看到 `active (running)` 或 `status: ok` 就继续。

检查后端是否正常：

```bash
curl -s http://127.0.0.1:8000/health
```

期望返回里有 `"status":"ok"`、`"store":"sqlite"`、`"sandbox":{"success":true}`。

## 四、装一个 80 端口的入口

优先用 Caddy：

```bash
apt-get install -y caddy
cat >/etc/caddy/Caddyfile <<'EOF'
:80 {
	encode zstd gzip
	reverse_proxy 127.0.0.1:8000
}
EOF
systemctl enable --now caddy
systemctl reload caddy
curl -I http://127.0.0.1/
```

Ubuntu 22.04 的默认源里没有 `caddy` 包，装不上就改用系统自带的 nginx：

```bash
apt-get install -y nginx
cat >/etc/nginx/sites-available/boxpilot <<'EOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    client_max_body_size 20m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
ln -sf /etc/nginx/sites-available/boxpilot /etc/nginx/sites-enabled/boxpilot
rm -f /etc/nginx/sites-enabled/default
systemctl enable --now nginx
systemctl reload nginx
curl -I http://127.0.0.1/
```

`curl -I` 返回 `200` 或 `303` 就说明入口通了。

## 五、在浏览器里验收

1. 打开 `http://<IP>/ui/login.html`（`<IP>` 换成你的公网 IP）。
2. 第一次会显示“创建管理员”，填用户名和至少 8 位密码。
3. 登录后进入工作台，右上角“账号”里再建一个普通账号。
4. 在“网页资料整理”页点“创建任务”，地址填 `https://books.toscrape.com/`，提交。
5. 等任务变成“成功”，点整行查看详情，确认有 `article.md`、`items.csv`、`screenshot.png`。
6. 退出登录，Windows 上执行下面这条，确认返回 401：

```powershell
curl.exe -i http://<IP>/tasks
```

## 六、日常维护

看服务状态与日志：

```bash
systemctl status boxpilot --no-pager
journalctl -u boxpilot -n 100 --no-pager
tail -n 20 /var/lib/boxpilot/logs/boxpilot.jsonl
```

重启后端：

```bash
systemctl restart boxpilot
```

升级代码：

```bash
systemctl stop boxpilot
cd /opt/boxpilot
git pull
uv sync --locked
systemctl start boxpilot
```

备份（数据库和产物必须一起备份）：

```bash
sqlite3 /var/lib/boxpilot/data/boxpilot.db ".backup '/root/boxpilot-$(date +%F).db'"
rsync -a /var/lib/boxpilot/artifacts/ /root/boxpilot-artifacts-$(date +%F)/
```

## 七、常见问题

- `docker pull` 卡住或中断：重新执行一次初始化脚本即可，已下载的层会复用。
- 健康检查里沙箱 `success` 是 `false`：`docker ps` 看 `boxpilot-sandbox` 是否在运行，`docker logs --tail 50 boxpilot-sandbox` 看原因。
- 打开页面是 502：`systemctl status boxpilot`，多半是数据库目录权限或 `/etc/boxpilot.env` 路径写错。
- 页面还是旧版：浏览器强制刷新；服务端已对 `/ui/` 关闭缓存。
- 内存吃紧：确认 swap 生效，网页任务不要同时开多个。
