# ssh_sftp_mcp

`ssh_sftp_mcp` 是一个本地 stdio MCP 服务器，用于持久化 SSH shell 会话和 SFTP 文件传输。

## 为什么独立实现

公开的 SSH/SFTP MCP 生态中已有多个项目，包括 `mcp-ssh-tool`、`openssh-mcp`、`ssh-mcp` 和 `mcp-sftp-orchestrator`。它们是有价值的参考，但本服务器因以下特定需求而独立实现：

- 每个 IP 注册一组明文凭据
- 为已注册 IP 打开多个持久化会话
- 会话 ID 返回给 AI agent，并要求模型在回复中带出 `session_id`
- 交互式发送输入到会话，并支持游标增量读取输出
- 支持一次 MCP 调用并发向多个会话发送输入
- 支持临时连接并单次阻塞执行命令（不推荐作为常规方式）
- 远端断连时显式标记 disconnected 状态
- 按 IP 进行 SFTP 上传/下载
- 支持宿主机、Docker 和 Kubernetes 三种命令模式

## 依赖

```bash
pip install netmiko "asyncssh>=2.24,<3"
```

需要 Python 3.10+。交互式 SSH 会话继续使用 Netmiko / Paramiko；文件传输使用独立的 AsyncSSH 连接，不占用交互式 shell。

## 运行

默认启动 MCP stdio 门面。门面会在第一次工具调用时连接本地服务；如果服务未运行，会自动启动独立服务进程：

```powershell
python -m ssh_sftp_mcp
```

也可以先单独启动服务进程，再启动 MCP 门面：

```powershell
python -m ssh_sftp_mcp --service
```

服务默认监听 `127.0.0.1:8765`。可通过环境变量调整：

| 环境变量 | 说明 |
|----------|------|
| `SSH_SFTP_MCP_SERVICE_HOST` | 服务监听地址，默认 `127.0.0.1` |
| `SSH_SFTP_MCP_SERVICE_PORT` | 服务监听端口，默认 `8765` |
| `SSH_SFTP_MCP_SERVICE_START` | MCP 门面是否自动启动服务，`0` / `false` / `off` 表示禁用 |
| `SSH_SFTP_MCP_PYTHON` | 自动启动服务时使用的 Python 可执行文件，默认当前解释器 |

MCP stdio 进程只保留协议门面和工具目录；SSH/SFTP 会话、注册表访问和热加载业务逻辑运行在独立服务进程中。服务进程崩溃时，MCP 连接不会因此断开；下一次工具调用会返回服务不可用错误，并在允许自动启动时尝试重新拉起服务。

## MCP 客户端配置

### Claude Code

在项目根目录 `.mcp.json` 中配置：

```json
{
  "mcpServers": {
    "ssh-sftp-mcp": {
      "command": "python",
      "args": ["-m", "ssh_sftp_mcp"],
      "cwd": "H:\\Documents\\AgentAbility"
    }
  }
}
```

或通过 `claude mcp add` 命令添加：

```bash
claude mcp add ssh-sftp-mcp -- python -m ssh_sftp_mcp
```

### OpenCode

OpenCode 官方配置文件是 `opencode.json` / `opencode.jsonc`。MCP 服务器应配置在顶层 `mcp` 字段下。

#### 方式一：项目级 `opencode.json`（推荐）

在项目根目录创建或编辑 `opencode.json`：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "ssh-sftp-mcp": {
      "type": "local",
      "command": ["python", "-m", "ssh_sftp_mcp"],
      "cwd": "H:\\Documents\\AgentAbility",
      "enabled": true
    }
  }
}
```

`cwd` 必须指向包含 `ssh_sftp_mcp` 包目录的父目录。当前本地路径下应使用 `H:\Documents\AgentAbility`。

#### 方式二：全局用户配置

编辑 `~/.config/opencode/opencode.json`：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "ssh-sftp-mcp": {
      "type": "local",
      "command": ["python", "-m", "ssh_sftp_mcp"],
      "cwd": "H:\\Documents\\AgentAbility",
      "enabled": true
    }
  }
}
```

