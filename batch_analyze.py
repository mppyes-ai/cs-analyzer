#!/usr/bin/env python3
"""简化版批量分析脚本"""
import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

import sqlite3
from datetime import datetime
from smart_scoring_v2 import score_session_with_rules

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'cs_analyzer_new.db')

def get_pending_sessions():
    """获取待分析的会话"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 获取没有评分的会话
    cursor.execute('''
        SELECT session_id, messages 
        FROM sessions 
        WHERE professionalism_score IS NULL
        LIMIT 10
    ''')
    
    sessions = []
    for row in cursor.fetchall():
        session_id, messages_json = row
        try:
            messages = json.loads(messages_json)
            sessions.append({
                'session_id': session_id,
                'messages': messages
            })
        except:
            pass
    
    conn.close()
    return sessions

def analyze_session(session):
    """分析单个会话"""
    session_id = session['session_id']
    messages = session['messages']
    
    print(f"\n📋 分析会话: {session_id}")
    print(f"   消息数: {len(messages)}")
    
    # 准备会话数据
    session_data = {
        'session_id': session_id,
        'messages': messages,
        'staff_name': '未知客服'
    }
    
    try:
        result = score_session_with_rules(session_data)
        
        if result and 'dimension_scores' in result:
            ds = result['dimension_scores']
            prof = ds.get('professionalism', {}).get('score', 0)
            std = ds.get('standardization', {}).get('score', 0)
            pol = ds.get('policy_execution', {}).get('score', 0)
            conv = ds.get('conversion', {}).get('score', 0)
            total = prof + std + pol + conv
            
            print(f"   ✅ 评分完成: 专业{prof} 标准{std} 政策{pol} 转化{conv} = {total}/20")
            
            # 保存结果
            save_result(session_id, result, prof, std, pol, conv, total)
            return True
        else:
            print(f"   ❌ 评分失败: 无结果")
            return False
            
    except Exception as e:
        print(f"   ❌ 评分失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def save_result(session_id, result, prof, std, pol, conv, total):
    """保存评分结果"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 提取分析文本
    summary = result.get('summary', {})
    strengths = summary.get('strengths', [])
    issues = summary.get('issues', [])
    suggestions = summary.get('suggestions', [])
    
    cursor.execute('''
        UPDATE sessions 
        SET professionalism_score = ?,
            standardization_score = ?,
            policy_execution_score = ?,
            conversion_score = ?,
            total_score = ?,
            analysis_json = ?,
            strengths = ?,
            issues = ?,
            suggestions = ?
        WHERE session_id = ?
    ''', (prof, std, pol, conv, total,
          json.dumps(result, ensure_ascii=False),
          json.dumps(strengths, ensure_ascii=False),
          json.dumps(issues, ensure_ascii=False),
          json.dumps(suggestions, ensure_ascii=False),
          session_id))
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    print("🚀 开始批量分析...")
    
    sessions = get_pending_sessions()
    print(f"📊 找到 {len(sessions)} 个待分析会话")
    
    success_count = 0
    for i, session in enumerate(sessions, 1):
        print(f"\n[{i}/{len(sessions)}]", end="")
        if analyze_session(session):
            success_count += 1
    
    print(f"\n\n✅ 分析完成: {success_count}/{len(sessions)} 个会话成功")