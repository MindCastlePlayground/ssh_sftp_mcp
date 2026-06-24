# ssh_sftp_mcp 一键安装脚本 (Windows PowerShell)
# 用法: .\setup.ps1

$ErrorActionPreference = "Stop"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  ssh_sftp_mcp MCP Server Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# --- 1. 检查 Python ---
Write-Host "[1/5] 检查 Python 环境..." -ForegroundColor Yellow
$python = $null
foreach ($cmd in @("python3", "python")) {
    try {
        $v = & $cmd --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $python = $cmd
            Write-Host "  [OK] 找到 $v" -ForegroundColor Green
            break
        }
    } catch {}
}

if (-not $python) {
    Write-Host "  [ERROR] 未找到 Python。请安装 Python 3.8+ 并加入 PATH。" -ForegroundColor Red
    Write-Host "  下载地址: https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}

# --- 2. 安装 paramiko ---
Write-Host "[2/5] 安装 paramiko 依赖..." -ForegroundColor Yellow
& $python -m pip install paramiko --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] paramiko 安装失败。" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] paramiko 已安装" -ForegroundColor Green

# --- 3. 确定路径 ---
$projectRoot = Split-Path -Parent $PSScriptRoot
$mcpCwd = $projectRoot

Write-Host "[3/5] 项目路径: $mcpCwd" -ForegroundColor Yellow

# --- 4. 创建状态目录 ---
$stateDir = Join-Path $mcpCwd ".ssh_sftp_mcp"
if (-not (Test-Path $stateDir)) {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    Write-Host "[4/5] 创建状态目录: $stateDir" -ForegroundColor Green
} else {
    Write-Host "[4/5] 状态目录已存在: $stateDir" -ForegroundColor Green
}

# --- 5. 生成 MCP 配置文件 ---
Write-Host "[5/5] 生成 MCP 配置文件..." -ForegroundColor Yellow

# 5a. Claude Code 配置 (.mcp.json)
$claudeConfig = @{
    mcpServers = @{
        "ssh-sftp-mcp" = @{
            command = $python
            args    = @("-m", "ssh_sftp_mcp")
            cwd     = $mcpCwd
        }
    }
}

$claudeConfigPath = Join-Path $projectRoot ".mcp.json"
$claudeConfig | ConvertTo-Json -Depth 4 | Set-Content -Path $claudeConfigPath -Encoding UTF8
Write-Host "  [OK] Claude Code 配置: $claudeConfigPath" -ForegroundColor Green

# 5b. OpenCode 配置 (opencode.json)
$opencodeConfig = @{
    '$schema' = "https://opencode.ai/config.json"
    mcp = @{
        "ssh-sftp-mcp" = @{
            type    = "local"
            command = @($python, "-m", "ssh_sftp_mcp")
            cwd     = $mcpCwd
            enabled = $true
        }
    }
}

$opencodeConfigPath = Join-Path $projectRoot "opencode.json"
$opencodeConfig | ConvertTo-Json -Depth 4 | Set-Content -Path $opencodeConfigPath -Encoding UTF8
Write-Host "  [OK] OpenCode 配置:    $opencodeConfigPath" -ForegroundColor Green

# --- 完成 ---
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  安装完成！" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Claude Code:  项目根目录已生成 .mcp.json，重启 Claude Code 即可使用。"
Write-Host "OpenCode:     项目根目录已生成 opencode.json，重启 OpenCode 即可使用。"
Write-Host ""
Write-Host "手动测试:     cd $mcpCwd; $python -m ssh_sftp_mcp"
Write-Host "配置文件:     $claudeConfigPath"
Write-Host "状态目录:     $stateDir"
Write-Host ""

# 可选：立即测试模块能否导入
Write-Host "验证模块导入..." -ForegroundColor Yellow
Push-Location $mcpCwd
try {
    & $python -c "from ssh_sftp_mcp.constants import Meta; print(f'  [OK] {Meta.name} v{Meta.version} 可正常导入')"
} catch {
    Write-Host "  [WARN] 模块导入失败，请检查 cwd 路径是否正确: $_" -ForegroundColor Yellow
}
Pop-Location
