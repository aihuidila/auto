#!/bin/bash

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}开始配置 AI 开发环境...${NC}"

# 1. 检查并安装基础环境 (Node.js & npm)
if ! command -v npm &> /dev/null; then
    echo "未检测到 npm，正在安装 Node.js 基础包..."
    sudo apt-get update
    sudo apt-get install -y nodejs npm
fi

# 2. 强制升级 Node.js 到最新 LTS (解决 ReferenceError: File is not defined)
echo -e "${BLUE}正在通过 'n' 模块强制升级 Node.js 到最新稳定版...${NC}"
sudo npm install -g n
sudo n lts

# 关键步骤：刷新 shell 路径缓存
hash -r
export PATH="/usr/local/bin:$PATH"

# 检查升级结果
CURRENT_NODE=$(node -v)
echo -e "${GREEN}当前 Node.js 版本: $CURRENT_NODE${NC}"

# 3. 安装 opencode-ai
echo -e "${BLUE}安装 opencode-ai...${NC}"
sudo npm install -g opencode-ai

# 4. 写入 opencode 配置
mkdir -p ~/.config/opencode
cat <<EOF > ~/.config/opencode/opencode.json
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "qujing": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "GLM-4.5-Air-nvfp4",
      "options": {
        "baseURL": "http://127.0.0.1:10011/v1",
        "apiKey": "AMES_4b1baaefedfc43ed_57ab75dde1eb4712f571771916f82338"
      },
      "models": {
        "GLM-4.5-Air-nvfp4": { "name": "GLM-4.5-Air-nvfp4" }
      }
    }
  }
}
EOF

# 5. 安装 Claude 相关工具
echo -e "${BLUE}安装 claude-code 及路由工具...${NC}"
sudo npm install -g @musistudio/claude-code-router
sudo npm install -g @anthropic-ai/claude-code --registry=https://registry.npmmirror.com

# 6. 写入 ccr 配置 (针对最新 v24 兼容性)
CCR_CONFIG_DIR="$HOME/.claude-code-router"
mkdir -p "$CCR_CONFIG_DIR"
cat <<EOF > "$CCR_CONFIG_DIR/config.json"
{
  "LOG": false,
  "OPENAI_API_KEY": "",
  "OPENAI_BASE_URL": "",
  "OPENAI_MODEL": "",
  "Providers": [
    {
      "name": "qujing",
      "api_base_url": "http://127.0.0.1:10011/v1/chat/completions",
      "api_key": "AMES_084d773df6dbfbfe_609b6e0ff99fb23f893825a121ffc209",
      "models": [ "GLM-4.5-Air-nvfp4" ]
    }
  ],
  "Router": {
    "default": "qujing,GLM-4.5-Air-nvfp4"
  }
}
EOF

# 7. 重启并验证
echo -e "${BLUE}正在重启 ccr 服务...${NC}"
sudo ccr restart || ccr restart

echo -e "${GREEN}==========================================${NC}"
echo -e "${GREEN}恭喜！安装与配置已完成。${NC}"
echo -e "Node 版本: $(node -v)"
echo -e "npm 版本: $(npm -v)"
echo -e "你可以现在运行 ${BLUE}ccr status${NC} 检查路由状态。"
echo -e "${GREEN}==========================================${NC}"
