#!/bin/zsh
set -e

workspace_dir="${0:A:h}"
if [ -f "/Users/jun/.zshrc" ]; then
  source "/Users/jun/.zshrc"
fi

python_bin="$workspace_dir/stock_investment_system/.venv313/bin/python"
server_file="$workspace_dir/deep-stock-research/ui_server.py"

if [ ! -x "$python_bin" ]; then
  echo "未找到 Python 3.13 项目环境：$python_bin"
  read -k 1 "?按任意键关闭…"
  exit 1
fi

exec "$python_bin" "$server_file" --open-browser
