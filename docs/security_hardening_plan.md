# BoxPilot 安全边界改进方案

> 实施状态（2026-11-13）：四项改进已落地。命令构造见 `backend/command.py`；URL/SSRF 见 `backend/validation.py` 的 `validate_public_url`；幂等与限流见 `backend/dedupe.py`、`backend/routes_tasks.py`、`backend/main.py`；超时结构见 `backend/sandbox_client.py`。测试共 66 项，其中 12 项专门覆盖这些边界（`tests/test_security.py`）。


编制日期：2026-11-13
适用范围：任务提交、命令执行、网页访问、任务去重与超时处理
目标：在不改变四类模板现有行为的前提下，把“能跑”提升到“能对外演示、出问题能解释”。

## 1. 现状与差距

| 能力 | 当前实现 | 主要缺口 |
|---|---|---|
| 输入校验 | `backend/validation.py` 的 `validate_task_id`、`validate_filename`、`safe_relative_path`、`validate_http_url`、`validate_git_url` | 不检查解析后的 IP；不拦控制字符和超长输入；URL 未规范化 |
| 命令执行 | `backend/sandbox_client.py` 的 `exec_shell` 统一传字符串；仓库地址已用 `--` 终止选项 | 命令拼接分散在各模板；缺少统一的引号与白名单入口 |
| 网页访问 | 只校验 scheme 和 host 格式 | 可访问内网与云元数据地址；跳转后不再校验 |
| 重复提交 | 无 | 同一输入可无限重复创建任务 |
| 超时 | `_exec_raw` 把 `hard_timeout` 映射为 `TIMEOUT`；调度器有步骤与任务超时 | 丢失已产生的输出；未区分 OOM、SIGKILL 与超时；失败结构不统一 |
| 限流 | 无 | 公网部署时单 IP 可打满执行队列 |

## 2. 改进一：命令构造统一入口

### 2.1 原则

校验和转义是两层，不能互相替代。校验负责“只允许合法输入”，转义负责“即使含特殊字符也只当数据”。

### 2.2 设计

新增 `backend/command.py`：

```python
def quote_arg(value: str, *, max_length: int = 2048) -> str:
    """拒绝控制字符与超长输入，再交给 shlex.quote。"""

def build_command(*parts: str, terminator: bool = True) -> str:
    """固定模板 + 逐个转义；需要时在末尾追加 `--` 终止选项。"""
```

约束：

- 所有模板拼命令必须经过 `build_command`，禁止在 f-string 中直接插入用户输入。
- URL 只允许 RFC 3986 可见字符；文件名只允许 `[A-Za-z0-9._-]`；两者都设长度上限。
- 需要把用户值作为位置参数时，命令末尾加 `--`，避免 `-rf` 这类值被当成选项。
- 增加一条回归测试：扫描 `backend/templates/*.py`，出现 `f"...{context.params"` 或 `f"...{repo_url}"` 形式的命令拼接即失败。

### 2.3 验收

- 提交含 `;`、换行、`$(...)`、`-rf` 的仓库地址或 URL，命令只把它们当普通参数，任务给出明确校验错误或安全失败。
- 现有四类模板的命令输出与改造前一致。

## 3. 改进二：URL 与 SSRF 防护

### 3.1 校验顺序

1. 语法：只允许 `http`/`https`，端口只允许 80/443，拒绝 URL 内凭据、控制字符、超长输入。
2. 解析：`socket.getaddrinfo` 拿到全部 A/AAAA 记录。
3. 地址判定：用 `ipaddress` 逐个检查 `is_private`、`is_loopback`、`is_link_local`、`is_reserved`、`is_multicast`、`is_unspecified`，任一命中即拒绝。
4. 域名黑名单：`host.docker.internal`、`gateway.docker.internal`、`*.docker.internal`。
5. 打开页面后读取最终 URL，再执行一次第 1 至 4 步，防止跳转绕过。

### 3.2 接口

`backend/validation.py` 新增：

```python
def validate_public_url(url: str) -> str:
    """语法 + 解析 + 地址判定；失败抛 ValidationFailure。"""
```

错误码统一为 `URL_NOT_ALLOWED`，message 说明是协议、端口、内网地址还是解析失败，不回显解析出的具体 IP。

### 3.3 边界说明

应用层校验挡不住浏览器自己发起的重定向和子资源请求。真正的边界在沙箱网络出口：给沙箱容器加出站白名单或代理，只允许 80/443。应用层校验作为第一道门，网络策略作为兜底，两者都要有。

### 3.4 验收

- `http://127.0.0.1:8080`、`http://192.168.1.1`、`http://169.254.169.254`、`http://localtest.me` 均被拒绝。
- 正常公网页面不受影响；跳转到内网地址时任务失败并给出 `URL_NOT_ALLOWED`。

