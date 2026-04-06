#!/usr/bin/env python3
"""异步任务队列系统 - 客服分析任务队列

基于SQLite的轻量级队列，支持：
- 任务提交（pending）
- 任务处理（processing）
- 结果保存（completed/failed）
- 进度查询

作者: 小虾米
更新: 2026-03-18
"""

import json
import sqlite3
import time
import sys
import os
from datetime import datetime
from typing import List, Dict, Optional

sys.path.insert(0, os.path.dirname(__file__))

# 队列数据库路径
QUEUE_DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'task_queue.db')


def init_queue_tables():
    """初始化队列表"""
    conn = sqlite3.connect(QUEUE_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analysis_tasks (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT DEFAULT '',
            session_id TEXT NOT NULL,
            session_data TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            result TEXT,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            retry_count INTEGER DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_status ON analysis_tasks(status)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_batch_status ON analysis_tasks(batch_id, status)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_session ON analysis_tasks(session_id)
    ''')
    
    conn.commit()
    conn.close()


def submit_task(session_id: str, session_data: Dict, batch_id: str = '') -> int:
    """提交分析任务到队列
    
    Args:
        session_id: 会话ID
        session_data: 会话数据（含messages等）
        batch_id: 批次ID，用于区分不同分析任务
        
    Returns:
        任务ID
    """
    init_queue_tables()
    
    conn = sqlite3.connect(QUEUE_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO analysis_tasks (session_id, session_data, batch_id, status, created_at)
        VALUES (?, ?, ?, 'pending', ?)
    ''', (session_id, json.dumps(session_data, ensure_ascii=False), batch_id, datetime.now().isoformat()))
    
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return task_id


def get_pending_task() -> Optional[Dict]:
    """获取一个待处理任务
    
    Returns:
        任务字典，如果没有待处理任务返回None
    """
    conn = sqlite3.connect(QUEUE_DB_PATH)
    cursor = conn.cursor()
    
    # 获取最老的待处理任务
    cursor.execute('''
        SELECT task_id, session_id, session_data, retry_count
        FROM analysis_tasks
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT 1
    ''')
    
    row = cursor.fetchone()
    
    if row:
        task_id, session_id, session_data, retry_count = row
        
        # 标记为处理中
        cursor.execute('''
            UPDATE analysis_tasks
            SET status = 'processing', started_at = ?
            WHERE task_id = ?
        ''', (datetime.now().isoformat(), task_id))
        
        conn.commit()
        conn.close()
        
        return {
            'task_id': task_id,
            'session_id': session_id,
            'session_data': json.loads(session_data),
            'retry_count': retry_count
        }
    
    conn.close()
    return None


def complete_task(task_id: int, result: Dict):
    """标记任务完成
    
    Args:
        task_id: 任务ID
        result: 分析结果
    """
    conn = sqlite3.connect(QUEUE_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE analysis_tasks
        SET status = 'completed',
            result = ?,
            completed_at = ?
        WHERE task_id = ?
    ''', (json.dumps(result, ensure_ascii=False), datetime.now().isoformat(), task_id))
    
    conn.commit()
    conn.close()


def cancel_task(task_id: int, reason: str = 'merged'):
    """取消任务（用于会话合并时取消被合并的任务）
    
    Args:
        task_id: 任务ID
        reason: 取消原因，默认'merged'
    """
    conn = sqlite3.connect(QUEUE_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE analysis_tasks
        SET status = 'cancelled',
            result = ?,
            completed_at = ?
        WHERE task_id = ?
    ''', (json.dumps({'cancel_reason': reason}, ensure_ascii=False), 
          datetime.now().isoformat(), task_id))
    
    conn.commit()
    conn.close()


def fail_task(task_id: int, error: str):
    """标记任务失败
    
    Args:
        task_id: 任务ID
        error: 错误信息
    """
    conn = sqlite3.connect(QUEUE_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE analysis_tasks
        SET status = 'failed',
            error = ?,
            completed_at = ?,
            retry_count = retry_count + 1
        WHERE task_id = ?
    ''', (error, datetime.now().isoformat(), task_id))
    
    conn.commit()
    conn.close()


def get_queue_stats(batch_id: str = '') -> Dict:
    """获取队列统计信息
    
    Args:
        batch_id: 批次ID，为空时返回全局统计
        
    Returns:
        统计字典
    """
    init_queue_tables()
    
    conn = sqlite3.connect(QUEUE_DB_PATH)
    cursor = conn.cursor()
    
    if batch_id:
        cursor.execute('''
            SELECT status, COUNT(*)
            FROM analysis_tasks
            WHERE batch_id = ?
            GROUP BY status
        ''', (batch_id,))
    else:
        cursor.execute('''
            SELECT status, COUNT(*)
            FROM analysis_tasks
            GROUP BY status
        ''')
    
    stats = dict(cursor.fetchall())
    conn.close()
    
    return {
        'pending': stats.get('pending', 0),
        'processing': stats.get('processing', 0),
        'completed': stats.get('completed', 0),
        'failed': stats.get('failed', 0),
        'total': sum(stats.values())
    }


def retry_failed_tasks():
    """重试失败任务（最多3次）"""
    conn = sqlite3.connect(QUEUE_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE analysis_tasks
        SET status = 'pending',
            error = NULL,
            started_at = NULL
        WHERE status = 'failed'
        AND retry_count < 3
    ''')
    
    count = cursor.rowcount
    conn.commit()
    conn.close()
    
    return count


def clear_completed_tasks(days: int = 7):
    """清理已完成任务（默认保留7天）"""
    conn = sqlite3.connect(QUEUE_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        DELETE FROM analysis_tasks
        WHERE status = 'completed'
        AND completed_at < datetime('now', '-' || ? || ' days')
    ''', (days,))
    
    count = cursor.rowcount
    conn.commit()
    conn.close()
    
    return count


# ========== 便捷函数 ==========

def submit_sessions_batch(sessions: List[Dict], batch_id: str = '') -> List[int]:
    """批量提交会话到队列
    
    Args:
        sessions: 会话列表
        batch_id: 批次ID
        
    Returns:
        任务ID列表
    """
    task_ids = []
    for session in sessions:
        task_id = submit_task(session['session_id'], session, batch_id)
        task_ids.append(task_id)
    return task_ids


def get_task_detail(task_id: int) -> Optional[Dict]:
    """获取任务详情
    
    Args:
        task_id: 任务ID
        
    Returns:
        任务详情字典
    """
    conn = sqlite3.connect(QUEUE_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT task_id, session_id, status, result, error, 
               created_at, started_at, completed_at, retry_count
        FROM analysis_tasks
        WHERE task_id = ?
    ''', (task_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            'task_id': row[0],
            'session_id': row[1],
            'status': row[2],
            'result': json.loads(row[3]) if row[3] else None,
            'error': row[4],
            'created_at': row[5],
            'started_at': row[6],
            'completed_at': row[7],
            'retry_count': row[8]
        }
    return None


def get_pending_tasks(limit: int = 10) -> List[Dict]:
    """批量获取待处理任务
    
    Args:
        limit: 最大获取数量
        
    Returns:
        任务字典列表
    """
    conn = sqlite3.connect(QUEUE_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT task_id, session_id, session_data, retry_count
        FROM analysis_tasks
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT ?
    ''', (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    
    tasks = []
    for row in rows:
        task_id, session_id, session_data, retry_count = row
        tasks.append({
            'task_id': task_id,
            'session_id': session_id,
            'session_data': json.loads(session_data),
            'retry_count': retry_count
        })
    
    return tasks


def mark_processing(task_id: int):
    """标记任务为处理中状态
    
    Args:
        task_id: 任务ID
    """
    conn = sqlite3.connect(QUEUE_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE analysis_tasks
        SET status = 'processing', started_at = ?
        WHERE task_id = ?
    ''', (datetime.now().isoformat(), task_id))
    
    conn.commit()
    conn.close()


if __name__ == '__main__':
    # 测试
    init_queue_tables()
    stats = get_queue_stats()
    print(f"队列统计: {stats}")
