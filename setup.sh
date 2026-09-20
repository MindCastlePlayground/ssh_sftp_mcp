#!/usr/bin/env bash
# ssh_sftp_mcp 一键安装脚本 (Linux / macOS)
# 用法: chmod +x setup.sh && ./setup.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}============================================${NC}"
echo -e "${CYAN}  ssh_sftp_mcp MCP Server Setup${NC}"
echo -e "${CYAN}============================================${NC}"
echo ""

# --- 1. 检查 Python ---
echo -e "${YELLOW}[1/5] 检查 Python 环境...${NC}"
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        v=$("$cmd" --version 2>&1)
        if [ $? -eq 0 ]; then
            PYTHON="$cmd"
            echo -e "  ${GREEN}[OK] 找到 $v${NC}"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "  ${RED}[ERROR] 未找到 Python。请安装 Python 3.10+。${NC}"
    exit 1
fi

# --- 2. 安装 netmiko 与 asyncssh ---
echo -e "${YELLOW}[2/5] 安装 netmiko 与 asyncssh 依赖...${NC}"
if "$PYTHON" -m pip install netmiko "asyncssh>=2.24,<3" --quiet 2>/dev/null; then
    echo -e "  ${GREEN}[OK] netmiko 与 asyncssh 已安装${NC}"
else
    # 如果用户环境需要 --user
    echo -e "  ${YELLOW}尝试用户级安装...${NC}"
    "$PYTHON" -m pip install --user netmiko "asyncssh>=2.24,<3" --quiet
    echo -e "  ${GREEN}[OK] netmiko 与 asyncssh 已安装 (user)${NC}"
fi

# --- 3. 确定路径 ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
MCP_CWD="$PROJECT_ROOT"

echo -e "${YELLOW}[3/5] 项目路径: $MCP_CWD${NC}"

# --- 4. 创建状态目录 ---
STATE_DIR="$MCP_CWD/.ssh_sftp_mcp"
if [ ! -d "$STATE_DIR" ]; then
    mkdir -p "$STATE_DIR"
    echo -e "${GREEN}[4/5] 创建状态目录: $STATE_DIR${NC}"
else
    echo -e "${GREEN}[4/5] 状态目录已存在: $STATE_DIR${NC}"
fi

# --- 5. 生成 MCP 配置文件 ---
echo -e "${YELLOW}[5/5] 生成 MCP 配置文件...${NC}"

# 5a. Claude Code 配置 (.mcp.json)
CLAUDE_CONFIG="$PROJECT_ROOT/.mcp.json"
cat > "$CLAUDE_CONFIG" << EOF
{
  "mcpServers": {
    "ssh-sftp-mcp": {
      "command": "$PYTHON",
      "args": ["-m", "ssh_sftp_mcp"],
      "cwd": "$MCP_CWD"
    }
  }
}
EOF
echo -e "  ${GREEN}[OK] Claude Code 配置: $CLAUDE_CONFIG${NC}"

# 5b. OpenCode 配置 (opencode.json)
OPENCODE_CONFIG="$PROJECT_ROOT/opencode.json"
cat > "$OPENCODE_CONFIG" << EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "mcp": {
    "ssh-sftp-mcp": {
      "type": "local",
      "command": ["$PYTHON", "-m", "ssh_sftp_mcp"],
      "cwd": "$MCP_CWD",
      "enabled": true
    }
  }
}
EOF
echo -e "  ${GREEN}[OK] OpenCode 配置:    $OPENCODE_CONFIG${NC}"

# --- 完成 ---
echo ""
echo -e "${CYAN}============================================${NC}"
echo -e "${GREEN}  安装完成！${NC}"
echo -e "${CYAN}============================================${NC}"
echo ""
echo "Claude Code:  项目根目录已生成 .mcp.json，重启 Claude Code 即可使用。"
echo "OpenCode:     项目根目录已生成 opencode.json，重启 OpenCode 即可使用。"
echo ""
echo "手动测试:     cd $MCP_CWD && $PYTHON -m ssh_sftp_mcp"
echo "配置目录:     $PROJECT_ROOT"
echo "状态目录:     $STATE_DIR"
echo ""

# 可选：验证模块导入
echo -e "${YELLOW}验证模块导入...${NC}"
cd "$MCP_CWD"
if "$PYTHON" -c "from ssh_sftp_mcp.constants import Meta; print(f'  [OK] {Meta.name} v{Meta.version} 可正常导入')" 2>/dev/null; then
    true
else
    echo -e "  ${YELLOW}[WARN] 模块导入失败，请检查 cwd 路径是否正确${NC}"
fi
