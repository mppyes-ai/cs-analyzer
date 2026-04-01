#!/usr/bin/env python3
"""串行模式测试5条失败数据"""

import os
import sys
import json
import sqlite3
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from smart_scoring_v2 import SmartScoringEngine

# 连接数据库读取5条失败数据
conn = sqlite3.connect('data/task_queue.db')
cursor = conn.cursor()
cursor.execute("SELECT session_id, session_data FROM analysis_tasks WHERE status='failed' LIMIT 5")
tasks = cursor.fetchall()
conn.close()

api_key = os.getenv('MOONSHOT_API_KEY')
scorer = SmartScoringEngine(api_key=api_key)

results = []

for i, (session_id, session_data_json) in enumerate(tasks, 1):
    print(f"\n{'='*60}")
    print(f"串行模式测试 - 第 {i}/5 条")
    print(f"Session ID: {session_id}")
    print('='*60)
    
    try:
        session_data = json.loads(session_data_json)
        result = scorer.score_session(session_data)
        total = result.get('summary', {}).get('total_score', 'N/A')
        risk = result.get('summary', {}).get('risk_level', 'N/A')
        print(f"✅ 成功! 总分: {total}, 风险: {risk}")
        results.append((session_id, 'success', total, risk))
    except Exception as e:
        print(f"❌ 失败: {e}")
        results.append((session_id, 'failed', str(e), ''))

print(f"\n{'='*60}")
print("测试总结")
print('='*60)
for sid, status, score, risk in results:
    print(f"{sid}: {status} | {score} | {risk}")
