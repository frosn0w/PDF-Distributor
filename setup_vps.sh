#!/bin/bash
set -e

PROJECT_NAME="PDF-Distributor"
REPO_URL="https://github.com/你的用户名/$PROJECT_NAME.git"

echo "🌐 正在一键部署 $PROJECT_NAME..."

# 安装 Docker
if ! command -v docker &> /dev/null; then
    echo "请确认已经安装适合当前服务器的docker"
fi

# 同步代码
if [ -d "$PROJECT_NAME" ]; then
    cd "$PROJECT_NAME" && git pull
else
    git clone "$REPO_URL" && cd "$PROJECT_NAME"
fi

# 检查 .env 是否存在，如果不存在则引导用户手动创建
if [ ! -f .env ]; then
    echo "⚠️ 检测到缺失 .env 配置文件，请手动输入以下信息："
    read -p "请输入百度 App Key: " ak
    read -p "请输入百度 Secret Key: " sk
    echo "BAIDU_AK=$ak" > .env
    echo "BAIDU_SK=$sk" >> .env
    echo "APP_FOLDER=转存分享助手" >> .env
    echo "FILE_PREFIX=BLS" >> .env
    echo "✅ .env 文件已生成。"
fi
# 启动
docker compose up -d --build

echo "✅ 部署成功！"
echo "🌐 访问地址: http://$(curl -s ifconfig.me):8501"