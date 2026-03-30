#!/usr/bin/env python3
"""批量分析客服日志文件"""

import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from log_parser import parse_log_file
from intent_classifier_v3 import classify_intent
from db_utils import get_connection
from task_queue import submit_task

def save_session(conn, session):
    """保存会话到数据库"""
    cursor = conn.cursor()
    
    # 序列化消息
    messages_json = json.dumps(session['messages'], ensure_ascii=False)
    
    cursor.execute('''
        INSERT OR REPLACE INTO sessions 
        (session_id, user_id, staff_name, messages, summary, 
         session_count, start_time, end_time, is_transfer, 
         transfer_from, transfer_to, transfer_reason, related_sessions)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        session['session_id'],
        session.get('user_id', ''),
        session.get('staff_name', ''),
        messages_json,
        session.get('summary', ''),
        session.get('session_count', 1),
        session.get('start_time', ''),
        session.get('end_time', ''),
        1 if session.get('is_transfer') else 0,
        session.get('transfer_from', ''),
        session.get('transfer_to', ''),
        session.get('transfer_reason', ''),
        json.dumps(session.get('related_sessions', []), ensure_ascii=False)
    ))
    conn.commit()

def analyze_log_file(log_file_path: str):
    """分析日志文件并入库"""
    print(f"📂 解析日志文件: {log_file_path}")
    
    # 1. 解析日志
    sessions = parse_log_file(log_file_path)
    print(f"✅ 解析完成: {len(sessions)} 个会话")
    
    if not sessions:
        print("⚠️ 未解析到任何会话")
        return
    
    # 2. 保存会话并提交分析任务
    conn = get_connection()
    analyzed_count = 0
    
    for i, session in enumerate(sessions, 1):
        session_id = session['session_id']
        print(f"\n[{i}/{len(sessions)}] 处理会话: {session_id}")
        
        # 先保存会话到数据库
        try:
            save_session(conn, session)
            print(f"   会话已保存")
        except Exception as e:
            print(f"   会话保存失败: {e}")
            continue
        
        # 意图分类
        messages = [{'role': m['role'], 'content': m['content']} for m in session['messages']]
        intent_result = classify_intent(messages)
        print(f"   意图: {intent_result['scene']}/{intent_result['sub_scene']} (来源: {intent_result['source']})")
        
        # 提交评分任务到队列
        try:
            task_id = submit_task(session_id=session_id, session_data=session)
            print(f"   任务提交: {task_id}")
            analyzed_count += 1
        except Exception as e:
            print(f"   任务提交失败: {e}")
    
    conn.close()
    print(f"\n✅ 会话保存完成: {analyzed_count}/{len(sessions)} 个会话已提交评分任务")
    
    # 3. 运行Worker处理评分任务
    if analyzed_count > 0:
        print("\n🚀 启动Worker处理评分任务...")
        os.system(f"cd {os.path.dirname(os.path.abspath(__file__))} && python3 worker.py --limit {analyzed_count}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 analyze_log.py <log_file_path>")
        sys.exit(1)
    
    log_file = sys.argv[1]
    if not os.path.exists(log_file):
        print(f"❌ 文件不存在: {log_file}")
        sys.exit(1)
    
    analyze_log_file(log_file)