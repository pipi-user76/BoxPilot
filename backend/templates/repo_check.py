"""文件职责：可信代码仓库体检模板。

模板限定 HTTPS 且主机在白名单内的仓库，只执行预设的 Python 或 Node 测试命令。
克隆失败属于任务执行失败；测试命令返回非零属于有效检查结果，会写入报告但不使任务失败。
"""

from __future__ import annotations

import json
import re
import shlex
import time
from pathlib import Path

from backend.config import settings
from backend.sandbox_client import SandboxAdapter
from backend.templates.base import StepDefinition, StepExecutionError, StepOutcome, TaskContext
from backend.validation import ValidationFailure, validate_git_url


# 把 git 的英文报错归并成“发生了什么 + 可以怎么办”，原始输出保留在技术详情里。
_CLONE_ERROR_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("could not resolve host", "failed to connect", "connection refused",
         "connection timed out", "operation timed out", "network is unreachable"),
        "REPO_UNREACHABLE",
        "无法连接代码托管网站。请检查本机网络或代理设置后再试；需要现场演示时，可以先用离线示例 demo://python。",
    ),
    (
        ("repository not found", "not found"),
        "REPO_NOT_FOUND",
        "仓库不存在，或者当前没有访问权限。请确认地址拼写；私有仓库需要换成公开地址后再试。",
    ),
    (
        ("authentication failed", "could not read username", "terminal prompts disabled"),
        "REPO_AUTH_REQUIRED",
        "该仓库需要登录后才能访问。当前版本只支持公开仓库，请换成公开地址。",
    ),
    (
        ("gnutls", "tls connection", "ssl", "certificate", "openssl"),
        "REPO_TLS_FAILED",
        "无法与代码托管网站建立安全连接，可能是网络代理或证书拦截。请检查代理设置后再试；演示时可使用离线示例 demo://python。",
    ),
    (
        ("early eof", "rpc failed", "remote end hung up", "unexpected disconnect"),
        "REPO_TRANSFER_FAILED",
        "代码下载中途断开。网络不稳定时已自动重试；可以稍后再试，或改用体积更小的仓库。",
    ),
)


def classify_clone_failure(raw_message: str) -> tuple[str, str]:
    """根据 git 原始输出返回错误码和面向用户的说明。"""
    text = (raw_message or "").lower()
    for needles, code, message in _CLONE_ERROR_RULES:
        if any(needle in text for needle in needles):
            return code, message
    return "CLONE_FAILED", "代码下载失败，已自动重试仍未成功。请稍后再试，或改用离线示例 demo://python 演示流程。"


# 测试命令因缺依赖直接退出时，结论应是“跑不起来”，不能算“测试未通过”。
_DEPENDENCY_ERROR_MARKERS = ("modulenotfounderror", "no module named", "importerror")


# 在沙箱内解析仓库声明：依赖清单和 requires-python，统一输出一行 JSON。
_REPO_INSPECTOR = r"""import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
as_lines = len(sys.argv) > 2 and sys.argv[2] == "--lines"
requires_python = ""
dependencies = []
source = ""

requirements = root / "requirements.txt"
pyproject = root / "pyproject.toml"
if requirements.is_file():
    source = "requirements.txt"
    for line in requirements.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("-"):
            dependencies.append(line)
elif pyproject.is_file():
    source = "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
    if match:
        requires_python = match.group(1)
    match = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.S)
    if match:
        dependencies = re.findall(r'"([^"]+)"', match.group(1))

if as_lines:
    print("\n".join(dependencies))
else:
    print(json.dumps({
        "requires_python": requires_python,
        "source": source,
        "dependencies": dependencies,
    }))
"""


# 沙箱内安装依赖：先建独立虚拟环境，再装依赖；每一步都写 install-result.json。
# 退出码约定：0 成功，10 仓库没有声明依赖，11 版本不满足，12 解析失败，
# 13 网络失败，14 超时，15 其他失败。脚本被强杀时文件里仍留有 running 状态。
_INSTALL_SCRIPT = r"""#!/bin/sh
set -u
python_spec="$1"
repo_dir="$2"

result_file=install-result.json
log_file=install.log
: > "$log_file"
printf '%s\n' '{"status":"running"}' > "$result_file"

fail() {
    code="$1"
    message="$2"
    printf '{"status":"error","code":%s,"message":"%s"}\n' "$code" "$message" > "$result_file"
    printf '%s\n' "$message" >> "$log_file"
    exit "$code"
}

if ! command -v uv >/dev/null 2>&1; then
    fail 15 "uv is not available in the sandbox"
fi

if ! uv venv -q --python "$python_spec" .venv >>"$log_file" 2>&1; then
    fail 11 "unable to create a virtual environment with Python $python_spec"
fi

if ! python read_repo.py "$repo_dir" --lines > deps.txt 2>>"$log_file"; then
    fail 12 "unable to read dependency declarations"
fi

if [ ! -s deps.txt ]; then
    printf '%s\n' '{"status":"no_deps"}' > "$result_file"
    exit 10
fi

if ! uv pip install -q --python .venv/bin/python -r deps.txt >>"$log_file" 2>&1; then
    if grep -Eqi 'could not find a version|no matching distribution' "$log_file"; then
        fail 11 "no compatible distribution for the selected Python version"
    fi
    if grep -Eqi 'ssl|connection|timed out|network|temporary failure' "$log_file"; then
        fail 13 "network error while downloading dependencies"
    fi
    fail 15 "dependency installation failed"
fi

version=$(.venv/bin/python -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])' 2>/dev/null || echo unknown)
printf '{"status":"ok","python":"%s"}\n' "$version" > "$result_file"
exit 0
"""


