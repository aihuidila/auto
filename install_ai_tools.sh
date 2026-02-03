#!/bin/bash

# =============================================================================
# AI 工具安装脚本 (NVM 版)
# 包含: nvm + nodejs + opencode + claude-code + claude-code-router
# =============================================================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# --- 修改后的 Node.js (NVM) 检查逻辑 ---
check_nodejs() {
    print_info "检查 Node.js 环境 (优先使用 nvm)..."

    # 加载 NVM 环境（如果已安装但在当前会话未加载）
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

    if ! command -v nvm &> /dev/null; then
        print_warning "未检测到 nvm，准备安装 nvm..."
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
        
        # 再次加载环境
        export NVM_DIR="$HOME/.nvm"
        [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
        print_success "nvm 安装成功"
    fi

    # 检查 node 是否满足版本（>=18）
    if ! command -v node &> /dev/null || [ $(node -v | cut -d'v' -f2 | cut -d'.' -f1) -lt 18 ]; then
        print_info "正在通过 nvm 安装 Node.js v20 (LTS)..."
        nvm install 20
        nvm use 20
        nvm alias default 20
    fi
    
    print_success "当前 Node.js 版本: $(node -v)"
}

# 配置 opencode
setup_opencode() {
    print_info "配置 opencode..."
    mkdir -p ~/.config/opencode

    echo ""
    print_info "请输入 opencode 配置信息:"
    read -p "API Base URL [http://127.0.0.1:10011/v1]: " base_url
    base_url=${base_url:-http://127.0.0.1:10011/v1}

    read -p "API Key: " api_key
    if [ -z "$api_key" ]; then print_error "API Key 不能为空"; exit 1; fi

    read -p "模型名称 [GLM-4.5-Air-nvfp4]: " model_name
    model_name=${model_name:-GLM-4.5-Air-nvfp4}

    read -p "配置名称 [qujing]: " provider_name
    provider_name=${provider_name:-qujing}

    cat > ~/.config/opencode/opencode.json << EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "${provider_name}": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "${model_name}",
      "options": {
        "baseURL": "${base_url}",
        "apiKey": "${api_key}"
      },
      "models": {
        "${model_name}": {
          "name": "${model_name}"
        }
      }
    }
  }
}
EOF
    print_success "opencode 配置完成"
}

# 安装工具 (不再使用 sudo)
install_tools() {
    print_info "正在全局安装 AI 工具..."
    
    # 核心：使用 nvm 后不再需要 sudo npm
    npm install -g opencode-ai
    print_success "opencode-ai 安装成功"

    npm install -g @anthropic-ai/claude-code
    print_success "claude-code 安装成功"

    npm install -g @musistudio/claude-code-router
    print_success "claude-code-router 安装成功"
}

# 配置 claude-code-router
setup_claude_router() {
    print_info "配置 claude-code-router..."

    echo ""
    read -p "API Base URL [http://127.0.0.1:10011/v1/chat/completions]: " api_url
    api_url=${api_url:-http://127.0.0.1:10011/v1/chat/completions}

    read -p "API Key: " api_key
    read -p "模型名称 [GLM-4.5-Air-nvfp4]: " model_name
    model_name=${model_name:-GLM-4.5-Air-nvfp4}

    # NVM 环境下全局包的真实路径
    NODE_GLOBAL_ROOT=$(npm root -g)
    CONFIG_DIR="$NODE_GLOBAL_ROOT/@musistudio/claude-code-router/config"
    
    mkdir -p "$CONFIG_DIR"

    cat > "${CONFIG_DIR}/config.json" << EOF
{
  "LOG": false,
  "Providers": [
    {
      "name": "qujing",
      "api_base_url": "${api_url}",
      "api_key": "${api_key}",
      "models": ["${model_name}"]
    }
  ],
  "Router": {
    "default": "qujing,${model_name}"
  }
}
EOF
    print_success "Router 配置已写入: ${CONFIG_DIR}/config.json"
}

start_ccr() {
    print_info "启动 ccr 服务..."
    # 直接运行 ccr (nvm 已将其加入 PATH)
    ccr restart || print_warning "ccr 启动失败，请稍后手动尝试 'ccr restart'"
}

main() {
    print_info "开始安装流程..."
    
    check_nodejs
    
    read -p "确认安装 opencode, claude-code 和 router? [Y/n]: " confirm
    [[ ! ${confirm:-Y} =~ ^[Yy]$ ]] && exit 0

    install_tools
    setup_opencode
    setup_claude_router
    start_ccr

    echo -e "\n${GREEN}======================================"
    echo -e "所有工具已安装完成！"
    echo -e "1. 输入 'claude' 启动 Claude Code"
    echo -e "2. 输入 'opencode' 启动 OpenCode"
    echo -e "3. 输入 'ccr status' 检查路由状态"
    echo -e "注意：如果命令不生效，请执行：source ~/.bashrc"
    echo -e "======================================${NC}"
}

main "$@"
