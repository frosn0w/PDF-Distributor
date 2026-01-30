#!/bin/bash
cd "$(dirname "$0")"

# 如果存在 .env 文件，则加载它 (导出为环境变量)
if [ -f .env ]; then
    export $(cat .env | xargs)
fi

echo "🚀 正在启动 pdf-distributor..."
# uv 会自动继承当前的 export 环境变量
uv run --with streamlit --with pymupdf --with requests streamlit run app.py