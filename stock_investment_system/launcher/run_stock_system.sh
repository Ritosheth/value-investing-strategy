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
DEEP_RESEARCH_AUTO="${DEEP_RESEARCH_AUTO:-1}"
DEEP_RESEARCH_HORIZON="${DEEP_RESEARCH_HORIZON:-MEDIUM}"
DEEP_RESEARCH_MAX_STOCKS="${DEEP_RESEARCH_MAX_STOCKS:-0}"
DEEP_RESEARCH_OPEN_REPORT="${DEEP_RESEARCH_OPEN_REPORT:-1}"
DEEP_RESEARCH_BACKGROUND="${DEEP_RESEARCH_BACKGROUND:-1}"
DEEP_RESEARCH_LOG="$OUTPUT_DIR/stock_watchlist_${MODEL}_${TIMESTAMP}_深度研究运行.log"

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

# Return the original CSV immediately in the default background mode so the
# Finder app remains responsive. Set DEEP_RESEARCH_AUTO=0 to disable research,
# or DEEP_RESEARCH_BACKGROUND=0 to wait for research (useful for diagnostics).
if [ "$DEEP_RESEARCH_AUTO" != "0" ]; then
  if [ "$DEEP_RESEARCH_BACKGROUND" != "0" ]; then
    if [ "$DEEP_RESEARCH_OPEN_REPORT" != "0" ]; then
      "$SYSTEM_DIR/env.sh" "$SYSTEM_DIR/launch_deep_research.py" \
        --watchlist-csv "$OUTPUT_FILE" \
        --timestamp "$TIMESTAMP" \
        --horizon "$DEEP_RESEARCH_HORIZON" \
        --max-stocks "$DEEP_RESEARCH_MAX_STOCKS" \
        --log "$DEEP_RESEARCH_LOG" \
        --open-report >> "$DEEP_RESEARCH_LOG" 2>&1 || true
    else
      "$SYSTEM_DIR/env.sh" "$SYSTEM_DIR/launch_deep_research.py" \
        --watchlist-csv "$OUTPUT_FILE" \
        --timestamp "$TIMESTAMP" \
        --horizon "$DEEP_RESEARCH_HORIZON" \
        --max-stocks "$DEEP_RESEARCH_MAX_STOCKS" \
        --log "$DEEP_RESEARCH_LOG" >> "$DEEP_RESEARCH_LOG" 2>&1 || true
    fi
  else
    if [ "$DEEP_RESEARCH_OPEN_REPORT" != "0" ]; then
      "$SYSTEM_DIR/env.sh" "$SYSTEM_DIR/deep_research_pipeline.py" \
        --watchlist-csv "$OUTPUT_FILE" \
        --timestamp "$TIMESTAMP" \
        --horizon "$DEEP_RESEARCH_HORIZON" \
        --max-stocks "$DEEP_RESEARCH_MAX_STOCKS" \
        --open-report > "$DEEP_RESEARCH_LOG" 2>&1 || true
    else
      "$SYSTEM_DIR/env.sh" "$SYSTEM_DIR/deep_research_pipeline.py" \
        --watchlist-csv "$OUTPUT_FILE" \
        --timestamp "$TIMESTAMP" \
        --horizon "$DEEP_RESEARCH_HORIZON" \
        --max-stocks "$DEEP_RESEARCH_MAX_STOCKS" > "$DEEP_RESEARCH_LOG" 2>&1 || true
    fi
  fi
fi

printf '%s\n' "$OUTPUT_FILE"
