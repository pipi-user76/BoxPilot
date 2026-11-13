"""文件职责：集中定义后端运行配置，并支持从 .env 覆盖默认值。

沙箱工作目录来自实际环境 /home/gem；产物目录使用项目根目录 artifacts。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "BoxPilot API"
    PROJECT_VERSION: str = "0.1.0"
    SANDBOX_URL: str = "http://127.0.0.1:8080"
    SANDBOX_WORK_ROOT: str = "/home/gem/boxpilot"
    SANDBOX_TIMEOUT_SECONDS: int = 120
    SANDBOX_HEALTH_TIMEOUT_SECONDS: int = 5
    ARTIFACT_ROOT: Path = Path("artifacts")
    MAX_ARTIFACT_BYTES: int = 20 * 1024 * 1024
    MAX_CSV_UPLOAD_BYTES: int = 5 * 1024 * 1024
    TRUSTED_GIT_HOSTS: str = "github.com,gitee.com,gitlab.com"
    REPO_CLONE_ATTEMPTS: int = 2
    REPO_CLONE_TIMEOUT_SECONDS: int = 60
    REPO_TEST_TIMEOUT_SECONDS: int = 180
    REPO_INSTALL_TIMEOUT_SECONDS: int = 240
    REPO_PYTHON_INSTALL_TIMEOUT_SECONDS: int = 180
    # uv 的两类源要分开配置：UV_INDEX_URL 管 Python 包，UV_PYTHON_INSTALL_MIRROR 管解释器下载。
    # 默认走 npmmirror，GitHub 直连过慢时也能在几秒内装好 3.13；需要官方源时显式设为空字符串。
    # 默认用阿里云镜像，避免沙箱直连 PyPI 超时；需要官方源时显式设为空字符串。
    UV_INDEX_URL: str = "https://mirrors.aliyun.com/pypi/simple/"
    UV_PYTHON_INSTALL_MIRROR: str = (
        "https://registry.npmmirror.com/-/binary/python-build-standalone"
    )
    WEB_ACCEPTANCE_TIMEOUT_SECONDS: int = 60
    # 步骤级与任务级兜底超时：即使沙箱请求卡死，调度器也不会无限期停在 RUNNING。
    STEP_TIMEOUT_SECONDS: int = 240
    TASK_TIMEOUT_SECONDS: int = 900
    ENABLE_DEMO_TASKS: bool = True
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Path = Path("logs/boxpilot.jsonl")
    TASK_PAGE_SIZE_DEFAULT: int = 20
    TASK_PAGE_SIZE_MAX: int = 100
    PREVIEW_TEXT_LIMIT: int = 200_000
    # 安全边界：同一任务参数在窗口内只允许创建一次；限流按来源 IP 计数，0 表示关闭。
    DEDUPE_WINDOW_SECONDS: int = 300
    RATE_LIMIT_PER_MINUTE: int = 20
    URL_MAX_LENGTH: int = 2048
    COMMAND_MAX_LENGTH: int = 8192

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def trusted_git_hosts(self) -> set[str]:
        """把逗号分隔的仓库主机配置转换为小写集合。"""
        return {host.strip().lower() for host in self.TRUSTED_GIT_HOSTS.split(",") if host.strip()}


settings = Settings()
