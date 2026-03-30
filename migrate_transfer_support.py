#!/usr/bin/env python3
"""
数据库迁移脚本：添加转接会话支持字段

新增字段：
- is_transfer: 是否转接会话
- transfer_from: 从哪个会话转接
- transfer_to: 转接到了哪个会话
- transfer_reason: 转接原因
- related_sessions: JSON格式关联会话列表
- transfer_quality: JSON格式转接质量评估
"""

import sqlite3
import os
import json

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "cs_analyzer_new.db")

def migrate():
    """执行数据库迁移"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("🔄 开始迁移数据库...")
    
    # 检查字段是否存在
    cursor.execute("PRAGMA table_info(sessions)")
    columns = [col[1] for col in cursor.fetchall()]
    
    # 新增转接相关字段
    new_columns = [
        ("is_transfer", "INTEGER DEFAULT 0"),
        ("transfer_from", "TEXT"),
        ("transfer_to", "TEXT"),
        ("transfer_reason", "TEXT"),
        ("related_sessions", "TEXT"),  # JSON格式
        ("transfer_quality", "TEXT"),  # JSON格式
        ("transfer_time", "TEXT"),     # 转接发生时间
    ]
    
    for col_name, col_type in new_columns:
        if col_name not in columns:
            cursor.execute(f"ALTER TABLE sessions ADD COLUMN {col_name} {col_type}")
            print(f"  ✅ 添加字段: {col_name}")
        else:
            print(f"  ℹ️ 字段已存在: {col_name}")
    
    # 创建转接质量评估表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transfer_quality_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            transfer_from_session TEXT,
            
            -- 转接及时性（秒）
            transfer_response_time REAL,
            
            -- 信息完整性评分（1-5）
            info_completeness_score INTEGER,
            
            -- 用户等待时间（秒）
            user_wait_time REAL,
            
            -- 重复询问次数
            repeat_question_count INTEGER DEFAULT 0,
            
            -- 转接说明（客服是否说明转接原因）
            has_transfer_explanation INTEGER DEFAULT 0,
            
            -- 总体转接质量评分（1-5）
            overall_transfer_score INTEGER,
            
            -- 备注
            notes TEXT,
            
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)
    print("  ✅ 创建表: transfer_quality_metrics")
    
    # 创建索引
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_transfer_from 
        ON sessions(transfer_from)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_user_id 
        ON sessions(user_id)
    """)
    print("  ✅ 创建索引")
    
    conn.commit()
    conn.close()
    print("\n✅ 数据库迁移完成！")

if __name__ == "__main__":
    migrate()
