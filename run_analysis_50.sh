#!/bin/bash
# 50通会话完整分析 - 使用项目日志目录

export LLM_MODE=local
export LOCAL_MODEL=qwen3.6-35b-a3b@4bit
export LOCAL_MODEL_URL=http://localhost:1234/v1

cd /Users/jinlu/.openclaw/workspace/skills/cs-analyzer

# 创建日志文件名
LOG_FILE="logs/worker_$(date +%Y%m%d_%H%M%S).log"

echo "🚀 50通会话完整分析"
echo "===================="
echo "开始时间: $(date)"
echo "日志文件: $LOG_FILE"
echo "模型: qwen3.6-35b-a3b@4bit"
echo "Embedding: text-embedding-qwen3-embedding-4b"
echo ""

# 运行Worker
python3.14 worker.py --async-batch --once --max-batch-size 50 --score-batch-size 5 2>&1 | tee "$LOG_FILE"

echo ""
echo "分析完成时间: $(date)"
echo "日志保存至: $LOG_FILE"