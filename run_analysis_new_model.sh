#!/bin/bash
# 使用lmstudio-community/qwen3.6-35b-a3b（关闭思考功能）分析50通会话

export LLM_MODE=local
export LOCAL_MODEL=lmstudio-community/qwen3.6-35b-a3b
export LOCAL_MODEL_URL=http://localhost:1234/v1

cd /Users/jinlu/.openclaw/workspace/skills/cs-analyzer

# 重置任务
python3.14 -c "
import sqlite3
from log_parser import parse_log_file
from task_queue import submit_task

conn = sqlite3.connect('data/task_queue.db')
cursor = conn.cursor()
cursor.execute('DELETE FROM analysis_tasks')
conn.commit()

LOG_FILE = '/Users/jinlu/Desktop/小虾米专属文档/客服聊天记录/客服聊天记录(50).log'
sessions = parse_log_file(LOG_FILE)

added = 0
for session in sessions:
    try:
        task_id = submit_task(session['session_id'], session)
        if task_id:
            added += 1
    except Exception as e:
        print(f'⚠️ 失败: {e}')

print(f'✅ 已创建 {added} 个任务')
conn.close()
"

# 创建日志文件
LOG_FILE="logs/worker_$(date +%Y%m%d_%H%M%S).log"

echo "🚀 使用 lmstudio-community/qwen3.6-35b-a3b 分析50通会话"
echo "===================="
echo "开始时间: $(date)"
echo "日志文件: $LOG_FILE"
echo ""

# 运行Worker
python3.14 worker.py --async-batch --once --max-batch-size 50 --score-batch-size 5 2>&1 | tee "$LOG_FILE"

echo ""
echo "完成时间: $(date)"
echo "日志保存至: $LOG_FILE"