在 Windows PowerShell 中，`~` 通常对应 `$env:USERPROFILE`，即 `$env:USERPROFILE\.config\opencode\opencode.json`。

### 其他 MCP 客户端

任何支持 stdio 传输的 MCP 客户端均可使用，按以下格式注册：

```json
{
  "command": "python",
  "args": ["-m", "ssh_sftp_mcp"],
  "cwd": "<AgentAbility 目录的绝对路径>"
}
```

## 一键安装脚本

### Windows (PowerShell)

```powershell
.\setup.ps1
```

或从网络直接运行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\setup.ps1
```

脚本会自动完成：
- 检查 Python 环境
- 安装 netmiko 依赖
- 创建状态存储目录
- 生成 `.mcp.json`（Claude Code）和 `opencode.json`（OpenCode）配置文件

### Linux / macOS (Bash)

```bash
chmod +x setup.sh
./setup.sh
```

或一行命令：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/<your-repo>/main/setup.sh)
```

脚本会自动完成：
- 检查 Python 3 环境
- 创建虚拟环境（可选）并安装 netmiko
- 创建状态存储目录
- 生成 MCP 配置文件
- 同时写入 Claude Code（`.mcp.json`）和 OpenCode（`opencode.json`）配置

## 工具列表

| 工具 | 说明 |
|------|------|
| `ssh_register(ip, port, username, password)` | 注册 SSH 服务器凭据 |
| `ssh_update(old_ip, ip, port, username, password)` | 修改已注册 SSH 服务器凭据，支持改 IP |
| `ssh_delete(ip)` | 删除已注册 SSH 服务器并关闭该 IP 的现有会话 |
| `ssh_connect(ip)` | 为已注册 IP 打开持久化交互式会话，返回 `session_id`；模型对用户回复时必须带上该 `session_id` |
| `ssh_send_input(session_id, input, mode?, container?, namespace?, pod?, shell?)` | 向持久化交互式会话发送输入，立即返回 `session_id` 和 `command_id` |
| `ssh_read_output(session_id, command_id?, cursor?, limit?)` | 从持久化交互式会话按游标读取输出；模型展示结果时必须带上 `session_id` |
| `ssh_send_many(items)` | 一次调用并发向多个持久化会话发送输入，items 内每项使用 `ssh_send_input` 参数形状 |
| `ssh_temp_exec(ip, port, username, password, command, mode?, container?, namespace?, pod?, shell?)` | 临时连接、执行单条命令、阻塞读取 stdout/stderr 后关闭；这是单次指令执行，不推荐作为常规方式 |
| `ssh_list_sessions(ip)` | 列出某 IP 的所有会话（含断连的） |
| `ssh_close(ip, session_id?)` | 关闭所有或指定会话 |
| `ssh_status(session_id, command_id?)` | 查询会话或命令状态 |
| `sftp_upload(ip, local_path, remote_path, session_id?, workers?, block_size?, verify?)` | SFTP 上传文件 |
| `sftp_download(ip, remote_path, local_path, session_id?, workers?, block_size?, verify?)` | SFTP 下载文件 |
| `ssh_list_servers()` | 列出已注册服务器（不含密码） |
| `service_start()` | 启动或确认本地 SSH/SFTP 后端服务进程 |
| `service_version()` | 返回当前后端服务热加载版本信息 |

## 注册表同步

`servers.json` 会在每次读取、连接、列出、上传或下载前检查文件更新时间。外部手动编辑 JSON 后，无需重启 MCP 服务器，下一次工具调用会自动加载新内容。

如果外部 JSON 暂时写坏，服务器会记录错误日志并保留内存中的上一次有效注册表，避免异常退出或清空可用配置。

## 单文件并发传输与完整性

