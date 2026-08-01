#!/usr/bin/env bash
set -euo pipefail

SYSTEM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="$(cd "$SYSTEM_DIR/.." && pwd)"
MODEL="${1:-all}"
MAX_WATCHLIST="${2:-10}"
OUTPUT_DIR="$SYSTEM_DIR/outputs"
TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
OUTPUT_FILE="$OUTPUT_DIR/stock_watchlist_${MODEL}_${TIMESTAMP}.csv"
TEMP_FILE="$OUTPUT_FILE.tmp"
ERROR_FILE="$OUTPUT_DIR/stock_watchlist_${MODEL}_${TIMESTAMP}_错误说明.txt"

mkdir -p "$OUTPUT_DIR"

# Finder launches an app without guaranteeing the project root as cwd.
# Run from the directory that contains the stock_investment_system package so
# Python can resolve `stock_investment_system.run_models` reliably.
cd "$PROJECT_DIR"

if "$SYSTEM_DIR/env.sh" -m stock_investment_system.run_models \
    --model "$MODEL" \
    --max-watchlist "$MAX_WATCHLIST" \
    --live-universe \
    --refresh-quotes \
    --format csv > "$TEMP_FILE" 2> "$ERROR_FILE"; then
  if [ ! -s "$TEMP_FILE" ]; then
    {
      echo "股票投资系统没有生成结果。"
      echo "请确认 Futu OpenD 已经打开并登录。"
      echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
    } > "$ERROR_FILE"
    rm -f "$TEMP_FILE"
    printf '%s\n' "$ERROR_FILE"
    exit 0
  fi
  mv "$TEMP_FILE" "$OUTPUT_FILE"
  if [ -s "$ERROR_FILE" ]; then
    mv "$ERROR_FILE" "${OUTPUT_FILE%.csv}_运行说明.txt"
  else
    rm -f "$ERROR_FILE"
  fi
else
  {
    echo "股票投资系统运行失败。"
    echo "请确认 Futu OpenD 已经打开并登录，然后重新运行。"
    echo
    echo "错误详情："
    cat "$ERROR_FILE"
  } > "$ERROR_FILE.new"
  mv "$ERROR_FILE.new" "$ERROR_FILE"
  rm -f "$TEMP_FILE"
  printf '%s\n' "$ERROR_FILE"
  exit 0
fi

printf '%s\n' "$OUTPUT_FILE"