## 4. 改进三：幂等与限流

### 4.1 去重键

```text
dedupe_key = sha256(task_type + canonical_json(params))
```

规范化规则：键排序、统一小写 host、去掉 URL 末尾斜杠、忽略空白。不要用 MD5，不是为了防碰撞，而是避免“看起来一样、算出来不一样”。

### 4.2 行为

- 窗口默认 300 秒，由 `DEDUPE_WINDOW_SECONDS` 控制。
- 命中窗口且原任务处于 `PENDING`、`RUNNING`、`SUCCESS` 时，直接返回原任务，HTTP 200，响应加 `deduplicated: true`。
- 原任务为 `FAILED`、`TIMEOUT`、`CANCELLED` 时允许重新创建，避免用户改不了错。
- 存储层保证原子性：内存实现用同一把锁完成“查 + 建”；SQLite 给 `tasks.dedupe_key` 加索引，写入前在事务内查询。

### 4.3 限流

限流与幂等分开实现。按来源 IP 做令牌桶，默认 20 次/分钟，超限返回 429 并带 `Retry-After`。配置项 `RATE_LIMIT_PER_MINUTE`，演示环境可调高或关闭。

### 4.4 验收

- 连续提交同一 URL 两次，第二次返回同一任务 ID 且 `deduplicated=true`。
- 失败任务可立即重跑，不被去重拦截。
- 超过限流阈值后返回 429，等待窗口恢复后可用。

## 5. 改进四：超时与结构化失败

### 5.1 统一结果结构

所有步骤结果收敛为：

```json
{
  "ok": false,
  "code": "STEP_TIMEOUT",
  "message": "步骤执行超时，已停止等待",
  "detail": "原始输出最后 4 KB",
  "partial_output": "已产生的输出",
  "exit_code": 124,
  "signal": null,
  "duration_ms": 240000
}
```

调度器只认这个结构，模板不再各自拼错误文案。

### 5.2 判定顺序

1. 沙箱脚本启动即写 `result.json`，内容 `{"status": "running"}`，结束时覆盖为最终结果。
2. 后端先看退出码与状态：`hard_timeout` 或退出码 124 记为 `STEP_TIMEOUT`；137 或负值记为 `STEP_KILLED`，提示可能是内存不足。
3. 读不到 `result.json` 时不报“系统错误”，按退出码生成兜底结构，保留已产生的输出。
4. Python 层的守护线程无法强杀进程，真正终止依赖沙箱的 `hard_timeout`；文档中明确这一点，避免误解。

### 5.3 验收

- 人为让步骤卡住，任务在超时后落为 `TIMEOUT`，`partial_output` 非空（若有输出）。
- 退出码 137 时提示“可能内存不足”，而不是笼统的“命令失败”。
- 后续任务不被卡住的任务阻塞。

## 6. 实施顺序

| 阶段 | 内容 | 完成标准 |
|---|---|---|
| P0 | `command.py`、`validate_public_url`、失败结构统一 | 注入与内网地址用例全部拦截，现有测试不回归 |
| P0 | 超时兜底与退出码映射 | 超时、OOM、正常失败三种结果可区分 |
| P1 | 去重键与原子写入 | 重复提交返回原任务，并发下不产生双任务 |
| P1 | 限流中间件 | 超限返回 429，配置可调 |
| P2 | 沙箱出站白名单、跳转后复检、审计日志 | 公网部署时网络层有第二道边界 |

## 7. 测试清单

| 编号 | 场景 | 预期 |
|---|---|---|
| S01 | 仓库地址含 `; rm -rf /` | 校验失败或作为普通参数，不执行 |
| S02 | 仓库地址以 `-` 开头 | 被 `--` 终止，不当作选项 |
| S03 | 网页 URL 指向 127.0.0.1 | `URL_NOT_ALLOWED` |
| S04 | 网页 URL 指向 169.254.169.254 | `URL_NOT_ALLOWED` |
| S05 | 公网页面跳转到内网 | 任务失败并说明原因 |
| S06 | 相同参数 5 分钟内重复提交 | 返回原任务，`deduplicated=true` |
| S07 | 失败任务立即重试 | 允许创建新任务 |
| S08 | 单 IP 高频提交 | 429 与 `Retry-After` |
| S09 | 步骤卡死 | `TIMEOUT`，保留部分输出 |
| S10 | 进程被 OOM 杀死 | `STEP_KILLED`，提示内存不足 |

## 8. 明确不做的事

- 不引入 WAF 或第三方风控，当前规模用应用层校验加网络白名单足够。
- 不做多租户隔离；公网部署前先补登录，再谈按用户限流。
- 不追求“完全阻止 SSRF”。浏览器行为无法在应用层完全约束，最终依赖沙箱出站策略。
