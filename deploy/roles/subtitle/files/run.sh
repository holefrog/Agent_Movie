#!/bin/bash
# Agent_Movie 启动脚本

cd "$(dirname "$0")"

# 确保 venv 存在并激活
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
else
    echo "❌ 找不到虚拟环境 (venv/bin/activate)，请确认部署已成功完成。"
    exit 1
fi

echo "🚀 启动 Agent_Movie Web 服务..."
exec python app.py
