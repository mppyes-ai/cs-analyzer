#!/bin/bash
# CS-Analyzer 安全入口 - 默认后台模式
# 禁止前台模式自动推断

LOG_FILE="$1"

if [ -z "$LOG_FILE" ]; then
    echo "用法: ./analyze.sh <日志文件路径>"
    exit 1
fi

# 强制后台模式，不接受 --foreground 参数
python3.14 "$(dirname "$0")/cs_analyzer_batch.py" "$LOG_FILE"
