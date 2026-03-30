#!/usr/bin/env python3
"""调试评分问题"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

import json
import sqlite3
from smart_scoring_v2 import SmartScoringEngine, score_session_with_rules

# 从队列获取一个任务
QUEUE_DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'task_queue.db')
conn = sqlite3.connect(QUEUE_DB_PATH)
cursor = conn.cursor()

cursor.execute('SELECT task_id, session_id, session_data FROM analysis_tasks WHERE task_id = 21')
row = cursor.fetchone()
conn.close()

if row:
    task_id, session_id, session_data_json = row
    print(f"Task ID: {task_id}")
    print(f"Session ID: {session_id}")
    print(f"Session data type: {type(session_data_json)}")
    print(f"Session data preview: {session_data_json[:200]}...")
    
    # 解析JSON
    try:
        session_data = json.loads(session_data_json)
        print(f"\nParsed session_data type: {type(session_data)}")
        print(f"session_data.keys(): {session_data.keys() if isinstance(session_data, dict) else 'N/A'}")
        
        # 尝试评分
        print("\n尝试评分...")
        result = score_session_with_rules(session_data)
        print(f"评分成功: {result}")
    except Exception as e:
        import traceback
        print(f"\n错误: {e}")
        traceback.print_exc()
else:
    print("Task not found")