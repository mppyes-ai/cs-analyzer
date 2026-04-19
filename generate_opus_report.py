#!/usr/bin/env python3
"""生成 Opus 4.6 要求的测试数据文件"""

import sqlite3
import json
import os
from datetime import datetime

DB_PATH = "data/task_queue.db"
LOGS_PATH = "logs/worker.log"
OUTPUT_DIR = "docs"

def parse_worker_log():
    """解析 Worker 日志提取关键数据"""
    token_costs = []
    prompt_structs = []
    session_profiles = []
    batch_decisions = []
    
    if not os.path.exists(LOGS_PATH):
        print(f"⚠️ 日志文件不存在: {LOGS_PATH}")
        return token_costs, prompt_structs, session_profiles, batch_decisions
    
    with open(LOGS_PATH, 'r') as f:
        for line in f:
            line = line.strip()
            
            # 提取 TOKEN_COST
            if 'TOKEN_COST|' in line:
                try:
                    json_part = line.split('TOKEN_COST|')[1]
                    data = json.loads(json_part)
                    token_costs.append(data)
                except:
                    pass
            
            # 提取 PROMPT_STRUCT
            if 'PROMPT_STRUCT|' in line:
                try:
                    json_part = line.split('PROMPT_STRUCT|')[1]
                    data = json.loads(json_part)
                    prompt_structs.append(data)
                except:
                    pass
            
            # 提取 SESSION_PROFILE
            if 'SESSION_PROFILE|' in line:
                try:
                    parts = line.split('SESSION_PROFILE|')[1].split('|')
                    profile = {}
                    for part in parts:
                        if '=' in part:
                            k, v = part.split('=', 1)
                            profile[k] = v
                    session_profiles.append(profile)
                except:
                    pass
            
            # 提取 BATCH_DECISION
            if 'BATCH_DECISION|' in line:
                try:
                    parts = line.split('BATCH_DECISION|')[1].split('|')
                    decision = {}
                    for part in parts:
                        if '=' in part:
                            k, v = part.split('=', 1)
                            decision[k] = v
                    batch_decisions.append(decision)
                except:
                    pass
    
    return token_costs, prompt_structs, session_profiles, batch_decisions

def get_session_stats():
    """从数据库获取会话统计"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 获取所有任务
    cursor.execute("SELECT session_data FROM analysis_tasks WHERE status='completed'")
    rows = cursor.fetchall()
    conn.close()
    
    msg_counts = []
    char_counts = []
    
    for row in rows:
        try:
            session_data = json.loads(row[0]) if row[0] else {}
            messages = session_data.get('messages', [])
            msg_count = len(messages)
            char_count = sum(len(m.get('content', '')) for m in messages)
            
            msg_counts.append(msg_count)
            char_counts.append(char_count)
        except:
            pass
    
    return msg_counts, char_counts

def generate_files():
    """生成所有测试数据文件"""
    print("🔄 正在解析 Worker 日志...")
    token_costs, prompt_structs, session_profiles, batch_decisions = parse_worker_log()
    
    print("🔄 正在获取会话统计...")
    msg_counts, char_counts = get_session_stats()
    
    # 1. token_cost_detail.jsonl
    print("📝 生成 token_cost_detail.jsonl...")
    with open(f"{OUTPUT_DIR}/token_cost_detail.jsonl", 'w') as f:
        for cost in token_costs:
            f.write(json.dumps(cost, ensure_ascii=False) + '\n')
    
    # 2. prompt_struct_detail.jsonl
    print("📝 生成 prompt_struct_detail.jsonl...")
    with open(f"{OUTPUT_DIR}/prompt_struct_detail.jsonl", 'w') as f:
        for struct in prompt_structs:
            f.write(json.dumps(struct, ensure_ascii=False) + '\n')
    
    # 3. session_profiles.txt
    print("📝 生成 session_profiles.txt...")
    with open(f"{OUTPUT_DIR}/session_profiles.txt", 'w') as f:
        f.write("=== 会话概况统计 ===\n\n")
        f.write(f"总会话数: {len(session_profiles)}\n\n")
        for i, profile in enumerate(session_profiles, 1):
            f.write(f"[{i}] {profile}\n")
    
    # 4. batch_decisions.txt
    print("📝 生成 batch_decisions.txt...")
    with open(f"{OUTPUT_DIR}/batch_decisions.txt", 'w') as f:
        f.write("=== 批量大小决策记录 ===\n\n")
        for decision in batch_decisions:
            f.write(f"{decision}\n")
    
    # 5. session_size_distribution.txt
    print("📝 生成 session_size_distribution.txt...")
    with open(f"{OUTPUT_DIR}/session_size_distribution.txt", 'w') as f:
        f.write("=== 会话大小分布统计 ===\n\n")
        
        if msg_counts:
            f.write(f"消息数分布:\n")
            f.write(f"  - 最大: {max(msg_counts)}\n")
            f.write(f"  - 最小: {min(msg_counts)}\n")
            f.write(f"  - 平均: {sum(msg_counts)/len(msg_counts):.1f}\n\n")
        
        if char_counts:
            f.write(f"字符数分布:\n")
            f.write(f"  - 最大: {max(char_counts)}\n")
            f.write(f"  - 最小: {min(char_counts)}\n")
            f.write(f"  - 平均: {sum(char_counts)/len(char_counts):.1f}\n")
    
    # 6. test_env_snapshot.txt
    print("📝 生成 test_env_snapshot.txt...")
    with open(f"{OUTPUT_DIR}/test_env_snapshot.txt", 'w') as f:
        f.write("=== 环境配置快照 ===\n\n")
        f.write(f"生成时间: {datetime.now().isoformat()}\n\n")
        
        # 读取 .env 配置
        if os.path.exists('.env'):
            with open('.env', 'r') as env_f:
                f.write("环境变量配置:\n")
                for line in env_f:
                    if '=' in line and not line.startswith('#'):
                        f.write(f"  {line.strip()}\n")
    
    print(f"\n✅ 所有文件已生成到 {OUTPUT_DIR}/")
    
    # 打印统计摘要
    print("\n📊 数据摘要:")
    print(f"  - Token成本记录: {len(token_costs)} 条")
    print(f"  - Prompt结构记录: {len(prompt_structs)} 条")
    print(f"  - 会话概况记录: {len(session_profiles)} 条")
    print(f"  - 批量决策记录: {len(batch_decisions)} 条")

if __name__ == "__main__":
    generate_files()