上传和下载均使用一个 AsyncSSH 连接、一个 SFTP 文件句柄和多个在途请求。每个请求显式传入 offset，默认 `workers=32`、`block_size=131072`（128 KiB）。workers 范围 1–128，block_size 范围 4096–131072；库还会遵守服务器声明的 SFTP 请求上限。workers 表示请求并发度，不是 SSH 连接数量。

上传仅创建一次独占 `.part-<uuid>` 文件；下载也先写本地临时文件。所有分块完成且整文件 SHA-256 匹配后，才改名发布。短读会继续读取未完成部分，提前 EOF、短写、哈希不匹配都会返回 MCP `isError=true`。失败不覆盖已有目标；断线时无法清理的远端 staging 文件可能保留。

`verify` 不能关闭完整性校验：

- `auto`（默认）：依次尝试远端 sha256sum、BusyBox、OpenSSL、Python 3；不可用时自动完整回读，通过客户端 SHA-256 验证。
- `readback`：直接完整回读，适用于没有 shell/哈希工具的 SFTP-only 服务器。上传会增加一次完整下载流量；下载会再读取源文件一遍。无需给服务器安装 JAR 或其他工具。

返回结果保留 `bytes`、`method`、路径等字段，并增加 `sha256`、`remote_sha256`、`verified`、`verification`、`workers`、`block_size`、`transfer_seconds`、`verify_seconds` 和 `total_seconds`。总耗时包含建连、检查、关闭与发布，不能简单等同于前两项之和。若未提供会话且未复用现有会话，`session_id` 为 null。

提供 `session_id` 时会核对其服务器归属。传输实现由独立 ServiceHost 加载，既有交互式会话状态仍留在 SessionManager。部署本次 facade/schema 变更需启动新进程；之后修改 `transfer.py` 可在下一次调用热加载。

## SFTP 子系统回退

只有 SFTP 子系统无法启动时才尝试同一用户的 shell 流式传输。普通权限、分块读写和校验错误直接失败，不绕过到另一条写入路径。shell 模式仍执行临时文件、SHA-256 校验和改名流程，返回 `method: "shell"`、`workers: 1`，不宣称并发提速。

远端不支持 POSIX 原子覆盖时，SFTP 标准 rename 仅能发布到不存在的目标；不会先删除旧目标再改名。

## 传输验收

测试计划见 [实施计划](docs/async-transfer-plan.md)，完整实测报告见本文末尾（另存 [独立报告](docs/async-transfer-report.md)）。从包的父目录运行：

```powershell
python -B -m unittest ssh_sftp_mcp.tests.test_smoke ssh_sftp_mcp.tests.test_transfer
```

独立终端启动后端 `python -m ssh_sftp_mcp --service`，再设置相同的 `SSH_SFTP_MCP_SERVICE_PORT` 和 `SSH_SFTP_MCP_SERVICE_START=0`，运行客户端：

```powershell
python -B -m ssh_sftp_mcp.tests.transfer_bench --server nas --phase async --size-mib 128 --rounds 3 --out nas.json
```

客户端另起真实 stdio MCP 进程，完成 initialize / tools/list / tools/call；凭据仅从 NAS_SERVER_* / ABYSS_SERVER_* 环境变量读取。测试应使用独立状态目录与端口。

## 日志

服务器会向 stderr 打印详细日志，同时写入状态目录：

```text
H:\Documents\AgentAbility\.ssh_sftp_mcp\ssh_sftp_mcp.log
```

可通过环境变量 `SSH_SFTP_MCP_LOG_LEVEL` 调整级别，例如 `DEBUG`、`INFO`、`WARNING`。

## 长命令与输出读取

推荐使用持久化交互式会话：`ssh_connect` 打开会话并返回 `session_id`，模型应在对用户回复中输出这个 `session_id`，后续使用 `ssh_send_input` 发送输入，再用 `ssh_read_output` 配合返回的 `command_id` 和游标（cursor）增量读取输出。使用 `ssh_status` 检查命令是否仍在运行或已完成。

