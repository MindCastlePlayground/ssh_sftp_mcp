# ssh_sftp_mcp

`ssh_sftp_mcp` 是一个本地 stdio MCP 服务器，用于持久化 SSH shell 会话和 SFTP 文件传输。

## 为什么独立实现

公开的 SSH/SFTP MCP 生态中已有多个项目，包括 `mcp-ssh-tool`、`openssh-mcp`、`ssh-mcp` 和 `mcp-sftp-orchestrator`。它们是有价值的参考，但本服务器因以下特定需求而独立实现：

- 每个 IP 注册一组明文凭据
- 为已注册 IP 打开多个持久化会话
- 支持临时（不保存凭据的）连接
- 会话 ID 返回给 AI agent
- 长命令支持游标增量读取输出
- 远端断连时显式标记 disconnected 状态
- 按 IP 进行 SFTP 上传/下载
- 支持宿主机、Docker 和 Kubernetes 三种命令模式

## 依赖

```bash
pip install paramiko
```

唯一的外部依赖是 `paramiko`（SSH 协议库），其余均为 Python 标准库。

## 运行

```powershell
python -m ssh_sftp_mcp
```

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
- 安装 paramiko 依赖
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
- 创建虚拟环境（可选）并安装 paramiko
- 创建状态存储目录
- 生成 MCP 配置文件
- 同时写入 Claude Code（`.mcp.json`）和 OpenCode（`opencode.json`）配置

## 工具列表

| 工具 | 说明 |
|------|------|
| `ssh_register(ip, port, username, password)` | 注册 SSH 服务器凭据 |
| `ssh_update(old_ip, ip, port, username, password)` | 修改已注册 SSH 服务器凭据，支持改 IP |
| `ssh_delete(ip)` | 删除已注册 SSH 服务器并关闭该 IP 的现有会话 |
| `ssh_connect(ip)` | 为已注册 IP 打开持久化会话，返回 session_id |
| `ssh_temp_connect(ip, port, username, password)` | 打开临时会话（不保存凭据） |
| `ssh_list_sessions(ip)` | 列出某 IP 的所有会话（含断连的） |
| `ssh_close(ip, session_id?)` | 关闭所有或指定会话 |
| `ssh_exec(session_id, command, mode?, container?, namespace?, pod?, shell?)` | 发送命令到持久化会话 |
| `ssh_read(session_id, command_id?, cursor?, limit?)` | 按游标增量读取命令输出 |
| `ssh_status(session_id, command_id?)` | 查询会话或命令状态 |
| `sftp_upload(ip, local_path, remote_path, session_id?)` | SFTP 上传文件 |
| `sftp_download(ip, remote_path, local_path, session_id?)` | SFTP 下载文件 |
| `ssh_list_servers()` | 列出已注册服务器（不含密码） |

## 注册表同步

`servers.json` 会在每次读取、连接、列出、上传或下载前检查文件更新时间。外部手动编辑 JSON 后，无需重启 MCP 服务器，下一次工具调用会自动加载新内容。

如果外部 JSON 暂时写坏，服务器会记录错误日志并保留内存中的上一次有效注册表，避免异常退出或清空可用配置。

## SFTP 权限回退

上传/下载默认优先使用 Paramiko SFTP。若 SFTP 子系统或目标路径触发权限异常，服务器会在同一个 SSH 登录用户下回退到远端 shell：

- 上传：通过 `cat > remote_path` 写入文件，并按需执行 `mkdir -p` 创建父目录
- 下载：通过 `cat remote_path` 读取内容并写入本地文件

回退结果会在返回结构中标记 `method: "shell"`；正常 SFTP 成功时为 `method: "sftp"`。

## 日志

服务器会向 stderr 打印详细日志，同时写入状态目录：

```text
H:\Documents\AgentAbility\.ssh_sftp_mcp\ssh_sftp_mcp.log
```

可通过环境变量 `SSH_SFTP_MCP_LOG_LEVEL` 调整级别，例如 `DEBUG`、`INFO`、`WARNING`。

## 长命令与输出读取

`ssh_exec` 将命令发送到持久化 shell 后立即返回 `command_id`。使用 `ssh_read` 配合返回的 `command_id` 和游标（cursor）增量读取输出。使用 `ssh_status` 检查命令是否仍在运行或已完成。

对于常规 `host`、`docker`、`k8s` 模式，命令会被包裹 POSIX shell 标记，以便检测命令完成和退出码。仅在远端 shell 不兼容 POSIX 时使用 `mode=raw`；原始命令直接发送，无法可靠推断完成状态。

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
