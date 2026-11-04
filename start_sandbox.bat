@echo off
echo 正在启动 BoxPilot 沙箱环境...
docker run --security-opt seccomp=unconfined --rm -it -p 127.0.0.1:8080:8080 ghcr.io/agent-infra/sandbox:1.11.0
pause
