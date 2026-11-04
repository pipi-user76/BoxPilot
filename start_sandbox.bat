@echo off
REM 文件作用：启动固定版本的 AIO Sandbox，并仅将 8080 端口绑定到本机回环地址。
echo 正在启动 BoxPilot 沙箱环境...
docker run --security-opt seccomp=unconfined --rm -it -p 127.0.0.1:8080:8080 ghcr.io/agent-infra/sandbox:1.11.0
pause
