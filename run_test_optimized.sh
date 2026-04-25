#!/bin/bash
# 使用优化后的配置重新测试50通会话

export LLM_MODE=local
export LOCAL_MODEL=lmstudio-community/qwen3.6-35b-a3b
export LOCAL_MODEL_URL=http://localhost:1234/v1
export LOCAL_TEMPERATURE=0.1

cd /Users/jinlu/.openclaw/workspace/skills/cs-analyzer

LOG_FILE="logs/worker_test_$(date +%Y%m%d_%H%M%S).log"

echo "🚀 重新测试50通会话（优化配置）"
echo "===================="
echo "模型: lmstudio-community/qwen3.6-35b-a3b"
echo "temperature: 0.1"
echo "系统提示词: 已加强JSON格式要求"
echo ""

python3.14 worker.py --async-batch --once --max-batch-size 50 --score-batch-size 5 2>&1 | tee "$LOG_FILE"

echo ""
echo "完成！日志: $LOG_FILE"