# 解释器优先级：优先高版本，但只使用沙箱里已经存在的，绝不触发下载。
_PYTHON_PREFERENCE = ("3.13", "3.12", "3.11", "3.10")


def _version_tuple(value: str) -> tuple[int, ...]:
    """把 3.12.1 这类版本号转成可比较的整数元组。"""
    parts: list[int] = []
    for item in (value or "").split("."):
        digits = "".join(ch for ch in item if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _satisfies(version: str, spec: str) -> bool:
    """判断版本是否满足 requires-python；无法识别的约束按“不拦截”处理。"""
    current = _version_tuple(version)
    if not current or not (spec or "").strip():
        return True
    for clause in spec.split(","):
        clause = clause.strip()
        if not clause:
            continue
        match = re.match(r"(>=|<=|==|~=|!=|>|<)\s*(\d+(?:\.\d+)*)\*?", clause)
        if not match:
            continue
        operator, raw = match.group(1), match.group(2)
        target = _version_tuple(raw)
        width = max(len(current), len(target))
        left = current + (0,) * (width - len(current))
        right = target + (0,) * (width - len(target))
        if operator == ">=" and left < right:
            return False
        if operator == ">" and left <= right:
            return False
        if operator == "<=" and left > right:
            return False
        if operator == "<" and left >= right:
            return False
        if operator == "!=" and left == right:
            return False
        if operator == "==" and current[: len(target)] != target:
            return False
        if operator == "~=":
            bound = target[:1] if len(target) == 1 else target[:2]
            upper = (bound[0] + 1,) if len(bound) == 1 else (bound[0], bound[1] + 1)
            padded_upper = upper + (0,) * (width - len(upper))
            if left < right or left[: len(upper)] >= padded_upper[: len(upper)]:
                return False
    return True



def missing_dependency(output: str) -> bool:
    """判断测试输出是否属于依赖缺失导致的导入失败。"""
    text = (output or "").lower()
    return any(marker in text for marker in _DEPENDENCY_ERROR_MARKERS)


class RepoCheckTemplate:
    """克隆可信仓库、识别技术栈并运行固定测试命令。"""

    name = "REPO_CHECK"

    def __init__(
        self,
        sandbox: SandboxAdapter,
        trusted_hosts: set[str] | None = None,
        timeout_s: int | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.trusted_hosts = trusted_hosts or settings.trusted_git_hosts()
        self.timeout_s = timeout_s or settings.REPO_TEST_TIMEOUT_SECONDS
        self.steps = (
            StepDefinition("校验仓库地址", self._validate_repository),
            # 两次克隆各 60 秒，再留出改名和收尾时间。
            StepDefinition("获取仓库代码", self._clone_repository, timeout_seconds=180),
            StepDefinition("识别项目类型", self._detect_toolchain),
            # 只装依赖、不跑测试；超时上限比安装本身略宽，留给上传脚本和收尾。
            StepDefinition(
                "准备测试环境",
                self._prepare_environment,
                timeout_seconds=settings.REPO_INSTALL_TIMEOUT_SECONDS + 30,
            ),
            StepDefinition("运行项目测试", self._run_repository_tests, timeout_seconds=self.timeout_s + 30),
            StepDefinition("生成检查报告", self._write_check_report),
        )

    def _validate_repository(self, context: TaskContext) -> StepOutcome:
        """只接受白名单主机上的公开 HTTPS 仓库。"""
        repo_url = str(context.params.get("repo_url", "")).strip()
        if repo_url == "demo://python" and settings.ENABLE_DEMO_TASKS:
            context.state.update(repo_url=repo_url, demo_repo=True)
            return StepOutcome(output="已选择内置的离线 Python 示例仓库")
        try:
            validate_git_url(repo_url, self.trusted_hosts)
        except ValidationFailure as exc:
            raise StepExecutionError("VALIDATION_ERROR", str(exc)) from exc
        context.state.update(repo_url=repo_url, demo_repo=False)
        return StepOutcome(output=f"可信仓库地址校验通过：{repo_url}")

    def _clone_repository(self, context: TaskContext) -> StepOutcome:
        """浅克隆仓库；网络失败自动重试一次，仍失败时给出可操作的说明。

        每次尝试都克隆到独立目录并在成功后改名，超时被杀掉的半成品不会阻塞下一次尝试。
        """
        repo_url = context.state["repo_url"]
        if context.state.get("demo_repo"):
            return self._prepare_demo_repository(context)

        attempts = max(1, settings.REPO_CLONE_ATTEMPTS)
        attempt_timeout = max(15, min(self.timeout_s, settings.REPO_CLONE_TIMEOUT_SECONDS))
        last_message = ""
        for attempt in range(1, attempts + 1):
            target = f"repo-{attempt}"
            command = (
                "GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/false "
                f"git clone --depth 1 --single-branch --no-tags -- {shlex.quote(repo_url)} {target} "
                f"&& mv {target} repo"
            )
            result = self.sandbox.exec_shell(command, task_id=context.task_id, timeout_s=attempt_timeout)
            if result["success"]:
                context.state["clone_attempts"] = attempt
                context.state["clone_output"] = result["data"]["output"]
                suffix = "" if attempt == 1 else f"（第 {attempt} 次尝试）"
                return StepOutcome(output=f"仓库代码获取成功{suffix}")
            last_message = result["error"]["message"] or "git clone failed"
            if attempt < attempts:
                time.sleep(2)

        code, message = classify_clone_failure(last_message)
        # 失败也要留下可下载的说明，前端能直接看到原因、尝试次数和原始输出。
        failure_report = context.artifact_store.write_text(
            context.task_id,
            "clone-failure.json",
            json.dumps(
                {
                    "repo_url": repo_url,
                    "error_code": code,
                    "error_message": message,
                    "attempts": attempts,
                    "last_output": last_message[-2000:],
                    "suggestion": "检查网络或代理设置后点击重试；现场演示可改用 demo://python。",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        raise StepExecutionError(code, message, detail=last_message[-4000:], artifacts=(failure_report,))

    def _prepare_demo_repository(self, context: TaskContext) -> StepOutcome:
        """上传 samples/repo_check_demo，并在沙箱内创建 Git 提交。"""
        sample_root = Path(__file__).resolve().parents[2] / "samples" / "repo_check_demo"
        if not sample_root.is_dir():
            raise StepExecutionError("FILE_NOT_FOUND", f"demo repository not found: {sample_root}")
        for source in sorted(path for path in sample_root.rglob("*") if path.is_file()):
            relative = source.relative_to(sample_root).as_posix()
            result = self.sandbox.upload_file(context.task_id, source, f"repo/{relative}")
            if not result["success"]:
                error = result["error"]
                raise StepExecutionError(error["code"], error["message"])
        command = (
            "git init -q repo && git -C repo config user.email boxpilot@example.local && "
            "git -C repo config user.name BoxPilot && git -C repo add . && "
            "git -C repo commit -q -m 'offline demo repository'"
        )
        result = self.sandbox.exec_shell(command, task_id=context.task_id, timeout_s=self.timeout_s)
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])
        context.state["clone_output"] = result["data"]["output"]
        context.state["clone_attempts"] = 0
        return StepOutcome(output="内置示例仓库准备完成")

    def _detect_toolchain(self, context: TaskContext) -> StepOutcome:
        """优先识别 Python，再识别 Node；其他仓库只生成未运行报告。"""
        command = (
            "if test -f repo/boxpilot-unittest; then printf python-unittest; "
            "elif test -f repo/pyproject.toml || test -f repo/setup.py || "
            "test -f repo/pytest.ini || test -d repo/tests; then printf python; "
            "elif test -f repo/package.json; then printf node; else printf unknown; fi"
        )
        result = self.sandbox.exec_shell(command, task_id=context.task_id, timeout_s=30)
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])
        toolchain = result["data"]["output"].strip().lower()
        if toolchain not in {"python", "python-unittest", "node", "unknown"}:
            toolchain = "unknown"
        context.state["toolchain"] = toolchain
        labels = {
            "python": "Python 测试项目",
            "python-unittest": "Python 标准库测试项目",
            "node": "Node.js 测试项目",
            "unknown": "未识别到支持的项目类型",
        }
        return StepOutcome(output=f"项目类型：{labels[toolchain]}")

    def _has_python_module(self, task_id: str, module: str) -> bool:
        """探测沙箱解释器是否装了某个包，避免把“缺工具”误判成“测试失败”。"""
        result = self.sandbox.exec_shell(
            f'python -c "import {module}" >/dev/null 2>&1; echo $?',
            task_id=task_id,
            timeout_s=20,
            check_exit=False,
        )
        if not result["success"]:
            return False
        return result["data"]["output"].strip().endswith("0")

    def _probe_pythons(self, task_id: str) -> list[str]:
        """探测沙箱已有的解释器；uv python find 不会触发下载。"""
        command = (
            'for version in 3.13 3.12 3.11 3.10; do '
            'if uv python find "$version" >/dev/null 2>&1; then printf "%s\\n" "$version"; fi; '
            "done"
        )
        result = self.sandbox.exec_shell(command, task_id=task_id, timeout_s=30, check_exit=False)
        found: list[str] = []
        if result["success"]:
            for line in (result["data"].get("output") or "").splitlines():
                value = line.strip()
                if value and value not in found:
                    found.append(value)
        if found:
            return found
        # uv 不可用时退回系统 python，至少能报告一个可用版本。
        fallback = self.sandbox.exec_shell(
            "python -c \"import sys;print('%d.%d'%sys.version_info[:2])\"",
            task_id=task_id,
            timeout_s=15,
            check_exit=False,
        )
        if fallback["success"]:
            value = (fallback["data"].get("output") or "").strip()
            if value:
                return [value]
        return []

    def _inspect_repository(self, context: TaskContext) -> dict:
        """读取仓库声明的依赖清单和 requires-python。"""
        uploaded = self.sandbox.write_text_file(context.task_id, "read_repo.py", _REPO_INSPECTOR)
        if not uploaded["success"]:
            return {}
        result = self.sandbox.exec_shell(
            "python read_repo.py repo",
            task_id=context.task_id,
            timeout_s=30,
            check_exit=False,
        )
        if not result["success"]:
            return {}
        for line in reversed((result["data"].get("output") or "").strip().splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return {}

    @staticmethod
    def _choose_python(available: list[str], requires: str) -> tuple[str, bool, str]:
        """选一个满足仓库要求的已有解释器；不满足时明确报告，而不是硬跑。"""
        if not available:
            return "", False, "沙箱里没有可用的 Python 解释器"
        if not requires:
            return available[0], True, ""
        for version in available:
            if _satisfies(version, requires):
                return version, True, ""
        joined = "、".join(available)
        return available[0], False, f"仓库要求 Python {requires}，沙箱可用 {joined}"

    def _install_python(self, task_id: str, requires: str) -> tuple[bool, str]:
        """按仓库要求补装解释器；uv 默认走 GitHub，国内网络需要镜像。"""
        match = re.search(r"(\d+\.\d+)", requires or "")
        if not match:
            return False, ""
        spec = match.group(1)
        parts: list[str] = []
        if settings.UV_PYTHON_INSTALL_MIRROR:
            parts.append(
                f"UV_PYTHON_INSTALL_MIRROR={shlex.quote(settings.UV_PYTHON_INSTALL_MIRROR)}"
            )
        parts.append(f"uv python install {spec}")
        result = self.sandbox.exec_shell(
            " ".join(parts),
            task_id=task_id,
            timeout_s=settings.REPO_PYTHON_INSTALL_TIMEOUT_SECONDS,
            check_exit=False,
        )
        if not result["success"]:
            return False, ""
        data = result["data"]
        output = (data.get("output") or "").strip()
        return data.get("exit_code") == 0, output

    def _prepare_environment(self, context: TaskContext) -> StepOutcome:
        """识别版本要求，按需用 uv 建虚拟环境并安装依赖。"""
        if context.state["toolchain"] == "node":
            context.state["install_state"] = "not_requested"
            return StepOutcome(output="Node 项目在测试前用 npm 安装依赖")

        info = self._inspect_repository(context)
        requires = str(info.get("requires_python") or "").strip()
        available = self._probe_pythons(context.task_id)
        chosen, ready, note = self._choose_python(available, requires)
        installed_python = False
        if not ready and requires and context.params.get("install_deps", True):
            # 镜像里没有所需版本时补装一次；国内网络下这一步通常只要几秒。
            installed_python, _ = self._install_python(context.task_id, requires)
            if installed_python:
                available = self._probe_pythons(context.task_id)
                chosen, ready, note = self._choose_python(available, requires)
        relaxed = False
        if not ready and available and context.params.get("allow_version_mismatch", True):
            # 沙箱没有 3.13 时允许降一档试跑，但必须在报告里说明，避免把结果当成严格结论。
            relaxed = True
            ready = True
            chosen = available[0]
            note = (
                f"仓库要求 Python {requires}，沙箱最高只有 {chosen}，本次按 {chosen} 尝试运行；"
                "结果仅供参考，严格验收需要在 3.13 环境重跑。"
            )
        context.state.update(
            requires_python=requires,
            available_pythons=available,
            python_spec=chosen,
            python_ready=ready,
            python_note=note,
            python_installed=installed_python,
            version_relaxed=relaxed,
            declared_dependencies=len(info.get("dependencies") or []),
            dependency_source=info.get("source", ""),
        )
        if not ready:
            context.state.update(
                install_state="version_mismatch",
                test_status="error",
                test_command=None,
                test_exit_code=None,
                test_output=note,
                test_note=f"{note}。请改用允许 3.12 及以下版本的仓库，或先在镜像里预装所需解释器。",
            )
            return StepOutcome(output="没有满足仓库要求的 Python 解释器")

        if not context.params.get("install_deps", True):
            context.state["install_state"] = "not_requested"
            return StepOutcome(output=f"将使用 Python {chosen} 运行测试；未开启依赖安装")

        return self._install_with_uv(context, requires)

    def _install_with_uv(self, context: TaskContext, requires: str) -> StepOutcome:
        """上传安装脚本，在任务目录内建 .venv 并安装依赖。"""
        task_id = context.task_id
        for name, content in (
            ("read_repo.py", _REPO_INSPECTOR),
            ("sandbox_install.sh", _INSTALL_SCRIPT),
        ):
            uploaded = self.sandbox.write_text_file(task_id, name, content)
            if not uploaded["success"]:
                raise StepExecutionError(uploaded["error"]["code"], uploaded["error"]["message"])

        command = (
            f"{self._uv_env()}sh sandbox_install.sh "
            f"{shlex.quote(context.state['python_spec'])} repo"
        )

        context.state["install_attempted"] = True
        result = self.sandbox.exec_shell(
            command,
            task_id=task_id,
            timeout_s=settings.REPO_INSTALL_TIMEOUT_SECONDS,
            check_exit=False,
        )
        payload = self._read_install_result(context)
        log_text = self._read_install_log(context)
        context.state["install_output"] = log_text
        context.state["install_exit_code"] = payload.get("code")

        if not result["success"]:
            code = result["error"]["code"]
            note = (
                "依赖安装超时，测试没有执行"
                if code == "TIMEOUT"
                else f"依赖安装失败：{result['error']['message']}"
            )
            return self._mark_install_failed(context, note)

        status = payload.get("status")
        if status == "ok":
            context.state.update(install_state="installed", python_used=payload.get("python", ""))
            tooling = self._install_test_tooling(context)
            return StepOutcome(
                output=(
                    f"依赖已安装到任务虚拟环境（Python {payload.get('python') or context.state['python_spec']}）"
                    + (f"；{tooling}" if tooling else "")
                )
            )
        if status == "no_deps":
            context.state["install_state"] = "no_deps"
            tooling = self._install_test_tooling(context)
            return StepOutcome(
                output="仓库未声明可安装的依赖，跳过安装" + (f"；{tooling}" if tooling else "")
            )
        if status is None:
            exit_code = result["data"].get("exit_code")
            if exit_code == 0:
                context.state["install_state"] = "installed"
                return StepOutcome(output="依赖安装完成，但未读取到结果文件")
            payload = {"code": exit_code, "message": f"exit code {exit_code}"}

        note = {
            11: "沙箱没有与仓库要求匹配的依赖版本，通常是 Python 版本偏低",
            12: "无法解析仓库的依赖声明",
            13: "下载依赖时网络中断",
            14: "依赖安装超时",
        }.get(payload.get("code"), "依赖安装失败")
        if requires:
            note = f"{note}（仓库要求 Python {requires}）"
        return self._mark_install_failed(context, note, str(payload.get("message") or ""))

    def _uv_env(self) -> str:
        """uv 命令的源配置前缀；包源和解释器源必须分别注入。"""
        parts: list[str] = []
        if settings.UV_INDEX_URL:
            parts.append(f"UV_INDEX_URL={shlex.quote(settings.UV_INDEX_URL)}")
        if settings.UV_PYTHON_INSTALL_MIRROR:
            parts.append(
                f"UV_PYTHON_INSTALL_MIRROR={shlex.quote(settings.UV_PYTHON_INSTALL_MIRROR)}"
            )
        return (" ".join(parts) + " ") if parts else ""

    def _install_test_tooling(self, context: TaskContext) -> str:
        """装测试工具并尽量把项目本身装成可导入的包；失败只记录，不阻断任务。"""
        toolchain = context.state.get("toolchain")
        if toolchain not in {"python", "python-unittest"}:
            return ""
        notes: list[str] = []
        if toolchain == "python":
            probe = self.sandbox.exec_shell(
                ".venv/bin/python -m pytest --version >/dev/null 2>&1",
                task_id=context.task_id,
                check_exit=False,
            )
            exit_code = (probe.get("data") or {}).get("exit_code") if probe["success"] else None
            if exit_code != 0:
                installed = self.sandbox.exec_shell(
                    f"{self._uv_env()}uv pip install -q --python .venv/bin/python pytest",
                    task_id=context.task_id,
                    timeout_s=90,
                    check_exit=False,
                )
                ok = installed["success"] and (installed.get("data") or {}).get("exit_code") == 0
                notes.append("已补装 pytest" if ok else "pytest 未装好，测试可能跑不起来")
            else:
                notes.append("pytest 已可用")
        editable = self.sandbox.exec_shell(
            f"{self._uv_env()}if [ -f repo/pyproject.toml ] || [ -f repo/setup.py ]; then "
            "uv pip install -q --python .venv/bin/python --no-deps -e repo; fi",
            task_id=context.task_id,
            timeout_s=180,
            check_exit=False,
        )
        if editable["success"] and (editable.get("data") or {}).get("exit_code") == 0:
            notes.append("项目已安装为可编辑包")
        return "，".join(notes)

    def _mark_install_failed(self, context: TaskContext, note: str, detail: str = "") -> StepOutcome:
        """安装失败时直接给测试步骤留下结论，避免再去跑必然失败的测试。"""
        context.state.update(
            install_state="failed",
            test_status="error",
            test_command=None,
            test_exit_code=context.state.get("install_exit_code"),
            test_output=detail or context.state.get("install_output") or note,
            test_note=note,
        )
        return StepOutcome(output=note)

    def _read_install_result(self, context: TaskContext) -> dict:
        result = self.sandbox.read_text_file(context.task_id, "install-result.json")
        if not result["success"]:
            return {}
        try:
            payload = json.loads(result["data"]["content"])
        except (json.JSONDecodeError, KeyError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _read_install_log(self, context: TaskContext) -> str:
        result = self.sandbox.read_text_file(context.task_id, "install.log")
        if not result["success"]:
            return ""
        return str(result["data"].get("content") or "")[-4000:]

    def _install_node_dependencies(self, context: TaskContext) -> StepOutcome | None:
        """Node 项目沿用 npm 安装；失败时同样留下可读结论。"""
        command = (
            "cd repo && if test -f package-lock.json; then npm ci --no-audit --no-fund; "
            "else npm install --no-audit --no-fund; fi"
        )
        context.state["install_attempted"] = True
        result = self.sandbox.exec_shell(
            command,
            task_id=context.task_id,
            timeout_s=settings.REPO_INSTALL_TIMEOUT_SECONDS,
            check_exit=False,
        )
        if not result["success"]:
            return self._mark_install_failed(context, f"依赖安装失败：{result['error']['message']}")
        exit_code = result["data"].get("exit_code")
        context.state["install_output"] = result["data"].get("output") or ""
        context.state["install_exit_code"] = exit_code
        if exit_code != 0:
            return self._mark_install_failed(context, "npm 安装依赖失败")
        context.state["install_state"] = "installed"
        return None

    def _run_repository_tests(self, context: TaskContext) -> StepOutcome:
        """执行固定命令；非零退出码保存为检查结果，而不是抛出系统异常。"""
        if context.state.get("test_status") == "error":
            # 版本或依赖在准备阶段已判定无法运行，这里不再重复执行。
            return StepOutcome(output=context.state.get("test_note") or "测试无法运行")

        toolchain = context.state["toolchain"]
        if toolchain == "node" and context.params.get("install_deps", True):
            install_failure = self._install_node_dependencies(context)
            if install_failure is not None:
                return install_failure

        venv_ready = context.state.get("install_state") in {"installed", "no_deps"}
        if toolchain == "python":
            if venv_ready:
                test_command = "cd repo && ../.venv/bin/python -m pytest -q"
            else:
                if not self._has_python_module(context.task_id, "pytest"):
                    # 沙箱镜像没有 pytest，但仓库可能是标准库 unittest 风格；先降级，再决定是否“未运行”。
                    return self._run_unittest_fallback(context)
                test_command = "cd repo && python -m pytest -q"
        elif toolchain == "python-unittest":
            runner = "../.venv/bin/python" if venv_ready else "python"
            test_command = f"cd repo && {runner} -m unittest discover -q"
        elif toolchain == "node":
            test_command = "cd repo && npm test -- --runInBand"
        else:
            test_command = None

        if test_command is None:
            context.state.update(
                test_command=None,
                test_exit_code=None,
                test_output="No supported test runner detected.",
                test_status="not_run",
                test_note="未检测到可运行的测试命令",
            )
            return StepOutcome(output="未检测到可运行的测试命令")

        result = self.sandbox.exec_shell(
            test_command,
            task_id=context.task_id,
            timeout_s=self.timeout_s,
            check_exit=False,
        )
        if not result["success"]:
            error = result["error"]
            context.state.update(
                test_command=test_command,
                test_exit_code=None,
                test_output=error["message"],
                test_status="error",
            )
            return StepOutcome(output=f"测试执行异常：{error['message']}")

        exit_code = result["data"].get("exit_code")
        test_output = result["data"].get("output") or "(no test output)"
        if exit_code != 0 and "no module named 'pytest'" in test_output.lower():
            # 依赖装上了但测试工具没就位，不能算仓库测试失败。
            context.state.update(
                test_command=test_command,
                test_exit_code=exit_code,
                test_output=test_output,
                test_status="error",
                test_note="任务虚拟环境里没有 pytest，测试工具没有装好；可以重试一次，或在镜像里预装 pytest",
            )
            return StepOutcome(output="任务虚拟环境里没有 pytest，测试工具没有装好")
        if exit_code != 0 and missing_dependency(test_output):
            context.state.update(
                test_command=test_command,
                test_exit_code=exit_code,
                test_output=test_output,
                test_status="error",
                test_note="沙箱环境未安装该仓库的依赖，测试无法运行",
            )
            return StepOutcome(output="沙箱环境未安装该仓库的依赖，测试无法运行")
        context.state.update(
            test_command=test_command,
            test_exit_code=exit_code,
            test_output=test_output,
            test_status="passed" if exit_code == 0 else "failed",
        )
        status_label = "测试通过" if context.state["test_status"] == "passed" else "测试未通过"
        return StepOutcome(output=f"{status_label}，退出码：{exit_code}")

    def _run_unittest_fallback(self, context: TaskContext) -> StepOutcome:
        """沙箱没有 pytest 时改用标准库 unittest，并区分“没跑”与“没通过”。"""
        command = "cd repo && python -m unittest discover -q"
        result = self.sandbox.exec_shell(
            command,
            task_id=context.task_id,
            timeout_s=self.timeout_s,
            check_exit=False,
        )
        output = result["data"].get("output", "") if result["success"] else result["error"]["message"]
        exit_code = result["data"].get("exit_code") if result["success"] else None
        ran_nothing = "ran 0 tests" in (output or "").lower() or not output
        if ran_nothing:
            note = "沙箱未安装 pytest，仓库里也没有 unittest 用例，本次没有执行测试"
            context.state.update(
                test_command=None,
                test_exit_code=None,
                test_output=output or "No tests were executed.",
                test_status="not_run",
                test_note=note,
            )
            return StepOutcome(output=note)
        if exit_code != 0 and missing_dependency(output):
            context.state.update(
                test_command=command,
                test_exit_code=exit_code,
                test_output=output,
                test_status="error",
                test_note="沙箱环境未安装该仓库的依赖，测试无法运行",
            )
            return StepOutcome(output="沙箱环境未安装该仓库的依赖，测试无法运行")
        context.state.update(
            test_command=command,
            test_exit_code=exit_code,
            test_output=output,
            test_status="passed" if exit_code == 0 else "failed",
            test_note="沙箱未安装 pytest，已改用标准库 unittest 运行",
        )
        status_label = "测试通过" if exit_code == 0 else "测试未通过"
        return StepOutcome(output=f"{status_label}（unittest 回退），退出码：{exit_code}")

    def _sandbox_python_version(self, task_id: str) -> str:
        """记录沙箱解释器版本，便于解释“依赖或版本不匹配”。"""
        result = self.sandbox.exec_shell(
            "python --version 2>&1",
            task_id=task_id,
            timeout_s=15,
            check_exit=False,
        )
        if not result["success"]:
            return ""
        return (result["data"].get("output") or "").strip().replace("Python ", "")

    def _write_check_report(self, context: TaskContext) -> StepOutcome:
        """同时产出机器可读的 JSON 和面向人的 Markdown 报告。"""
        python_version = (
            context.state.get("python_used")
            or context.state.get("python_spec")
            or self._sandbox_python_version(context.task_id)
        )
        status_messages = {
            "passed": "仓库测试通过",
            "failed": "仓库测试未通过",
            "not_run": context.state.get("test_note") or "未检测到可运行的测试",
            "error": context.state.get("test_note") or "测试执行异常",
        }
        message = status_messages[context.state["test_status"]]
        test_output = context.state["test_output"]
        clone_output = context.state.get("clone_output", "")

        # JSON 只保留结论字段；原始输出放在日志文件里，避免预览时满屏堆栈。
        report = {
            "repo_url": context.state["repo_url"],
            "toolchain": context.state["toolchain"],
            "test_command": context.state["test_command"],
            "exit_code": context.state["test_exit_code"],
            "test_status": context.state["test_status"],
            "test_note": context.state.get("test_note", ""),
            "clone_attempts": context.state.get("clone_attempts", 1),
            "requires_python": context.state.get("requires_python", ""),
            "python": python_version,
            "version_relaxed": bool(context.state.get("version_relaxed")),
            "dependencies": context.state.get("install_state", "not_requested"),
        }
        report_text = json.dumps(report, ensure_ascii=False, indent=2)

        guidance = {
            "passed": "仓库自带的测试可以在沙箱里跑通。",
            "failed": "测试确实执行了，但有用例失败，请看 test.log 里的失败用例。",
            "not_run": "仓库里没有找到能直接运行的测试，本次没有得出通过或失败的结论。",
            "error": "测试命令没有真正跑起来。通常是沙箱环境缺少仓库声明的依赖，或 Python 版本低于仓库要求；这不是“测试失败”，也不代表仓库代码有问题。",
        }[context.state["test_status"]]
        toolchain_labels = {
            "python": "Python 项目（pytest）",
            "python-unittest": "Python 项目（标准库测试）",
            "node": "Node 项目",
        }
        clone_attempts = context.state.get("clone_attempts", 1)
        raw_sources = ["- 测试输出：`test.log`"]
        if clone_output and clone_attempts > 1:
            raw_sources.append("- 克隆输出：`clone.log`")
        install_state = context.state.get("install_state", "not_requested")
        if install_state == "failed":
            raw_sources.append("- 依赖安装输出：`install.log`")
        install_labels = {
            "not_requested": "未开启（默认只做只读检查）",
            "installed": "已用 uv 安装到任务虚拟环境",
            "no_deps": "仓库未声明依赖（已跳过）",
            "version_mismatch": "沙箱没有满足要求的 Python 解释器",
            "failed": "安装失败（详见 install.log）",
            "skipped": "仓库未声明可安装的依赖（已跳过）",
        }
        requires_python = context.state.get("requires_python") or "未声明"
        markdown = "\n".join(
            [
                "# 代码仓库体检报告",
                "",
                f"- 仓库：{context.state['repo_url']}",
                f"- 项目类型：{toolchain_labels.get(context.state['toolchain'], context.state['toolchain'])}",
                f"- 结论：{message}",
                f"- 测试命令：{context.state['test_command'] or '未执行'}",
                f"- 退出码：{context.state['test_exit_code'] if context.state['test_exit_code'] is not None else '无'}",
                f"- 克隆尝试：{'内置示例，无需克隆' if not clone_attempts else str(clone_attempts) + ' 次'}",
                f"- 仓库要求 Python：{requires_python}",
                f"- 实际使用 Python：{python_version or '未知'}",
                *(
                    ["- 解释器准备：按仓库要求现场安装"]
                    if context.state.get("python_installed")
                    else []
                ),                *(
                    ["- 版本说明：沙箱缺少满足要求的解释器，本次降级试跑，结果仅供参考"]
                    if context.state.get("version_relaxed")
                    else []
                ),
                f"- 依赖准备：{install_labels.get(install_state, install_state)}",
                "",
                "## 说明",
                "",
                guidance,
                "",
                "## 原始输出",
                "",
                *raw_sources,
                "",
            ]
        )

        artifacts = [
            context.artifact_store.write_text(context.task_id, "check-report.md", markdown),
            context.artifact_store.write_text(context.task_id, "check-report.json", report_text),
            context.artifact_store.write_text(context.task_id, "test.log", test_output + "\n"),
        ]
        if clone_output and clone_attempts > 1:
            artifacts.append(
                context.artifact_store.write_text(context.task_id, "clone.log", clone_output + "\n")
            )
        if install_state == "failed":
            artifacts.append(
                context.artifact_store.write_text(
                    context.task_id,
                    "install.log",
                    (context.state.get("install_output") or "") + "\n",
                )
            )
        return StepOutcome(
            output=f"检查完成：{message}",
            artifacts=artifacts,
            result={
                "kind": "repo_check",
                "message": message,
                "test_status": context.state["test_status"],
                "toolchain": context.state["toolchain"],
                "exit_code": context.state["test_exit_code"],
            },
        )