`ssh_send_many` 用于在一个 MCP 调用中并发分发多条输入：

```json
{
  "items": [
    {"session_id": "session-a", "input": "pwd"},
    {"session_id": "session-b", "input": "whoami", "mode": "host"}
  ]
}
```

返回结构会保持 `items` 顺序，每项包含 `index` 和 `ok`。某一项失败时只标记该项 `ok: false` 和 `reason`，不会阻断其他会话的命令分发。

`ssh_temp_exec` 会临时建立 SSH 连接、执行一条命令、阻塞读取 stdout/stderr，然后关闭连接。该方式不会留下可复用会话，不推荐用于连续操作；连续排障、运维和文件处理应优先使用持久化交互式会话。

对于常规 `host`、`docker`、`k8s` 模式，持久会话采用 Netmiko 风格的命令边界：直接发送命令行，等待远端 shell prompt 返回后标记完成。该模式不再注入 POSIX marker/wrapper，因此 `cd`、`export` 等会保留在同一个交互 shell 中；代价是持久会话无法可靠返回 `exit_code`，需要退出码时使用 `ssh_temp_exec`。

同一个持久 shell 一次只能有一个可追踪命令处于运行状态；需要向 REPL、数据库 CLI 或其他交互式程序继续输入时应使用 `mode=raw`。例如先用 `host` 发送 `psql ...` 进入 PostgreSQL 提示符后，后续 SQL、`\c other_db`、`\dt` 等 psql 指令都应通过 `mode=raw` 发送；服务器只保证这些字符完整写入 SSH channel，不判断 SQL 是否执行完成。是否完成应通过 `ssh_read_output` 的游标增量读取和远端提示符判断。

`ssh_read_output` 的 `cursor` 是输出流位置。返回的 `next_cursor` 可直接用于下一次读取；即使单个 SSH recv 块大于 `limit`，也会按 `limit` 分段返回，避免大输出一次性堆积到调用方。

## Docker 与 Kubernetes 模式

Docker 模式将命令包裹为：

```sh
docker exec -i <container> <shell> -lc <command>
```

Kubernetes 模式将命令包裹为：

```sh
kubectl exec -n <namespace> <pod> [-c <container>] -- <shell> -lc <command>
```

MCP 服务器不假定 agent 在容器内部还是外部。命令从 SSH 目标主机执行，因此 Docker 或 Kubernetes 的访问取决于远端用户的权限和已安装的工具。

## 凭据存储

已注册服务器以明文 JSON 保存在进程工作目录下：

```text
H:\Documents\AgentAbility\.ssh_sftp_mcp\servers.json
```

设置环境变量 `SSH_SFTP_MCP_HOME` 可更改此状态目录。

这符合明文存储的明确需求。请勿将此 MCP 服务器暴露给不受信任的客户端。

## AsyncSSH 单文件并发传输与完整性验收报告

日期：2026-09-20。当前实现已完成 AsyncSSH 单文件并发上传/下载、强制 SHA-256 校验、无远端哈希工具时的完整回读、临时文件校验后发布。

### 结论与性能对比

**本次真实链路未观察到稳定的整体提速。** 原实现的 Paramiko `put/get` 本身已有流水线/预取，不能把旧版本等同于“每个分块都等 ACK 的串行传输”。新版提供了明确可控的有界并发和更完整的校验语义；实际链路带宽已经限制吞吐，校验还会增加时间。

以下为每组 3 轮的 MCP 调用耗时中位数；正数表示新版更快，负数表示变慢。提速率 = 旧耗时 / 新耗时 − 1。

| 服务器 | 文件大小 | 方向 | 原 Paramiko | 新 AsyncSSH，含校验 | 提速率 |
|---|---|---|---:|---:|---:|
| NAS | 128 MiB + 137 B | 上传 | 13.930 s | 14.566 s | -4.4% |
| NAS | 128 MiB + 137 B | 下载 | 13.267 s | 14.678 s | -9.6% |
| ABYSS | 16 MiB + 137 B | 上传 | 2.911 s | 3.067 s | -5.1% |
| ABYSS | 16 MiB + 137 B | 下载 | 40.265 s | 39.735 s | +1.3% |

