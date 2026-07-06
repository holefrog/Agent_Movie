#!/bin/bash
# 自动部署脚本
# 快速执行 Ansible playbook，将 Translator-Pilot 部署到本地环境
set -e

# 确保在 ansible 目录下执行
cd "$(dirname "$0")"

echo "=================================================="
echo "🚀 开始部署 Agent_Movie"
echo "=================================================="

# 运行 ansible-playbook
ansible-playbook -i hosts playbook.yml

echo "=================================================="
echo "✅ 部署完成！"
echo "你可以前往 ~/Programs/Agent_Movie/ 目录使用程序。"
echo "=================================================="