- NAS 网口 `/sys/class/net/eth0/speed` 实测为 100 Mbps。新版纯上传约 11.39 秒（约 11.24 MiB/s），已接近百兆链路可用吞吐；一次上传校验约 1.95 秒。不能靠增加并发突破该网口带宽。
- ABYSS 下载实测约 0.40 MiB/s；16 MiB 三轮新版下载只有约 1.3% 改善，不应宣传成明显提速。新版首次上传 7.53 秒，后续约 3 秒，原始数据完整保留，未删掉慢样本。
- 旧版 MCP 调用不做整文件哈希，新版调用包含校验。报告比较用户实际等待时间；两版均另做独立远端 SHA-256 和本地 SHA-256 验收，外部验收不算进上表调用耗时。不能把该对比解释为“同等内置校验条件”的纯算法对比。

### 大文件验收

NAS：两版各 3 轮 128 MiB + 137 B，均完成完整往返和独立哈希检查。

ABYSS：旧版第一轮 128 MiB + 137 B 上传 20.822 秒、下载 318.598 秒，哈希一致。因公网下载时间较长，随后主动停止第二轮大文件基线，保留完整第一轮，并改用 16 MiB 三轮做重复对比。原始 128 MiB JSON 保留该主动中止产生的错误字段及 sampling_note，不能将其算作完整三轮或宣称旧版失败。

ABYSS 新版 128 MiB + 137 B：上传 22.602 秒、下载 318.291 秒，源文件、远端文件和下载文件 SHA-256 全部一致。该大文件组只有各一轮有效样本，仅用于大文件可用性和参考耗时，不计算有统计意义的提速结论。

### 验收方式与范围

- 基线源码：Git `c4056aa760c36e131e0283ad12247b7046c0c8ee`，实现修改前保存的 Python 源码快照。
- 手动以独立进程运行 `python -B -m ssh_sftp_mcp --service`，分别监听测试端口 18765 / 18766，使用独立状态目录；没有重启用户原有实例。
- 另开 benchmark 客户端进程；客户端再启动真实 stdio MCP 门面进程，经 JSON-RPC initialize、notifications/initialized、tools/list、tools/call 调用上传/下载，并非直接调用传输类代替 MCP 验收。
- 参数：workers=32、block_size=131072、verify=auto。每组使用不可压缩随机数据，包含非整块尾部，避免重复块掩盖错位。
- 两台远端均使用本次独占的 `/tmp/ssh_sftp_mcp_bench_<uuid>`，位于 tmpfs；这不是机械磁盘写入性能测试。原始环境数据见 [test-environment.json](docs/test-environment.json)。
- 两台服务器都额外完成 verify=readback 的 4 MiB 文件、空文件、覆盖旧远端和旧本地文件测试。常规哈希与回读路径分别由结果中的 verification 字段证明。
- 本地 MCP 测试：**50 项通过**，包含有界并发在途请求、显式 offset、乱序模拟、短读补齐、提前 EOF、同长度损坏、失败保留旧目标、shell 二进制回读及 Python/OpenSSL 哈希回退。shell-only 路径为本地模拟验收，两台真实机器走 SFTP。
- PMUD2 安装后的测试：**14 项通过**，包含原有 7 项应用测试与新增 7 项真实本地 SFTP 测试。大文件回归使用 17 MiB + 137 B、两个 SSH 连接。

### 参考实现的问题及修复

实际文件为 `E:\devs\PMUD2\sftp_tool.py`，原先给出的 `sftp\_tool.py` 不存在。修改前备份为 `sftp_tool.py.bak-20260920`；补丁采用无上下文格式，兼容原文件 CRLF 换行，应用命令为 `git apply --unidiff-zero --ignore-whitespace <补丁路径>`；完整补丁见 [pmud2-integrity.patch](docs/pmud2-integrity.patch)。

已确认的问题：

1. 先把最终文件扩展到目标大小，而 verify 默认关闭；大小一致无法证明分块完整写入。
2. 开启校验时哈希不一致仅返回 verify_passed=False；调用它的 pmud 服务仍可包装成 OK。
3. 直接截断/写入最终文件，失败会破坏已有文件。旧 CLI 下载错误处理还会删除最终目标。
4. 并行任务首次失败后没有显式取消并等待其他任务收敛；下载的本地短写也没有检查返回长度。

修复后：强制 SHA-256；保留旧 API 的 verify/hash_algorithm 参数以兼容调用者，但 verify=False 也不会关闭完整性检查。上传和下载均先写独占 staging，等待所有分块，完整回读验证（无需远端哈希工具），匹配后原子发布；错误哈希直接抛错，旧目标保留，失败任务取消并等待。请求全部使用显式 offset，短读补齐，短写失败。

**故障复现证据：** 本地启动真实 AsyncSSH SFTP 服务，故意丢弃首个写入分块但向客户端返回成功确认。旧代码的实际 upload_async(..., verify=False) 返回成功，最终大小正确但内容出现空洞；新代码检测到哈希不一致，拒绝发布，旧目标内容保持不变。该模拟无需 JAR。

这证明旧实现会漏检并发布不完整文件；并不证明历史故障中的服务器为什么漏写。静态检查中，旧版每个上传 worker 各自持有独立 r+b 句柄，没有发现可直接证明的共享 seek 游标竞态。

### 没有哈希工具的 Linux 如何校验

| 条件 | 实现方式 | 代价与保证 |
|---|---|---|
| 有 sha256sum / BusyBox / OpenSSL / Python 3 | 远端计算 SHA-256，与客户端摘要比较 | 不回传整文件；消耗远端顺序读与 CPU |
| 无上述工具，但有 SFTP 读权限 | 完整回读远端文件，在客户端按 offset 顺序计算 SHA-256 | 上传额外一次完整下载；下载再读一次源文件；无需安装依赖 |
| 没有 SFTP，但有可用 shell | cat 流式传输及回读校验 | 顺序降级，workers=1，不宣称并发提速 |
| 仅写权限，且无法执行哈希/回读 | 返回校验失败，不发布成功结果 | 文件大小或少量抽样无法替代完整内容校验 |

默认 auto 自动选择前两种；readback 可强制回读。回读不是“让客户端只计算本地源文件哈希”，而是重新读取实际远端字节。读写过程需保持源文件稳定；代码检查大小、mtime 和最终摘要，不能代替应用层文件快照。

并发写入使用同一个文件句柄时必须明确传入 offset，AsyncSSH 官方文档也明确要求这一点；库按服务器限制拆分请求。[AsyncSSH API](https://asyncssh.readthedocs.io/en/latest/api.html)

### 原始数据

- [NAS 基线](docs/bench-baseline-nas.json)、[NAS 新版](docs/bench-async-nas.json)
- [ABYSS 16 MiB 基线](docs/bench-baseline-abyss.json)、[ABYSS 16 MiB 新版](docs/bench-async-abyss.json)
- [ABYSS 128 MiB 基线，含主动中止说明](docs/bench-baseline-abyss-128mib.json)、[ABYSS 128 MiB 新版](docs/bench-async-abyss-128mib.json)

每份结果包含逐轮耗时、三个独立哈希、大小验证、内部传输/校验耗时和清理状态。测试脚本为 `tests/transfer_bench.py`；phase 只决定参数和标签，基线必须连接到旧源码后端，不能把新后端改名为 baseline 代替基线。

验收日志摘要：[validation.txt](docs/validation.txt)。所有已完成远端测试目录清理成功；主动中止基线的远端目录也由独立连接清理。测试进程与本地大文件在结束后清理，仅保留报告、日志、补丁和原文件备份。
